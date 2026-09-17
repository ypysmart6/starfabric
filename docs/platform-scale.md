> 当前默认入口已切换到逐星物理模式，详见 [物理星座说明](physical-constellation.md)。本页原有 120 星通过和 360 星内存不足记录属于历史逻辑图运行，不能用作新物理模式的通过证据。节点数量保持配置驱动，连接数量和参数现在由物理输入计算。

大星座配置直接驱动统一平台

120 星与 360 星使用同一个 `make test-unified` 入口、同一个星座生成器、同一套 FRR/5G/协议/云/自治/NOC 验收。修改配置会改变实际创建的节点和连接，不再只改变 memory adapter 中的图。运行结果以逐轮原始证据为准。

历史逻辑模式实测：120 星＋4 网关通过，44 项检查有效，持续 UE 丢包率 2.49%；360 星＋8 网关因当时可用内存不足未部署。详细记录见 [历史交付记录](../reports/platform-scale-delivery.md)。当前物理模式以各规模最新报告为准。

```bash
# 数值参数
make test-unified SATELLITES=120 GATEWAYS=4 PLANES=12 FLOWS=8
make test-unified SATELLITES=360 GATEWAYS=8 PLANES=18 FLOWS=16

# 或使用可编辑 JSON 文件
make test-unified CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json
make test-unified CONSTELLATION_CONFIG=scenarios/constellations/leo-360.config.json

# 只生成逐节点 FRR 配置和部署清单，不启动容器
make platform-configuration CONSTELLATION_CONFIG=scenarios/constellations/leo-360.config.json \
  PLATFORM_DIR=reports/platform-config/360-8
```

数值参数与 JSON 选择一种：设置 `CONSTELLATION_CONFIG` 后，Make 完全使用文件中的节点、分面和意图参数，不叠加数值变量的默认值。`PLANES=0` 自动分面；显式面数必须整除卫星数。`FLOWS=0` 默认为网关数两倍。物理模式使用 `PHYSICS_CONFIG` 中的轨道、站点、终端和信道参数，替换逻辑种子的上联、容量、时延与连接。详细参数见 [星座配置](constellation.md)和[物理配置](physical-constellation.md)。

| 默认物理配置 | FRR 卫星 | FRR 网关 | FRR 总数 | 时间范围内可能连接 / 初始有效连接 | Rust 星上进程 | Kubernetes 节点 / 控制器副本 |
|---|---:|---:|---:|---:|---:|---:|
| 120 星 / 12 面 / 4 网关 | 120 | 4 | 124 | 252 / 250 | 120 | 3 / 3 |
| 360 星 / 18 面 / 8 网关 | 360 | 8 | 368 | 744 / 743 | 360 | 3 / 3 |

以上是要求部署的数量，失败运行可能只创建其中一部分。`deployed_counts` 记录实际创建数量。Kubernetes 节点、控制器副本、捕获辅助进程、5G 和 NOC 服务不算卫星。默认生成 8/16 条网关业务意图，统一平台另加双向 N3 意图；意图数不等于持续流发生器数量。

完整验收会创建资源、持续发包、主动注入链路与控制器故障、核验恢复，最后清理本轮资源。它目前不是验收后仍常驻的运营服务。证据会保留：

- `PLATFORM_DIR/deployment/deployment-manifest.json` 与逐节点 `frr.conf`：部署预览，标记 `preview_only`，不代表已运行。
- `reports/platform-runtime.json`：最近一次尝试，失败也更新。
- `reports/platforms/leo-<卫星>-<网关>/latest.json`：各规模最近一次尝试。
- 报告 `artifacts` 指向的唯一目录：输入与绑定场景、部署清单、资源预算、全节点邻接/探测、网关业务探测、真实路由操作、逐模式逐星 PCAP、PCEP、星上状态、云与 NOC 证据。

完整通过必须满足 `success=true`、`full_protocol_integration=true`、`source_unchanged=true`，以及配置与实际部署数量一致、全节点探测、每星自治、逐协议业务与恢复、同一 PDU、NOC 和资源清理等检查。运行中修改源码会判为失败，避免将新源码和旧进程的证据混为一轮。

全部节点真实运行 OSPFv2/v3、IS-IS、LDP、SR SID；全部网关参与 EVPN。两个配置选定的 N3 网关担任 PCC/VTEP/业务端点，PCE 使用同一 committed 计划与实际 BGP-LS 全图。各承载模式依次使用同一拓扑和同一 PDU，分别执行初始、断链恢复、接口恢复探测。每颗卫星均有捕获点，三个阶段各自的时间窗口必须包含对应封装内的双向 GTP-U。报告另列实际承载当前业务的卫星，不把所有捕获点都算成业务经过的节点。

SR-MPLS/PCEP 启用前还核验全节点相关 SID 的实际 FRR 标签下一跳，发现残留时通过 FRR 撤销、重新发布 Prefix SID 并重新检查。路由器数量的绑定上限为 7999，总体可运行规模受主机资源与协议限制；PCEP 协商的 SID 深度最多 32，超限明确拒绝，不截断路径。

所有配置节点还要通过真实底层 ping；所有配置网关意图都要向真实服务地址发包。服务目标放在不由 IGP 宣告的 `svc0`，其可达性依赖控制器的业务路由。每颗卫星启动独立 Rust 进程；云端全停后心跳超时安装备用内核路由，云端重连后撤销。持续 UE 业务覆盖故障过程，最低 300 包，丢包率门禁仍为低于 5%。各技术的完整角色见 [平台说明](../lab/platform/README.md)。

当前资源检查采用 `MemAvailable`，预算为每 FRR 节点 40 MiB，加 4 GiB 固定辅助预算：124 节点约额外 8.84 GiB，368 节点约额外 18.38 GiB。该估算不是峰值保证。资源不足时提前失败，明确保留需要量和可用量；不会自动换成内存模拟，也不能据此声称 360 星已跑通。入口所需 Docker、固定镜像、内核能力、kind/Cilium 工具见平台说明。

当前采用逐星 SGP4／TEME OEM 和地理站点计算采样连接，时延、容量与丢包配置进入实际内核报文队列。默认轨道与站点是可替换的设计示例，参考 SNR 容量模型和 IP 整形不等于逐链路 IQ 仿真。协议回归保持初始快照，随后运行 1:1 物理回放，自治在结束快照上验证。仍是一套 5G PDU 会话、单机 hostPath、有限 SR PCE；24 小时稳定性、多宿主机容错、飞行硬件及 [其他独立专项](independent-closures.md) 需要各自的运行证据。
