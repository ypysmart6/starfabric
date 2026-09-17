独立专项闭环清单

“专项闭环”表示该专项自己的输入、执行、检查及报告通过。它不能单独证明技术已进入统一星地业务。完整的历史能力条目和门禁声明分别见 [coverage-manifest.json](coverage-manifest.json) 和 [single-pc-closure.json](single-pc-closure.json)。本清单保留专项入口，用于回归和故障定位。

| 独立专项 | 入口 | 原有独立边界 |
|---|---|---|
| OSPFv2/v3、LDP、SR-MPLS | `make test-protocol-live` | 独立 FRR 四节点与专用探测流量 |
| SRv6、BGP-LS、PCEP | `make test-advanced-live` | 独立拓扑；原 PCE 返回 NO-PATH，不能作为正向业务路径下发证据 |
| EVPN/VXLAN | `make test-advanced-live` | 独立 VTEP、主机和 MAC/FDB 验证 |
| Kubernetes、Helm、Cilium、Hubble、Lease HA | `make test-cloud-runtime` | 原云专项使用 memory adapter、独立 kind 集群及共享 hostPath 存储 |
| FRR、OTG/Ixia-c、BFD/LFA、流量 SLO | `make test-live` | 独立测试仪流量；不是统一平台的 UE PDU 会话 |
| Linux 路由、MTU、ICMP/TCP/UDP、PCAP | `make test-linux` | Linux 网络基础专项 |
| gNMI/gNOI/gRIBI/OpenConfig | `make test-openconfig` | 软件网络设备靶机和事务验证 |
| NetBox/Nautobot 台账导入 | `make test-inventory` | 输入导入和模型校验 |
| Batfish | `make test-batfish` | 独立配置静态分析 |
| NTN IQ 信道 | `make test-ntn-r17` | 独立信道样本和指标，不等同于真实外部 NTN UE |
| srsRAN | `make test-srsran` | 独立 gNB/test UE 运行 |
| QoS/切片传输映射 | `make test-qos` | 独立分类、标记和 tc 类计数 |
| P4/BMv2 | `make test-p4` | 独立软件交换数据面 |
| XDP/eBPF | `make test-xdp` | 独立内核程序和报文验证 |
| 星上 Rust、签名 A/B 更新 | `make test-onboard` | 运行时、状态和更新专项；统一业务另有实际自治验收 |
| ARM64/QEMU | `make test-qemu` | 软件指令集与启动运行；不能代替飞行 BSP/硬件 |
| API 契约与 SDK | `make test-api-contract` | 协议和接口契约专项 |
| 安全认证和传输 | `make test-security` | 令牌/mTLS 专项 |
| HA、故障注入、恢复 | `make test-ha`、`make test-reliability` | 各自的持久化和故障场景 |
| 指标、日志、追踪及 Grafana | `make test-observability` | 独立可观测性门禁；统一业务还需查询其实际控制器数据 |
| 星座生成器的内存回放回归 | `make test-constellation` | 同一配置的 memory adapter 回放；真实大星座统一入口为 `make test-unified`，见 [直接驱动说明](platform-scale.md) |

统一平台的新增入口是 `make test-platform`，实现位于 `lab/platform/`。它的接入结论只能依据本轮 `reports/platform-runtime.json` 中的 `success`、`full_protocol_integration` 和具体检查项。开发或失败运行保持 `false`，不会借用上述专项旧报告来补齐通过状态。

本次目标是把表中前四行纳入同一个星地平台。其余专项继续单独列示；没有相应同轮业务证据时，不称为已经进入统一平台。
