默认入口已采用逐星物理模式，详见[物理星座说明](../../docs/physical-constellation.md)。以下历史大星座记录来自逻辑图运行。

统一星地协议与云平台

入口：`make test-platform`，与 `make test-unified` 相同。星座配置直接决定实际 FRR 节点、链路、网关业务和每星 Rust 进程；默认 **120 星＋4 网关**。它们与一套 Open5GS/UERANSIM PDU 会话、三个 Kubernetes 节点、三个控制器副本及 NOC 组成同一运行。完整参数、数量和证据判定见 [大星座直接驱动](../../docs/platform-scale.md)。

独立专项保留为回归工具，清单见 [独立专项闭环](../../docs/independent-closures.md)。本入口不会调用专项验收报告来替代自身的报文或路由证据。最新整体结论看 `reports/platform-runtime.json` 的 `success` 和 `full_protocol_integration`；两者都为 `true` 才表示本轮全部集成验收通过。

历史四节点运行 `sf-unified-52aedb8dcc` 的记录见 [历史交付记录](../../reports/platform-delivery.md)，不能代替扩容后的验收。新运行按规模保留在 `reports/platforms/leo-<卫星>-<网关>/latest.json`，最新一次尝试也写入 `reports/platform-runtime.json`；失败仍如实保留。

历史逻辑模式中，120 星＋4 网关通过，44 项检查、840 份 PCAP，持续 UE 丢包率 2.49%；360 星配置因额外内存预算 18.38 GiB 高于当时可用 12.45 GiB 而拒绝部署。历史证据见 [大星座交付记录](../../reports/platform-scale-delivery.md)，当前物理模式看各规模最新报告。

```bash
# 同一平台的两种规模；结束自动清理本轮资源，证据保留
make test-unified SATELLITES=120 GATEWAYS=4 PLANES=12 FLOWS=8
make test-unified SATELLITES=360 GATEWAYS=8 PLANES=18 FLOWS=16

# 直接编辑并使用 JSON 配置
make test-unified CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json

# 查看整体结论和各项检查
python3 -m json.tool reports/platform-runtime.json

# 不创建运行资源，只预览逐节点真实部署配置
make platform-configuration CONSTELLATION_CONFIG=scenarios/constellations/leo-360.config.json
```

需要 Linux、Docker、MPLS/SRv6 内核能力，以及已缓存的云平台、5G 和 NOC 镜像。`make bootstrap-cloud-tools` 准备固定版本 kind/Cilium；5G 父入口负责验证其固定版本依赖。平台会临时提高不足的 inotify 额度，把原值记在本轮 `host-budget.json` 中，结束时恢复。遇到已有同名、仍活动的 5G 栈会拒绝启动，以免误删其他运行。当前共享存储使用单机 hostPath，属于软件在环环境。

| 技术 | 在同一平台中的实际作用 | 必须具备的本轮证据 |
|---|---|---|
| OSPFv2 | IPv4 星地底层路由，承载两端网关的 IP-in-IP N3 传输 | 邻接、选中的 OSPF 路由、内含原 N3 GTP-U 的报文与断链恢复 |
| OSPFv3 | 发布 IPv6 SID 可达性，为 SRv6 外层提供路由 | IPv6 邻接、SID 路由、真实 SRH/GTP-U 报文 |
| LDP | 为星地传输环回地址分配和交换标签 | 真实 LDP 绑定、内核 LFIB、同包 MPLS/GTP-U 解码 |
| SR-MPLS | 根据同一控制器计划在网关施加 Prefix SID 标签栈 | 主备计划、实际标签栈、断链后新路径上的 GTP-U |
| BGP-LS | 配置中选定的 N3 服务源网关输出全图 IS-IS TE 数据库，接收网关与 PCE 使用实时链路集合校验路径 | 全图接收端 NLRI、物理断链后的撤销与恢复、PCE 使用的图 |
| PCEP | PCC 请求动态 SR 路径；平台 PCE 返回来自同一计划且通过 BGP-LS 校验的正向 SR-ERO | PCReq/PCRep/PCRpt、Active 动态策略、已安装的绑定 SID、实际业务标签报文 |
| SRv6 | 网关封装原有 IPv4 GTP-U，卫星执行 End，出口以 End.DX4 交给原 gNB/UPF 接口 | SRH、原始双向 GTP-U、实际 SID 路由和故障恢复 |
| EVPN/VXLAN | 两端网关交换 VNI 100 的 SVI MAC/IP，作为 N3 业务虚拟转接网段 | Type-2 路由、同包 VXLAN/GTP-U 解码及断链恢复 |
| Kubernetes / Helm | 部署真实 FRR adapter 控制器，通过有认证、限定节点和命令的执行通道管理路由 | Deployment、Secret 引用、实际路由操作日志和 committed 计划 |
| Lease HA | 三副本共享持久状态，只有 Lease 主实例可以提交；删除主实例后由新主接续 | Lease 转移、从副本拒绝修改、状态一致、接任后的真实提交与 UE 报文 |
| Cilium / Hubble | 管理上述实际控制器的访问策略，并观测允许/拒绝的 API 流量 | 对实际 controller 的策略阻断与恢复、对应 Hubble 记录；阻断期间数据面继续转发 |
| 星上自治 / 轨道切换 / NOC | 复用同一 PDU 会话和星地节点执行接触切换、云端全停、星上备用路由及重连恢复 | 同轮 PCAP、内核路由、心跳状态、Prometheus/Loki/Tempo/Grafana 查询 |

控制器提交普通业务路由，平台在网关的独立策略表中选择承载方式。不同模式依次运行于同一拓扑和会话；没有要求每个包同时套用全部封装。BGP-LS 本身是拓扑分发协议，PCEP 本身是控制协议，它们的接入证据必须连到实际业务路径。Hubble 观测的是云内控制流量；FRR 网络命名空间里的 5G 数据报文由 PCAP 证明。

当前固定 FRR 版本在重复断链时可能出现 IS-IS TED 已更新、BGP-LS 仍保留旧 NLRI 的情况。平台检测三秒内未同步时，保存旧 RIB/TED，重建本轮网关的 BGP 导出实例，再等待接收端真实更新。这也会短暂重新建立该网关的 EVPN 会话；时间线用 `bgpls_exporter_resynchronizing` / `bgpls_exporter_resynchronized` 明确记录，不宣称无损切换。

扩容后的诊断还发现过标签下一跳残留与实际 MPLS 环路。因此先准备云平台，再建立星座，降低镜像安装引起的邻接抖动。SR-MPLS/PCEP 承载启用前检查全节点相关 Prefix SID 的已安装下一跳是否符合当前图的最短路；持续不一致时，记录旧 LFIB，经实际 FRR 撤销并重新发布相关 Prefix SID，等待全图标签撤销与重新收敛，随后再核验报文。不会通过手写内核标签表绕过协议。相关证据为 `sr-lfib-qualification-*.json` 与 `sr_prefixes_resynchronizing` 时间线事件。

恢复物理接口后，平台先确认该链路两端的 LDP 邻接，并要求网关间双向真实探测连续三轮成功，才把恢复的接触发布给控制器。每次探测结果与耗时写入 `*-underlay-recovery.json`。持续 UE 流量覆盖这一等待过程；整体丢包仍须低于 5%，每阶段的 20 包探测不能代替连续流量门禁。

本轮原始证据归档在报告 `artifacts` 指向的目录：`scenario.json`、`deployment-manifest.json`、全节点 inventory/探测、`timeline.jsonl`、各次提交、`frr-agent.jsonl`、每种模式覆盖全部配置卫星的 PCAP、`protocol-packet-proofs.json`、PCEP 消息、路由快照、`cloud/` 和 `noc/`。逐包解码在同一个报文中匹配封装与原 N3 两端地址及 GTP-U，并按初始、断链恢复、接口恢复三个时间窗口分别核验。报告分别列出捕获点数量和实际经过业务的卫星。

`make test-unified` 现在直接创建配置数量的 FRR；`make test-constellation` 保留为 memory adapter 回归。前者在资源不足时明确失败，不降级成后者。配置数量必须与 `deployed_counts` 对照，配置预览不算运行通过。当前 PCE 是最多 32 层 SID 的有限 SR 请求/响应实现；共享 hostPath、采样信道和单机软件卫星不替代生产云存储、长时星座运行或飞行硬件验收。

轨道边界：默认物理模式为每颗卫星传播轨道，并按地理站点、遮挡、距离、终端和捕获条件产生全图连接；时延、容量和配置丢包进入实际内核队列。默认输入是设计示例，参考 SNR 模型需要任务数据校准。协议回归保持初始快照，随后运行 1:1 物理回放，最后在结束快照验证自治。运行仍共用一套 5G 会话，不代表每颗卫星配有独立 gNB/UE；Doppler 元数据不等于 RF 波形处理。

协议配置依据：[FRR pathd](https://docs.frrouting.org/en/latest/pathd.html)、[FRR LDP](https://docs.frrouting.org/en/latest/ldpd.html)、[RFC 8664 SR-ERO](https://www.rfc-editor.org/rfc/rfc8664.html)。最终是否成立由本工程实际运行证据判定。
