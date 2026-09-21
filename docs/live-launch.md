# 实时平台的启动前选择

先运行 `python3 frontend/server.py`，打开 <http://127.0.0.1:8090/?mode=live>，点击
**配置并启动**，或进入侧栏的 **启动配置**。选择后点击 **按所选配置启动**。
表单修改会保存在当前浏览器中；后台刷新不会重置未提交的选择。

| 选择 | 实际作用 |
|---|---|
| IS-IS / OSPF | IS-IS 双栈，或 OSPFv2 + OSPFv3；控制实际 FRR 进程、初始和新增链路配置、邻居检查 |
| 业务承载（可多选） | 原生 IPv4、SRv6、EVPN + SRv6、LDP、SR-MPLS、PCEP / SR-MPLS、EVPN + VXLAN；依次分配给 8 类业务 |
| 路径算法 | latency、hops、capacity、reliability；写入实际控制器意图，IGP 自身仍运行 SPF |
| 星座规模 | 120 星 / 24 网关、120 星 / 4 网关、60 星 / 4 网关或自定义卫星、网关、轨道面数量 |
| 业务对数 | 8 类业务 × 每类对数；各对有正反两个控制器意图 |
| 采样间隔 | 实时轨道及拓扑的目标采样间隔；实际下发耗时另行记录 |

当前默认选择为 **IS-IS + EVPN/SRv6、时延优先、120 星 / 24 网关、16 对业务**。
这是启动配置，**不是已通过稳定性验收的容量承诺**。实时地址池要求卫星与网关合计
不超过 255，站点输入最多 24 个网关，卫星数量必须能被轨道面数整除。
1000 星暂不能通过本入口启动；需要先扩展地址、资源与编排能力。
SR-MPLS / PCEP 的现有实现依赖 IS-IS 分发 Prefix-SID，选择 OSPF 时必须取消这两项。

运行中的配置固定。停止并完成清理后，可以更换配置再启动。
另一个页面或 CLI 持有服务锁时，携带新配置的启动请求返回 409，不覆盖现有运行。

## EVPN + SRv6 的实现范围

实时编排的 `evpn-srv6` 包含三条实际数据链路：

| 业务 | 控制面与转发 |
|---|---|
| 三层 TCP/UDP | Type-5 发布 IPv4 /32 与 End.DX4 SID，控制器选择显式路径 |
| 二层透明以太网 | Type-2 学习 MAC，Type-3 发布参与网关；SRH next-header 143 携带原始以太网帧，End.DX2 接入对应网桥 |
| 5G N3 | 原 gNB / UPF 的 /32 通过 Type-5 发布独立 End.DX4 SID；原 GTP-U 包在 SRv6 内跨越卫星，UE 会话保持由 5G 管理 |

三层路径：

1. 网关创建实际业务端点与本地解封装 SID。
2. 每个参与网关的网络命名空间运行独立的实验性 iBGP speaker，沿卫星 IPv6 网络建立会话。
3. 通告 EVPN Type-5 前缀、RD、RT 65000:100，以及 BGP Prefix-SID 属性中的 SRv6 L3 Service TLV。
4. 入口从收到的 EVPN 路由读取 Service SID，将它接到控制器计算的段列表末尾，安装 Linux `seg6` 路由。
5. 出口以 `End.DX4` 解封装；业务收发计数及实际卫星接口抓包验证转发。
6. 会话断开、路由撤销或服务属性无效时，撤回该路由；网关独立任务将对应业务表设为
   `unreachable`，不依赖下一次集中式算路来阻断原生 IP 回退。会话恢复后重新通告，下一次业务更新重新下发。

该实现使用 [RFC 9136 Type-5](https://www.rfc-editor.org/rfc/rfc9136.html) 和
[RFC 9252 §6.5 的 Service SID 编码](https://www.rfc-editor.org/rfc/rfc9252.html#section-6.5)。
实验 speaker 使用 TCP **1179**，与 FRR 原有的 BGP-LS / EVPN-VXLAN 会话分开，允许承载组合并存。
它只接收本轮已配置对端和已配置业务前缀，不承担通用 BGP 路由器或路由反射器的功能。

二层路径为每个网关创建真实 CE 网络命名空间、独立 EVI 网桥和解封装 SID。
已知远端 MAC 使用 Type-2 中收到的 SID；广播、未知单播、多播按照 Type-3 成员入口复制。
来自远端的解封装帧不会重新进入覆盖网络，避免环路。MAC 有 300 秒老化和移动序号；
会话失效撤回该对端的 MAC 与复制成员。不同 EVI 使用不同 RT、网桥和 SID，可使用重复 IP 地址。
默认 EVI 201；编排文件的 `evpn_l2_evis` 可以指定多个 EVI，小拓扑验收使用 201 / 202。
每个 CE 持续向下一站发送 IPv4 / IPv6 探测，结果在 `l2-metrics-*.json`。

这是**单归属站点**实现。Type-3 采用入口复制到 End.DX2 附着网桥，
没有宣称实现 RFC 9252 推荐的 End.DT2M / ESI 多归属过滤。
入口以太网封装在用户态执行，卫星转发与出口解封装由 Linux 内核完成，不能将其吞吐量等同于硬件转发。
三层仍为一个 RT 65000:100；未实现多租户三层 VRF、RR、SID transposition 或厂商互操作认证。

选择 EVPN + SRv6 而未选 EVPN + VXLAN 时，不创建 VXLAN / VNI / br100。
不选 MPLS 类承载时，不启用 LDP、MPLS 或 BGP-LS；SRv6 的可用性不再以 BGP-LS 收敛为前提。
所选实时配置只保留实际业务意图与 N3 意图，去掉生成器仅用于探测的额外网关意图，避免为未启动的业务预留容量和要求备用路径。

选择 EVPN + SRv6 时，**5G N3 同时使用 EVPN/SRv6**，独立使用业务表 300 和服务 SID。
缺少 EVPN 路由时阻断，不回退原生 IP。没有选择 EVPN/SRv6 时保留原生 N3。
卫星路由控制器控制卫星和网关的转发；UE、gNB、UPF 的生命周期与接入会话由 5G 编排管理。

实际动态调度使用第三个网关终端提前获取即将离开的上联的后继链路；仍遵守原终端数量、当前可见性与捕获时间。控制器实时入口每批并行 16 台设备，批后读回与失败回滚继续执行。

SRv6 入口路由绑定稳定的 `sr0`，外层目的 SID 由 IPv6 IGP 解析，物理上联切换不会直接删除入口业务路由。控制器继续计算段列表；底层修复期间仍须以实际业务计数确认连续性。
FRR 执行器使用本轮专用 Unix socket 和固定路由器进程身份，直接进入对应命名空间执行受限的路由读写与探测，减少逐条 `docker exec` 的开销。
5G 编排在云控及卫星节点启动期间持续发送单独记录的基线保活；切换 N3 前检查 PDU 身份和实际连通。基线流量不作为卫星承载证据。

gNB 使用固定 UERANSIM v3.2.6 源码构建的修复镜像。该版本原来的 `SIGNAL_LOST`
只记录日志，没有通知 RRC，导致无线丢失后旧上下文滞留、拒绝重新接入。
`ntn/single-pc/ueransim-rlf.patch` 将该事件连接到已有的 RRC / NGAP 释放流程。
对于仅 UE 检测到失联的情况，新接入请求会等待旧 NGAP 上下文释放完成，再继续建立无线连接；
没有延长无线定时器，也不靠重启 UE 或重建 PDU 恢复。
首次启动需要联网获取固定提交、安装编译依赖并构建 gNB；之后按源码、补丁和 Dockerfile
摘要复用镜像。原始 UE 镜像不变，修复镜像内含对应完整源码和许可证，构建来源记录在
`reports/ueransim-gnb-build.json`。独立 8 秒无线断链测试已验证上行约 4.1 秒、下行约 6.2 秒
恢复，两次故障前后为同一 PDU，证据见 `reports/evpn-srv6-complete/radio-recovery-r3/`；
这份组件报告与全星座报告分别保存。

## 配置与证据

每次启动在 `reports/live/launches/<唯一编号>/` 保存独立的
`launch.json`、`constellation.json`、`physical.json`、`workloads.json`。
后台子进程继承这些路径。运行目录另存 `launch-config.json`，状态接口返回 `launch_config`。
修改仓库源 JSON 不会改变已经提交的那一轮启动输入。

接口为 `GET /api/runtime/options`、`POST /api/runtime/validate`、`POST /api/runtime/start`。
后两者的请求体是 `{"configuration": {...}}`，仅接受同源 JSON；不接收命令或任意文件路径。
CLI 可使用 `python3 lab/live/service.py start --config /path/to/launch.json`。
不带配置的 `make live-start` / CLI 启动仍保留旧环境变量入口，以便复现旧验收结果。

本地验收记录：

| 检查 | 结果 |
|---|---|
| 启动表单 | 真实 Chromium 验证选择、轮询保持、校验与提交；生命周期替身，不实际启动整个平台 |
| IS-IS + EVPN/SRv6 | 4 网关 + 1 卫星，全部 BGP 对端、Type-2/3/5，二层 IPv4/IPv6、重复 IP 隔离、SRH 抓包、撤路阻断和 BGP 重启恢复通过 |
| OSPFv2/v3 + EVPN/SRv6 | 同一实包检查通过，底层实际换为 OSPF |
| 120 星 / 24 网关稳定性 | 24 对三层业务、48 路二层双栈和 N3 已通过约 603 秒观察；见 `reports/evpn-srv6-complete/revision3/README.md`。该轮未通过 10 秒更新目标；`revision4/`、`revision5/` 因 gNB 无线恢复缺陷启动失败，保留失败证据。后续完整复测记录在 `revision6/`，结果以该轮报告为准 |

原始证据在 [IS-IS 检查](../reports/evpn-srv6-complete/revision2/isis/result.json) 和
[OSPF 检查](../reports/evpn-srv6-complete/revision2/ospf/result.json)，同目录包含 PCAP、解码报文及路由配置。
两轮检查结束均已清理所创建的容器。

复现命令：

```bash
python3 -m unittest lab.live.test_launch frontend.test_server frontend.test_launch_browser
python3 lab/live/check_evpn_srv6.py --igp isis --output reports/evpn-srv6-complete/isis
python3 lab/live/check_evpn_srv6.py --igp ospf --output reports/evpn-srv6-complete/ospf
```

实包检查需要 Docker 权限，浏览器检查需要 Chromium 与 `websocket-client`。
底层路由可达并不等于动态星座稳定；仍需对新增链路、接触切换、网关上联分配和实际下发延迟做全规模验收。

对已启动的 120 星 / 24 网关、24 对业务、仅 EVPN/SRv6 的测试实例，可使用完整验收入口：

```bash
python3 lab/live/check_evpn_complete.py --run-id <本轮运行标识> --output reports/evpn-srv6-complete/<新目录>
```

该入口并行验证 600 秒三层和二层/N3 稳定性，通过后主动停止并恢复一个本轮 EVPN 实例，检查撤路阻断、恢复与 PDU 身份；再分别阻断无线的上行和下行各 8 秒，验证同一星座上的 N3 恢复及原 PDU 保持。每项故障均要求 60 秒内恢复。结束后自动停止、清理绑定的测试实例。结果分别保存在 `full-scale/`、`services/`、`faults/`、`radio-faults/` 和汇总 `result.json`，实时更新指标单独判定。
