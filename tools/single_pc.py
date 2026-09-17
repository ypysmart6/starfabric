#!/usr/bin/env python3
"""Run the single-host acceptance suite with isolated, current-run evidence."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "docs/single-pc-suite.json"
EXCLUDED = {".git", ".cache", ".agents", ".codex", ".ruff_cache", "__pycache__",
            "reports", "artifacts", "target", "build", "bin", "node_modules"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_digest(root: Path = ROOT) -> str:
    """Include code, configs, manifests and generated API sources, not outputs."""
    digest = hashlib.sha256()
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name not in EXCLUDED and not name.startswith("clab-"))
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix in {".pyc", ".pcap", ".log"}:
                continue
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update((os.readlink(path) if path.is_symlink() else sha256(path)).encode() + b"\0")
    return digest.hexdigest()


def local_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f"not a relative artifact path: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"artifact escapes root: {relative!r}")
    return path


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_suite(path: Path = SUITE, root: Path = ROOT) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != 1 or not suite.get("stages"):
        raise ValueError("suite must have schema_version 1 and nonempty stages")
    seen: set[str] = set()
    for stage in suite["stages"]:
        identifier = stage.get("id")
        if not isinstance(identifier, str) or not identifier.replace("-", "").isalnum() or identifier in seen:
            raise ValueError(f"invalid or duplicate stage: {identifier!r}")
        command = stage.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(v, str) or not v for v in command):
            raise ValueError(f"{identifier}: command must be an argv array")
        if any(dep not in seen for dep in stage.get("depends_on", [])):
            raise ValueError(f"{identifier}: dependencies must precede stage")
        if not isinstance(stage.get("timeout_seconds"), int) or stage["timeout_seconds"] <= 0:
            raise ValueError(f"{identifier}: positive timeout_seconds required")
        for relative in stage.get("reports", []):
            local_path(root, relative)
        for relative, prefix in stage.get("artifact_reports", {}).items():
            if relative not in stage.get("reports", []):
                raise ValueError(f"{identifier}: artifact report has no producer: {relative}")
            local_path(root, prefix)
        if stage.get("profile") not in {"core", "full"}:
            raise ValueError(f"{identifier}: invalid profile")
        seen.add(identifier)
    closure = json.loads((root / "docs/single-pc-closure.json").read_text())
    required = {gate["report"] for entry in closure["entries"] for gate in entry.get("gates", [])}
    produced = {report for stage in suite["stages"] for report in stage.get("reports", [])}
    if required - produced:
        raise ValueError(f"closure reports without executable producers: {sorted(required - produced)}")
    return suite


def file_stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        status = path.stat()
        return status.st_ino, status.st_mtime_ns, status.st_ctime_ns
    except FileNotFoundError:
        return None


def stop_process(process: subprocess.Popen[Any]) -> None:
    # Let existing lab EXIT traps clean up their own containers first.
    for sig, seconds in ((signal.SIGINT, 20), (signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, sig)
            process.wait(timeout=seconds)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue


def execute_stage(stage: dict[str, Any], run_dir: Path, root: Path = ROOT) -> dict[str, Any]:
    before = {relative: file_stamp(local_path(root, relative)) for relative in stage.get("reports", [])}
    started_ns = time.time_ns()
    log = run_dir / "logs" / (stage["id"] + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"id": stage["id"], "command": stage["command"],
                              "started_at": now(), "status": "failed", "reports": {}, "errors": [],
                              "artifact_reports": stage.get("artifact_reports", {}), "artifacts": {}}
    environment = os.environ.copy()
    environment["PATH"] = str(root / "bin") + os.pathsep + environment.get("PATH", "")
    environment.setdefault("GOCACHE", str(root / ".cache/go-build"))
    environment["SF_RUN_ID"] = run_dir.name
    environment["PYTHONUNBUFFERED"] = "1"
    process = None
    try:
        with log.open("wb") as stream:
            process = subprocess.Popen(stage["command"], cwd=root, env=environment,
                                       stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                result["exit_code"] = process.wait(timeout=stage["timeout_seconds"])
            except subprocess.TimeoutExpired:
                result["errors"].append(f"timeout after {stage['timeout_seconds']}s")
                stop_process(process)
                result["exit_code"] = process.returncode
    except OSError as error:
        result["errors"].append(str(error))
    finally:
        if process is not None and process.poll() is None:
            stop_process(process)
    if result.get("exit_code") != 0:
        result["errors"].append(f"command exit_code={result.get('exit_code')}")
    for relative, previous in before.items():
        path = local_path(root, relative)
        current = file_stamp(path)
        if current is None or current == previous or current[1] < started_ns:
            result["errors"].append(f"report was not produced by this stage: {relative}")
            continue
        try:
            data = path.read_bytes()
            document = json.loads(data)
            if not isinstance(document, dict) or document.get("success") is not True:
                result["errors"].append(f"report success is not true: {relative}")
            snapshot = run_dir / "evidence" / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(data)
            result["reports"][relative] = {"path": snapshot.relative_to(run_dir).as_posix(),
                                           "sha256": sha256(snapshot)}
            if relative in stage.get("artifact_reports", {}):
                allowed = local_path(root, stage["artifact_reports"][relative])
                directory = local_path(root, document["artifacts"])
                if not directory.is_relative_to(allowed) or directory == allowed or not directory.is_dir():
                    raise ValueError("artifact directory must be a unique run beneath the declared artifact root")
                artifacts = {}
                for source in sorted(directory.rglob("*")):
                    if not source.is_file():
                        continue
                    if source.is_symlink() or not source.resolve().is_relative_to(directory):
                        raise ValueError(f"artifact escapes report directory: {source}")
                    original = source.relative_to(root).as_posix()
                    before_copy = file_stamp(source)
                    if before_copy is None or before_copy[1] < started_ns:
                        raise ValueError(f"artifact was not produced by this stage: {original}")
                    archived = run_dir / "artifacts" / original
                    archived.parent.mkdir(parents=True, exist_ok=True)
                    archived.write_bytes(source.read_bytes())
                    if file_stamp(source) != before_copy:
                        raise ValueError(f"artifact changed while archiving: {original}")
                    artifacts[original] = {"path": archived.relative_to(run_dir).as_posix(),
                                           "sha256": sha256(archived), "bytes": archived.stat().st_size}
                if not artifacts:
                    raise ValueError("report has no raw artifacts")
                result["artifacts"][relative] = artifacts
        except (OSError, ValueError, KeyError, TypeError) as error:
            result["errors"].append(f"invalid report {relative}: {error}")
    if not result["errors"]:
        result["status"] = "passed"
    result["finished_at"] = now()
    result["duration_seconds"] = (time.time_ns() - started_ns) / 1e9
    result["log"] = {"path": log.relative_to(run_dir).as_posix(), "sha256": sha256(log)}
    return result


def render_html(result: dict[str, Any]) -> str:
    rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (
        stage["id"], stage["status"], round(stage.get("duration_seconds", 0), 2),
        "; ".join(stage.get("errors", [])))) + "</tr>" for stage in result["stages"])
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>StarFabric 单机验收</title>"
            "<style>body{font:16px system-ui;max-width:1200px;margin:3em auto}td,th{padding:.7em;"
            "border-bottom:1px solid #ddd;text-align:left}table{border-collapse:collapse;width:100%}</style>"
            f"<h1>StarFabric 单机验收：{'通过' if result['success'] else '未通过'}</h1>"
            f"<p>运行：{html.escape(result['run_id'])}；范围：{html.escape(result['profile'])}；"
            f"全系统闭环：{'是' if result['system_closed'] else '未完成'}</p>"
            "<table><tr><th>阶段</th><th>状态</th><th>耗时（秒）</th><th>原因</th></tr>"
            + rows + "</table><pre>" + html.escape(json.dumps(result.get("closure", {}),
                ensure_ascii=False, indent=2)) + "</pre></html>")


def run_suite(profile: str) -> int:
    suite = load_suite()
    runs = ROOT / "reports/runs"
    runs.mkdir(parents=True, exist_ok=True)
    with (runs / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another single-PC suite is using the shared lab") from None
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        run_dir = runs / run_id
        run_dir.mkdir()
        result: dict[str, Any] = {"schema_version": 1, "run_id": run_id, "profile": profile,
            "started_at": now(), "success": False, "system_closed": False, "status": "running",
            "source_sha256": source_digest(), "stages": []}
        write_json(run_dir / "run.json", result)
        write_json(ROOT / "reports/single-pc-latest.json", {"run_dir": run_dir.relative_to(ROOT).as_posix()})
        selected = [s for s in suite["stages"] if profile == "full" or s["profile"] == "core"]
        passed: set[str] = set()
        interrupted = False
        try:
            for index, stage in enumerate(selected, 1):
                print(f"[{index}/{len(selected)}] {stage['id']}", flush=True)
                if any(dep not in passed for dep in stage.get("depends_on", [])):
                    outcome = {"id": stage["id"], "status": "blocked", "reports": {},
                               "errors": ["required preceding stage did not pass"]}
                else:
                    outcome = execute_stage(stage, run_dir)
                result["stages"].append(outcome)
                if outcome["status"] == "passed":
                    passed.add(stage["id"])
                print(f"  {outcome['status']}: {'; '.join(outcome.get('errors', []))}", flush=True)
                write_json(run_dir / "run.json", result)
        except KeyboardInterrupt:
            interrupted = True
        result["source_unchanged"] = source_digest() == result["source_sha256"]
        result["status"] = "interrupted" if interrupted else "finished"
        result["finished_at"] = now()
        write_json(run_dir / "run.json", result)
        if profile == "full" and not interrupted:
            audit = subprocess.run(["python3", "tools/verify_single_pc_closure.py", "--require-complete",
                "--run-dir", str(run_dir), "--output", str(run_dir / "closure.json")], cwd=ROOT, check=False)
            if (run_dir / "closure.json").is_file():
                result["closure"] = json.loads((run_dir / "closure.json").read_text())
            result["system_closed"] = audit.returncode == 0 and len(passed) == len(selected) and result["source_unchanged"]
        result["success"] = (not interrupted and len(passed) == len(selected) and result["source_unchanged"]
                             and (profile == "core" or result["system_closed"]))
        write_json(run_dir / "run.json", result)
        (run_dir / "report.html").write_text(render_html(result), encoding="utf-8")
        print(f"{'PASS' if result['success'] else 'FAIL'}: {run_dir / 'report.html'}", flush=True)
        return 0 if result["success"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "run", "status", "audit"])
    parser.add_argument("--profile", choices=["core", "full"], default="full")
    args = parser.parse_args()
    try:
        if args.action == "plan":
            suite = load_suite()
            for stage in suite["stages"]:
                if args.profile == "full" or stage["profile"] == "core":
                    print(f"{stage['id']}: {stage['command']} -> {stage.get('reports', [])}")
        elif args.action in {"status", "audit"}:
            pointer = json.loads((ROOT / "reports/single-pc-latest.json").read_text())
            path = local_path(ROOT, pointer["run_dir"]) / "run.json"
            if args.action == "status":
                print(path.read_text())
            else:
                raise SystemExit(subprocess.call(["python3", "tools/verify_single_pc_closure.py",
                    "--require-complete", "--run-dir", str(path.parent)], cwd=ROOT))
        else:
            raise SystemExit(run_suite(args.profile))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
