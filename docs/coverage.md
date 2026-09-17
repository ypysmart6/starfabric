# 原文非 AI 技术覆盖矩阵

`coverage-manifest.json` 是可由 CI 校验的完整事实源；本文是人类可读摘要。
最终“完成”以[全文单机闭环验收](single-pc-acceptance.md)及当前运行的严格门禁为准。共享轨道/FRR/5G/星载自治/NOC 运行由 `p6-unified-system` 的实测门禁验证；原文 24 星/4 网关/100 流/24 小时等预算仍在 `p4-document-performance` 中待完成。其他缺项见[剩余系统清单](remaining-systems.md)，不得由独立组件演示替代。
`python3 tools/verify_coverage.py` 会强制 Phase 0–6 都有映射、证据文件真实存在、
B/C 项具有外部或硬件边界，并检查依赖清单没有被禁止的学习/生成式组件。

本矩阵是工程事实清单，不把“存在配置文件”写成“生产验证完成”。覆盖级别：

- **A — 核心已实现**：代码进入默认工程，具有本机自动测试或确定性演示。
- **B — 集成已实现**：适配器、配置和验收脚本已提供；必须连接外部容器、NOS、RAN/Core 或仿真器后运行。
- **C — 目标接口已定义**：只能在任务硬件、BSP、射频/光学设备或 HIL 环境完成最终验收。
- **X — 明确排除**：用户要求排除的 AI，以及不属于卫星/NTN 主线的 Networks-for-AI 扩展。

## Phase 0–2：路由、开放接口和自动验证

| 原文技术 | 级别 | 本工程对应实现 | 不能混淆的边界 |
|---|---:|---|---|
| Go / Python / Docker / GitHub Actions | A | Go 控制器、Python 场景工具、多阶段容器、CI 的 fmt/vet/race/e2e/覆盖门禁 | Docker 运行需 daemon 权限 |
| dependency scanning / dependency updates | B | CI `govulncheck`、Go/Rust lockfile 与 Dependabot 的 Go/Cargo/Python/Docker/Actions 周期检查 | 依赖在线漏洞库和 GitHub 仓库安全设置；本机离线测试不冒充扫描结果 |
| Ethernet / ARP / ND / IPv4/IPv6 / MTU / ICMP / TCP / UDP / NAT / VRF / netns / tc | B | `lab/linux-networking` 的 root-only 可回收集成实验 | 是 Linux 数据面，不代表星载 ASIC 或线速 |
| Linux forwarding / RIB / FIB | A/B | FRR adapter、actual-state route probe；星上 runtime 的幂等 `ip route replace` 备用路由 | 默认数字孪生不等于内核/ASIC FIB |
| Ethernet switching / MAC learning / RSTP | B | 双 Linux bridge 冗余链路实验验证 FDB 与 STP 阻塞；RSTP 复用相同验收 | 生产 RSTP 需支持该协议的 NOS/`mstpd` 靶机并保存收敛包证据 |
| FRRouting / zebra | A/B | `internal/adapter/frr`、`make test-live` 控制器/FRR/OTG 闭环和协议矩阵 | 单机闭环已实测；生产 NOS 仍需目标环境验收 |
| OSPFv2 / OSPFv3 | A/B | `make test-protocol-live`：双路 Full、双栈 FIB、主链路双端故障、备路收敛、持续流量与恢复 | 与 IS-IS 并行是单机协议验收设计，不是推荐生产双 IGP 设计 |
| IS-IS / BGP / BFD / LFA | A/B | `phase1-frr-otg` 与 protocol matrix；主闭环由 OTG 双向流量验证 | 其他协议组合仍按各自矩阵运行 |
| MPLS / LDP / SR-MPLS / SR Policy | A/B | `make test-protocol-live`：LDP 邻接/标签、SR Prefix-SID/LFIB、主备路 MPLS 报文抓包；`make test-advanced-live`：`pathd -M pathd_pcep` 真实会话与动态候选请求 | 本机 Linux MPLS/LDP/SR-MPLS 已实测；最小 PCE 有意返回 NO-PATH，生产 PCE 算路与正向 SR Policy 安装仍需目标互操作 |
| SRv6 | A/B | `make test-advanced-live`：四节点 locator 通告、Linux `seg6local End`、主/备 SID 内核路由、SRH type 4 抓包、持续流量与恢复 | 是单机内核功能/收敛证据，不代表星载 ASIC 线速或厂商 NOS 互操作 |
| policy / route-map / debugging | B | FRR 配置、JSON show/state 校验和实验 runbook | 厂商 NOS 命令差异留在 adapter |
| OpenConfig YANG / gNMI Get, Set, Subscribe | A/B | 官方 `openconfig/gnmi` 客户端、JSON-IETF 配置与流式遥测 | 互操作需 Lemming/SONiC/设备靶机 |
| gRIBI | A/B | 官方 gribigo，NH/NHG/IPv4/IPv6 AFT、FIB ACK、Persist、All-Primary、actual Get、make-before-break | 事务和持久化语义以目标 NOS 为准 |
| gNOI | A/B | `make test-openconfig`：System Ping/Time、定时重启/取消、BERT start/result/stop、X.509 capability/load/readback、有界原始以太帧抓包、OS transfer/progress/validate/activate/verify；TLS/mTLS 共用安全传输 | 破坏性 RPC 只在独立经审批运维流程中触发；仿真软件包/证书/端口不代表厂商设备互操或硬件效果 |
| containerlab | B | 教学实验、FRR+OTG 产品实验、协议矩阵 | 本机需 Docker/net admin 权限与镜像 |
| KNE | B | Lemming + Keysight OTG topology/testbed | 需 Kubernetes 集群与 KNE operators |
| Ondatra | B | 独立 Go module：gNMI、OTG telemetry、gRIBI FIB ACK | 编译成功不等于真实 testbed 已运行 |
| Open Traffic Generator / Ixia-c | A/B | `make test-live` 已串起意图、实际 FRR FIB、故障、重规划和双向包 SLO | 软件流量引擎性能不能代表硬件线速 |
| NetBox / Nautobot | A/B | HTTPS REST inventory/cable/IPAM importer `sf-inventory`，分页同源校验 | 高频链路状态不写回 Source of Truth |
| Batfish | B | parse、undefined reference、loop、reachability 变更前门禁 | 静态模型不替代 OTG 和实际 FIB |
| BGP-LS / PCEP / EVPN-VXLAN（按场景深入） | A/B | `make test-advanced-live`：IS-IS TED→eBGP BGP-LS collector→故障 NLRI 撤销/恢复；PCEP OPEN/KEEPALIVE/PCReq/PCRep；EVPN Type-2→Zebra Netlink→Linux `extern_learn` FDB→VXLAN UDP/4789 主备链路报文 | 单机 FRR/Linux 已实测；厂商 collector/PCE/NOS 互操作、正向 PCE 算路和硬件线速仍是外部验收 |

## Phase 3–4：动态 LEO 与生产可靠性

| 原文技术 | 级别 | 本工程对应实现 | 边界 |
|---|---:|---|---|
| TLE / SGP4 | A/B | `make test-orbit-live`：固定 TLE 生成 CCSDS OEM，并继续驱动路由与报文闭环 | TLE/SGP4 是工程预测输入，不是任务级导航真值 |
| 预计算星历 / CCSDS OEM | A | OEM KVN 解析、contact compiler、输入/OEM/窗口哈希溯源报告 | OEM 生成本身应来自经验证的任务动力学链路 |
| 星地可见性、链路窗口 | A | 最小仰角、地球自转、双向接触窗、range/delay/Doppler 到 FRR 链路参数与实际 Ixia 报文 | 使用球形地球近似；精确链路预算留给任务模型 |
| 星间可见性 / OISL | A/C | 地球遮挡、距离/传播时延/Doppler；搜索→捕获→锁定→退化→故障状态机 | PAT、光机电和真实终端只可 HIL 验证 |
| time-varying topology | A | 版本快照、增量事件、有效期、确定性回放 | 外部遥测时钟必须给出误差预算 |
| predictive routing / shadow control | A | 未来快照预计算、shadow validation、提前激活 | 生产需与实际轨道/链路调度器校时 |
| Dijkstra / ECMP / K candidates | A | 确定性候选路径与同成本选择 | 多商品流全局最优不作虚假承诺 |
| latency/capacity/loss/reliability constraints | A | SLA 约束路径、按 priority 决定性排序、`demand_bps` 主路径容量预留 | 是顺序 admission/TE，不声称多商品流全局最优 |
| link/node/SRLG-disjoint backup | A | 双向物理边、可选节点和 `risk_groups` 共享故障域移除 | SRLG 标签必须由 Source of Truth/任务接触输入 |
| gateway selection / joint TE | A | 多候选网关可达性和确定性最优选择 | 射频资源分配由外部 RAN/调度器提供 |
| make-before-break | A/B | 预测计划；gRIBI 分阶段 NH→NHG→route switch→stale cleanup | 无损程度由目标 NOS + OTG 测量 |
| idempotency / versions / stale rejection | A | event ID、per-subject sequence、plan ID、actual reconciliation | — |
| timeout / retry / exponential backoff | A | 所有 adapter 操作有 deadline 和有界重试 | 不允许无限重试 |
| partial failure / canary / rollback | A | Prepare、批次 Commit、每批验证、逆序全局回滚 | 多设备原子性通过补偿，不声称分布式原子事务 |
| controller restart recovery | A | 原子 fsync/rename 状态恢复；Lease 接管前 reload | RWX JSON 是小规模 HA；大规模生产应换事务数据库 |
| control-plane sharding / regional failure domains | A/B | rendezvous hashing 稳定分片、副本和区域故障域分散；每 shard 仍需 Lease fencing | 跨区域复制/event log 是部署方状态服务职责 |
| chaos cases | A/B | crash recovery、node/link、乱序/延迟、partial commit、probe failure、telemetry backend failure；FRR/OTG runtime fault suite | 外部进程/网络故障需在实验环境运行 |
| scale / performance | A/B | 1000 星 planner smoke/benchmark；reconcile、route programming、event-to-forwarding metrics | 24h soak 和更大规模需 CI/testbed |
| OpenTelemetry / Prometheus / Grafana | A/B | OTLP HTTP trace、JSON logs→Collector→Loki、metrics、Tempo、Grafana provisioning、alerts | 后端不可用不阻塞控制环 |

## Phase 5：5G NTN / Direct-to-Device

| 原文技术 | 级别 | 本工程对应实现 | 边界 |
|---|---:|---|---|
| 5G SA / NGAP / N2 | A/B | `make test-5g` 固定 Open5GS/UERANSIM 版本并实测 NG Setup、注册与 PDU 会话；另有 srsRAN overlay | 单机 UERANSIM 是可运行基线，不等于 srsRAN NTN 或商用 UE 一致性 |
| GTP-U / N3 / UPF | A/B | 单机实际双向 UDP/2152 抓包、UE tunnel ping、transport scenario | 加密/用户隐私数据不得进入公开报告 |
| PFCP / AMF / SMF / UPF | A/B | 单机实测 PFCP association；Open5GS overlay 固定 N2/N3/PFCP、PLMN/TAC/S-NSSAI、地址池和 MTU | 本仓库不重写 5GC；任务部署仍需固定版本验收 |
| NTN delay / Doppler / SIB19 / timers | A/B | `make test-ntn-r17` 校验 band 256、SIB19、common TA、extended timers、HARQ-off 与 ECEF ephemeris，并通过真实 ZeroMQ complex-IQ 流实测 119.72 ms delay、Doppler 注入/预补偿、path loss；channel 同时支持 delay/Doppler rate | 商用 NTN UE 与 OTA 一致性必须依赖厂商/射频实物，不在单机软件结论内 |
| mobility / moving cell / gateway switch | B | gateway candidates、make-before-break 与实验 1 | UE/基站移动过程最终由真实栈信令确认 |
| interruption recovery | B | 实验 2：链路中断、恢复时间/丢包 SLO | — |
| latency variation | A/B | 单机真实 PDU 会话上注入 40/80 ms；实验 3 支持动态 delay/loss；IQ channel 支持逐时变化的 delay/Doppler rate | netem 负责 IP 效应，ZeroMQ complex-IQ channel 负责基带信道效应 |
| QoS / NTN slicing | A/B | intent class/priority/DSCP、实验 4 多业务流 | 端到端 5QI/网络切片需 RAN/Core 相同配置配合 |
| O-RAN（岗位相关可选） | B/C | srsRAN E2 overlay：E2AP、E2SM-KPM、E2SM-RC、metrics 和 PCAP；O1 保留 SMO 验收边界 | E2 需 O-RAN SC/FlexRIC 实测；O1 和 RU/DU/CU 一致性仍需具体设备 |

## Phase 6：云原生 NOC 与星上软件

| 原文技术 | 级别 | 本工程对应实现 | 边界 |
|---|---:|---|---|
| Kubernetes / Helm | B | 非 root/read-only Deployment、Service、PVC、probes、PDB | 集群部署需实际 StorageClass、证书、镜像仓库 |
| HA controller / leader failure | A/B | Kubernetes Lease 单写者、follower 503、接管前 reload、RBAC、anti-affinity | 跨区域一致性数据库未内置 |
| GitOps / Argo CD | B | Application manifest、声明式 Helm values | 仓库 URL/环境 promotion 由部署方设置 |
| mTLS / certificates / secrets / RBAC | A/B | TLS 1.3 双向认证、Secret mounts、Bearer optional、最小 Lease RBAC | 证书签发/轮换需企业 PKI/secret manager |
| Cilium / Hubble | B | network policy、设备 CIDR allowlist、Hubble relay/UI/metrics values | 需 Cilium 集群运行时验证流可见性 |
| logs / metrics / traces / alert / NOC | A/B | Loki、Prometheus、Tempo、Grafana 与控制器 OTLP | retention、告警路由和 SLO 需按环境定标 |
| rollout / rollback / recovery runbook | A/B | rolling HA、单副本 Recreate、PDB、Helm rollback/GitOps contract、runbook | 应在预生产演练升级和降级 |
| Rust node runtime | A | 独立 Rust crate，容器化编译测试 | 不是地面 Go 控制器的别名 |
| Embedded Linux / systemd / watchdog | A/B | hardened systemd unit、sd_notify watchdog、断联自治 | RTOS/任务 BSP 不在通用 Linux 实现内 |
| ARM64 cross compilation / QEMU/KVM / device emulation | B | ARM64 build命令与 QEMU SIL launcher | 用户提供合法 kernel/rootfs/BSP |
| Yocto / Buildroot | B | recipe/package integration fragments | 每个板卡需要自己的 BSP layer/defconfig |
| OTA / signed update / rollback image | A/C | Ed25519 + SHA-256 校验、inactive A/B slot、pending/confirm/rollback、原子 fsync state | Secure Boot root-of-trust 和 bootloader rollback 必须在硬件验证 |
| local fallback / disconnected operation | A | hold timer、持久 generation、幂等备用路由 | 任务规则和安全状态由系统工程定义 |
| fault injection / SIL / HIL / deterministic behavior | A/B/C | API fault、确定性 loop、QEMU SIL；HIL 接口边界 | HIL 需要 modem/OISL/switch/FPGA 实物 |
| SONiC / SAI / switch SDK | B/C | Ondatra/OTG/OpenConfig/gRIBI 验收合同 | 不分发厂商 NOS/SDK，不声称 ASIC 已验证 |
| P4 / P4Runtime | B/C | P4_16 IPv4/IPv6/QoS pipeline 与 P4Runtime shell 编程 | BMv2 不是飞行 ASIC；架构映射和资源门禁需硬件 |
| eBPF / XDP / AF_XDP / DPDK | B/C | Cilium/Hubble 是 Kubernetes eBPF 升级实现；`lab/dataplane` 提供 XDP 计数器和 AF_XDP/DPDK 选型验收合同 | 只在 profiling 证明需要加速后开启，需 kernel/NIC/driver/HIL |
| C/C++ 原角色 | A/B/C | 地面控制器升级为 Go，星上服务升级为 Rust；C 只保留 XDP/kernel 边界，C++ 只留给必须使用的 BSP/vendor SDK | 不为“覆盖关键词”重写成熟协议栈 |
| 相控阵校准 / 宽带与混合波束赋形 / RF-baseband / modem / DSP / FPGA | C | `docs/hil/rf-phy-acceptance.md` 定义网络侧规范化 Link 输入、故障语义和 HIL 证据 | 没有天线、暗室/外场、基带与任务硬件时不能声称实现或认证 |

## 排除范围

| 原文内容 | 级别 | 原因 |
|---|---:|---|
| 强化学习、GNN、AI-based routing、AI coding agent 工作流 | X | 用户明确要求排除 AI；策略插件只保留确定性接口 |
| Networks for AI、RDMA、RoCEv2、InfiniBand、PFC/ECN、DPU | X | 属于原文的跨行业扩展，不属于本卫星/NTN 系统范围 |
| 自研完整 OSPF/IS-IS/BGP、5GC、NOS、轨道动力学、inventory UI、日志数据库 | X | 采用 FRR/Open5GS/SONiC/OpenConfig/任务星历/NetBox/OTel 等成熟升级方案 |

## “全面”的准确含义

本仓库现在对原文的每个卫星/网络系统技术给出三种之一：可测试实现、可运行外部集成或明确的硬件验收合同。“涵盖”不等于“本机已通过”。它不能把未提供的卫星载荷、基带、光终端、ASIC SDK、任务密钥、商用 UE 和 Kubernetes 集群凭空变成“已通过生产认证”。最终 Definition of Done 是 A 项自动通过、每个选定 B 项在固定 testbed 留存日志/pcap/FIB 证据、每个部署相关 C 项有对应硬件/任务验收记录。
