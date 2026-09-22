**动态帧性能观测与严格验收**

第一批修改补充观测并修正验收。提交批次、验证并发、CPU 配额、状态读取轮数、EVPN 路由写入和持久化安全条件均沿用原设置。

每个新 live 运行在 `lab/unified/artifacts/<run_id>/` 保存：

| 文件 | 用途 |
| --- | --- |
| `source-manifest-start.json` / `source-manifest-end.json` | Git 提交、dirty 状态及 Go/Python/Rust/部署/场景等源码哈希；包含新增未忽略文件 |
| `cloud/controller-image.json` | 构建镜像 ID、标签、可用的 RepoDigests |
| `cloud/platform-controller-deployment.json` / `platform-controller-pods.json` | 实际参数、CPU 配额及 Pod 镜像身份 |
| `controller-client.jsonl` / `controller-proxy.jsonl` | 每次 API 请求的开始、结束、状态和墙钟耗时 |
| `controller.log` | `path computation` 和 `reconciliation profile` 结构化记录 |
| `frr-agent.jsonl` 及 `.1`–`.8` 分段 | 命令时间、结果、响应字节数、关联字段以及执行槽等待/执行时间 |
| `cpu-samples.jsonl` | 驱动与 kind 父 cgroup 的 CPU 配额、累计使用/节流和 cpuset；控制器自身数据在事务 profile 中 |
| `performance-frames.jsonl` | 每个完成帧的完整计时，独立于滚动 UI 快照 |
| `schedule.json` | 调度基准、步长、完成/跳过数及下一时隙，完成后立即更新 |

按 `run_id / frame_id / request_id / plan_id` 关联各层。关联标识用于诊断，不参与授权。日志不记录认证 token。事务 profile 在成功、失败和 noop 时都输出；`phases` 是阶段墙钟耗时，`work` 是可能并行/嵌套的设备工作量，不能与 phases 相加。每项记录调用数、失败数、总耗时、最大单次耗时和适用时的最慢设备。

阶段包括锁等待、准备前检查、准备、分批提交、批次验证、最终验证、路由探测、实包探测、回滚及控制器持久化。`work.adapter_persist` 含序列化及文件/目录同步；字节数记录写入尝试，历史条目数记录单设备最大值。CPU stat 是累计计数，应比较同一 cgroup 的前后差值，不能把累计节流时间当成本帧耗时。不存在的 cgroup 字段表示未采集到。

`apply_seconds` 保留旧含义。新增 `sample_seconds`、`snapshot_seconds`、`iteration_seconds`、`scheduled_to_forwarding_seconds` 和 `scheduled_to_completion_seconds`；整轮包含轨道采样、apply、快照、日志维护和 CPU 采样。随后的小型性能记录/状态落盘不计入本轮 iteration，其引起的调度延迟会进入下一时隙的时效指标。`forwarding_verified` 要求该轮控制器和业务更新未报错，且全部业务方向及适用的 N3 状态均为 ready；这仍不是逐流逐包通过证明。

实时性分母为测量窗口内**已经到达 10 秒截止时间的应执行时隙**。跳过、未完成、更新报错或整体/转发完成超过截止时间均不能计作及时完成。窗口末端尚未到截止时间的时隙单列 pending。保留旧完成帧 apply 通过率为 `completed_apply_passing_fraction`，便于对照，不用于新的总体判定。

容量结果包含独立的 `functional_passed`、`realtime_passed` 和 `overall_passed`。默认命令行退出码由 overall 决定；`--functional-only` 仅改变退出码依据，仍报告实时性与整体失败。完整 EVPN 验收继续单独检查业务稳定性及故障恢复，不把严格容量结果误作功能失败。

ACK 接收线程记录实际到达时间和序号。重复/未知 ACK 不推进接收状态；保留最近 256 个至少 100 毫秒的接收间隔事件，另记录采样时未结束的间隔。测量器不再用轮询时刻计算网络断流；重启、事件覆盖或过期统计会明确失败。`maximum_ack_receive_gaps` 是 ACK 往返接收间隔，包含业务正常发包间隔，并非单向网络中断。小于 100 毫秒的已结束间隔不逐项保存，不能用该指标宣称毫秒级切换。现有抓包证据仍是累计每流证明，不是每时隙证明。

在已启动且属于本次实验的运行上执行：

```bash
python3 lab/live/capacity.py --run-id <run_id> --streams 24 \
  --window 600 --warmup 600 --outage 60 --output reports/capacity/<run_id>
```

需要测量后停止该实例时加 `--stop-after`。比较性能前应确认源码哈希、镜像身份和实际参数；容量报告会检查运行启动到验收结束期间源码是否改变。缺少新格式调度证据的旧运行不会被严格实时验收判为通过。

只读汇总完成帧和对应事务：

```bash
python3 lab/live/summarize_performance.py --artifacts lab/unified/artifacts/<run_id> \
  --report reports/capacity/<run_id>/result.json --output reports/capacity/<run_id>/performance.json
```

提供 `--report` 时使用报告冻结的帧集合，并排除在 `observed_at` 之后才完成的帧，避免停止过程中新增的帧改变基线。省略 `--report` 时汇总全部保留的动态帧（包含预热）。FRR 日志每段最多 8 MiB、保留 8 个历史段；汇总器读取全部分段，并输出命令数与事务计数供检查覆盖情况。
