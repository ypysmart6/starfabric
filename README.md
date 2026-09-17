# StarFabric 2.0

默认大星座入口现已接入逐星物理模式：独立轨道、地理网关、动态接触和实际报文时延／限速。配置、运行阶段与证据见[物理星座说明](docs/physical-constellation.md)。历史逻辑图通过记录不代替新模式验收。

StarFabric 是一套覆盖动态 LEO 控制、开放网络设备接口、自动验证、5G NTN 集成、云原生 NOC 和星上自治运行时的工程系统。范围以[非 AI 技术覆盖矩阵](docs/coverage.md)为准：原文中的非 AI 卫星技术要么已有可测试实现，要么已有可运行集成合同，要么被明确标为只能上真实硬件验收；不会把未运行的外部系统写成已完成。

最终目标是全文所有非 AI、非必须实物技术形成统一单机系统闭环。轨道/FRR/5G/星载自治/NOC 的统一业务链已建立，全文仍有 [8 组待完成系统与验收能力](docs/remaining-systems.md)；“存在实现/集成合同”不代表全系统完成。具体定义见[全文单机闭环验收](docs/single-pc-acceptance.md)。

常驻实时系统 `make live-start` 默认编排 **120 星、24 个六大洲网关、384 条并行业务**，业务覆盖 276 对网关，详见 [实时运行说明](lab/live/README.md)。编排数量不是已验证的稳定容量；本机逐档测试与限制见 [容量测试记录](reports/capacity/README.md)。原四网关配置仍用于独立验收。

统一平台入口为 `make test-unified`（同 `make test-platform`），现由大星座配置直接驱动真实 FRR、逐星 Rust 进程和统一业务验收。使用 `make test-unified SATELLITES=120 GATEWAYS=4 PLANES=12` 或 `make test-unified SATELLITES=360 GATEWAYS=8 PLANES=18`；两种规模是同一系统的参数配置，另有 3 个 Kubernetes 节点。同一平台依次验证 OSPFv2/v3、LDP、SR-MPLS、SRv6、PCEP、BGP-LS、EVPN/VXLAN，以及 Helm、Cilium、Hubble、Lease HA、5G、自治和 NOC。是否实际部署并全部通过，以本轮 `reports/platform-runtime.json` 为准，资源不足会明确失败。用法与数量见[大星座直接驱动](docs/platform-scale.md)、[统一业务范围](docs/unified-integration.md)和[独立专项清单](docs/independent-closures.md)。`make test-constellation` 仍保留同一生成器的 memory adapter 回归，不替代真实验收。

完整的 Phase 0–6 机器可校验清单位于 `docs/coverage-manifest.json`；
`make coverage` 会检查每个条目的证据文件、外部边界与禁止依赖。

## 最短可运行路径

无 Docker 的快速逻辑/数字孪生门禁：

```bash
make check
make api-contract
make build
./bin/sfctl experiment run --scenario scenarios/leo-resilient.json
```

它验证版本化拓扑、约束主备路径、Prepare/Commit、actual-state probe、故障回滚和持久化恢复，但其默认设备是内存数字孪生。

真实单机包数据面闭环需 Docker、containerlab 和匹配 OTG 1.61 的 `otgen`：

```bash
PATH="$PWD/bin:$PATH" make test-live
```

它使用真实 FRR FIB 和 Ixia-c 双向流量，在主链路故障后把 controller plan、actual FIB、收敛时间和丢包统一验收。
三条新增的单机闭环分别验收“轨道输入→真实路由/报文”、“基础协议/标签→故障收敛→报文”和“高级协议→内核状态→故障恢复”：

```bash
make test-orbit-live
make test-protocol-live
make test-advanced-live
make test-unified  # 协议、Kubernetes/Helm、5G、轨道、星上自治与 NOC 同平台运行
```

已安装所有单机依赖后，`make acceptance-single-pc` 统一执行所有已接入阶段，保存本次运行的日志、报告快照和源码哈希，最后强制检查完整闭环。缺项、失败、未重新生成的旧报告均不能通过。用 `make acceptance-plan` 查看命令，用 `make acceptance-status` 查看进展；报告位于 `reports/runs/<run-id>/report.html`。

```text
TLE/SGP4 or mission OEM → contact windows → predictive topology
                                           ↓
NetBox inventory → SLA intent → deterministic TE + gateway selection
                                           ↓
                       shadow validate → make-before-break RoutePlan
                                           ↓
                 gNMI/gNOI + gRIBI FIB ACK | FRR | digital twin
                                           ↓
                    actual FIB + OTG probe → commit or rollback
                                           ↓
             Prometheus + OTel → Loki/Tempo/Grafana/Hubble NOC
```

## 真实设备与实验入口

- `lab/containerlab/phase1-frr-otg`：4 台 FRR + Ixia-c，IS-IS/BGP/BFD/LFA、故障丢包和收敛 SLO。
- `lab/containerlab/protocol-matrix`：OSPFv2/v3、IS-IS、LDP、SR-MPLS 的主备路故障报文闭环。
- `lab/containerlab/advanced-protocols`：SRv6 SRH 主备段、BGP-LS NLRI 撤销/恢复、PCEP 会话与 PCReq/PCRep、EVPN Type-2→Linux FDB→VXLAN UDP/4789 的聚合闭环。
- `lab/orbit-closed-loop`：TLE→SGP4→CCSDS OEM→可见性/range/delay/Doppler→contact window→FRR FIB→Ixia-c 双向报文闭环。
- `lab/kne` + `tests/ondatra`：Lemming/OTG KNE testbed，gNMI、gRIBI FIB ACK 和 OTG contract。
- `lab/batfish`：静态 parse/reference/loop/reachability 门禁。
- `lab/p4`、`lab/sonic`：P4Runtime 与 SONiC/SAI 的可编程星载交换扩展合同。
- `sf-inventory`：从 NetBox/Nautobot 导入慢变 inventory、IPAM 和物理 cable 元数据。

OpenConfig 模式从节点标签读取 `gnmi_target`、`gribi_target`、`network_instance` 和可选 `tls_server_name`：

```bash
./bin/sf-controller --scenario scenario-with-targets.json --adapter openconfig \
  --openconfig-ca pki/ca.pem --openconfig-client-cert pki/client.pem \
  --openconfig-client-key pki/client-key.pem --state-dir data
```

默认要求安全传输和 gNOI Time health；只有本地 emulator 才应使用 `--openconfig-insecure`。

## 轨道、OISL 与预测式路由

任务星历可以直接输入 `tools/ephemeris_contacts.py`。TLE 路径先用 SGP4 转 OEM：

```bash
python3 -m pip install -r tools/requirements-orbit.txt
python3 tools/tle_to_oem.py --catalog catalog.json --start 2026-09-04T00:00:00Z \
  --duration-seconds 5400 --output-dir /tmp/oem
python3 tools/ephemeris_contacts.py --oem sat-01=/tmp/oem/sat-01.oem \
  --ground-stations ground.json --output /tmp/contacts.csv
python3 tools/contactplan.py --base scenarios/leo-resilient.json \
  --contacts /tmp/contacts.csv --output /tmp/predictive.json
```

接触计划包含星地/星间可见性、范围、传播时延、Doppler 和窗口；控制器拒绝在链路或计划过期后安装 stale route。OISL 网络可用性由 acquisition state machine 决定，只有 `locked/degraded` 进入路由图。

## 5G NTN

5G NTN 是本卫星通信工程的业务接入/端到端集成纵向。单电脑基线会启动固定版本的 Open5GS＋UERANSIM，验证 N2/NGAP、PFCP、UE 注册、PDU session、双向 N3/GTP-U，并在活跃用户面注入星上传输时延：

```bash
make test-5g
```

`ntn/` 另提供 srsRAN Release 17 NTN overlay 和四组集成实验：gateway switching、interruption recovery、transport latency/loss、DSCP/QoS classes。离线合同检查：

```bash
for experiment in ntn/experiments/*.json; do python3 ntn/experiment.py --manifest "$experiment" --validate-only; done
```

单机基线的 UERANSIM 是无射频的 5G SA 仿真；公开上游的 srsRAN GEO fixed-delay 示例也不能被解释为完整 LEO variable-delay/Doppler 射频信道。

## 星上运行时

`onboard/` 是独立 Rust 组件，包含断联自治、generation/stale rejection、备用路由、持久化、systemd watchdog、fault injection 和 Ed25519 签名 A/B 更新：

```bash
cargo test --manifest-path onboard/Cargo.toml
cargo build --release --target aarch64-unknown-linux-gnu --manifest-path onboard/Cargo.toml
```

同时提供 QEMU ARM64 SIL、Buildroot 和 Yocto 集成入口。Secure Boot、bootloader slot、FPGA、modem、OISL terminal、SAI/ASIC 和 HIL 只能在目标 BSP/硬件上验收。

## 云原生部署与 NOC

Compose 启动 controller、Prometheus、OpenTelemetry Collector、Loki、Tempo 与 Grafana：

```bash
docker compose -f deploy/compose/docker-compose.yaml up --build
```

Helm 包含 TLS 1.3 mTLS、独立内部 probe/metrics 端口、Secret、Lease RBAC、PDB、anti-affinity、CiliumNetworkPolicy 和 HA values。HA 使用 Kubernetes Lease 保证单写者，接管前 reload RWX 持久状态：

```bash
helm upgrade --install sf deploy/helm/starfabric -f deploy/helm/starfabric/values-ha.yaml
```

大规模或跨区域生产不应把 RWX JSON 当作最终数据库，应替换为带事务、备份和 schema migration 的状态服务。Argo CD 与 Cilium/Hubble 的声明式入口位于 `deploy/gitops` 和 `deploy/cilium`。

## 验证

```bash
make fmt vet test test-race test-e2e build
python3 -m py_compile tools/*.py ntn/*.py lab/containerlab/phase1-frr-otg/*.py lab/batfish/*.py lab/p4/*.py
```

核心 CI 覆盖 controller restart、node/link failure、delayed/out-of-order telemetry、partial commit、traffic verifier failure、observability failure和1000星 planner smoke。外部 KNE/Ondatra、Batfish、FRR/OTG、srsRAN/Open5GS、P4/SONiC、QEMU/HIL 测试必须保存各自的实际证据，详见[生产门禁](docs/production-readiness.md)。

## 文档

- [技术覆盖与明确排除](docs/coverage.md)
- [单电脑实现边界与 P0–P2 结论](docs/one-computer-scope.md)
- [架构与不变量](docs/architecture.md)
- [生产门禁](docs/production-readiness.md)
- [控制器恢复 Runbook](docs/runbooks/controller-recovery.md)
- [REST API](api/openapi.yaml)
- [Protobuf contract](api/proto/starfabric/v1/control.proto)
