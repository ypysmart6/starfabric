> 当前默认入口已切换到逐星物理模式，详见 [物理星座说明](physical-constellation.md)。本页原有 120 星通过和 360 星内存不足记录属于历史逻辑图运行，不能用作新物理模式的通过证据。节点数量保持配置驱动，连接数量和参数现在由物理输入计算。

当前统一业务接入范围

统一平台入口是 `make test-unified`，等同于 `make test-platform`。星座配置决定全部实际 FRR 节点与连接，默认 **120 星＋4 网关，共 124 个 FRR 节点**；360 星示例为 **360 星＋8 网关，共 368 个 FRR 节点**。另外建立 **3 个 Kubernetes 节点、3 个控制器副本**，以及 5G 和 NOC 服务。数量是部署要求，实际完成量及验收结果看本轮报告。云节点和服务容器不计为卫星。

本入口建立一套 Open5GS/UERANSIM PDU 会话。协议承载模式依次运行在相同的卫星/网关网络命名空间，始终使用原 gNB/UPF 端点与 UE 会话。云中的控制器通过 FRR adapter 实际写入配置图中计划涉及的节点。检查同一运行 ID、计划、路由、业务报文及故障恢复，不能用独立专项报告拼出统一通过。

原四节点完整运行的 [历史交付记录](../reports/platform-delivery.md) 不代替本次大星座验收。新入口、资源需求、报告字段和完整使用方式见 [大星座直接驱动](platform-scale.md)。

**如何使用**

```bash
# 完整集成验收，结束自动清理本轮资源并保留证据
make test-unified

# 查看本次总结果与每个技术点的检查
python3 -m json.tool reports/platform-runtime.json

# 原四节点基础业务回归，不作为完整协议/云集成验收
make test-unified-base
```

环境依赖、协议角色、证据位置和限制见 [统一平台运行说明](../lab/platform/README.md)。`reports/platform-runtime.json` 的 `success` 和 `full_protocol_integration` 必须都为 `true`；失败运行保持失败。总单机验收的 `unified` 阶段也改为该入口，其 `p6-unified-system` 门禁要求各协议、云平台和原基础业务检查同时通过。

**同一业务运行中的技术及证据**

| 技术 | 在共享平台中的连接 |
|---|---|
| OSPFv2 / OSPFv3 | 同一星地节点的 IPv4 / IPv6 路由；IPv4 网关传输和 IPv6 SRv6 SID 可达性 |
| IS-IS / LDP / SR-MPLS | 同一链路的拓扑与标签控制；根据同一控制器计划承载原 N3 GTP-U，验证实际标签和断链恢复 |
| BGP-LS / PCEP | 接收真实 IS-IS TE 节点/链路 NLRI；PCE 用该图校验控制器计划，向 FRR PCC 返回正向 SR-ERO，安装动态 SR 策略并承载实际业务 |
| SRv6 | 原有 GTP-U 经网关 SRH 封装、卫星 End 和出口 End.DX4 转发，验证双向同包解码 |
| EVPN / VXLAN | 原网关作为 VTEP，交换 Type-2 MAC/IP 路由，在 VNI 100 上承载原 N3 业务 |
| Kubernetes / Helm | Helm 部署真实 FRR adapter 控制器；认证执行通道限定本轮节点和路由/探测命令 |
| Lease HA | 三节点三副本，只有主实例可提交；删除主实例，验证新主接续、共享状态、真实路由及 UE 业务 |
| Cilium / Hubble | 对上述实际控制器应用策略，观测允许和拒绝流量；策略阻断期间数据面继续转发 |
| TLE / SGP4 / CCSDS OEM / 接触窗口 | 每星独立传播，地理站点参与仰角和距离计算；全图遮挡、距离、终端与捕获条件决定动态连接，时延／容量进入内核队列 |
| 版本拓扑 / 意图 / 主备计划 / 持久化恢复 | 同一 Go controller 计划和提交，路由写入真实 FRR，并关联计划编号和故障时间线 |
| Open5GS / UERANSIM / N2 / PFCP / PDU / GTP-U | 原 5G 栈建立真实 PDU 会话；原 N3 端点业务经过上述承载模式和故障，比较前后会话身份 |
| Rust 心跳超时 / 星上自治 / 地面恢复 | 云端全部停止后自行超时，星上写入真实备用内核路由；云端恢复并重连后撤销备用 |
| Prometheus / OTel / Loki / Tempo / Grafana | 查询同一实际控制器的指标、日志、追踪、计划和断联告警，归档本轮后端证据 |
| 报文 / FIB / 生命周期 | 全部配置卫星设置捕获点，逐阶段检查封装内双向 GTP-U，另列实际经过业务的卫星；全节点探测、持续 UE 流量、路由、时间线与清理 |

协议分工不同，不要求每个包同时叠加全部封装。BGP-LS/PCEP 的控制信息必须落到实际业务；Hubble 证明云内控制流量，卫星数据面由 PCAP 证明。固定 FRR 版本的重复断链 BGP-LS 导出滞后，会触发记录在案的导出实例重建，并等待真实接收端更新；不把旧 RIB 当作已撤销，也不宣称无损切换。

**独立专项单独列示**

完整清单及逐项命令见 [独立专项闭环清单](independent-closures.md)。OSPF/MPLS、高级协议和云专项入口仍保留为回归工具；统一接入结论看新平台本轮证据。OTG/Ixia-c、BFD/LFA 专项、OpenConfig、台账导入、Batfish、NTN IQ、srsRAN、QoS/切片映射、P4/XDP、ARM64/QEMU、签名升级等，没有相应同轮业务证据时仍属于独立专项。

原 `lab/unified/run.py` 是基础 FRR/5G/轨道/自治/NOC 运行，报告为 `reports/unified-runtime.json`，现在由 `make test-unified-base` 单独保留。历史文档中的基础“统一”结论不能追溯性地当成新增协议已集成的证据。

**自行配置 120 星 / 360 星**

```bash
make test-unified SATELLITES=120 GATEWAYS=4 PLANES=12
make test-unified SATELLITES=360 GATEWAYS=8 PLANES=18
make test-unified CONSTELLATION_CONFIG=scenarios/constellations/leo-360.config.json
```

上述要求部署 124 或 368 个真实 FRR 节点，并为每颗配置卫星启动 Rust 进程；资源不足明确失败，不能宣称部署通过。物理输入见 [物理星座说明](physical-constellation.md)，可选逻辑内存回放见 [星座配置](constellation.md)。`FLOWS` 是意图数量，统一入口逐条进行真实网关探测，但不等于持续流发生器数量。

接口、接触选择、星上进程、捕获点与预算已由配置驱动，目标规模仍必须实际验收。默认物理模式先保持初始快照验证协议和云故障，再按 1:1 时间回放物理变化，最后在结束快照验证自治。当前共享 hostPath、有限 SR PCE、采样信道和单机软件卫星不替代生产存储、24 小时运行或飞行硬件；全文待完成能力见 [remaining-systems.md](remaining-systems.md)。
