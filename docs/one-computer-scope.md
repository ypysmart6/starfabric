# 单电脑卫星网络工程范围

本工程的最终要求是原文全部非 AI 卫星技术形成统一单机软件闭环。软件靶机、虚拟集群、模拟器和构建系统均在范围内，不能因为依赖未安装而作为实物排除。当前实现、未闭环连接和原文性能预算按[全文单机闭环验收](single-pc-acceptance.md)执行；只有真实硬件性质由 HIL 合同界定。

## P0：产品级软件闭环

`make test-live` 是主验收路径：

```text
双向业务意图 → 路径计算 → FRR 实际静态 FIB → Ixia-c 双向包
       → 主链路双端故障 → 拓扑事件 → 备用路径/FIB → 丢包与收敛 SLO
```

验收同时比对 controller plan、FRR actual FIB 和 OTG packet metrics，任意一层不一致即失败。`make check` 仍保留为无 Docker 的快速逻辑/数字孪生门禁，不再代表真实数据面闭环。

## P1：主线与外部集成

核心产品只有一条主线：时变拓扑→意图/TE→设备下发→实际状态→包验证→回滚/恢复。轨道/contact plan、inventory、OpenConfig、Kubernetes/NOC、星上 runtime 都以输入、adapter 或部署面接入这条主线，不再将独立 demo 等同于生产完成。

`make test-orbit-live` 现在把 TLE/SGP4、CCSDS OEM、可见性、range/delay/Doppler 和接触窗直接接入 FRR/Ixia-c 故障闭环。`make test-protocol-live` 验证 OSPFv2/v3、LDP、SR-MPLS 的真实邻接、FIB/LFIB、MPLS 报文、主备切换和恢复。`make test-advanced-live` 进一步验证 SRv6 SRH/End 数据面、BGP-LS Node/Link/Prefix NLRI 的故障撤销/恢复、PCEP OPEN/KEEPALIVE/PCReq/PCRep，以及 EVPN Type-2 到 Linux VXLAN FDB 和 UDP/4789 报文。

`make test-5g` 是单机 5G 纵向门禁：Open5GS＋UERANSIM 实际建立 N2/NGAP、PFCP、UE 注册、PDU session 和双向 N3/GTP-U；随后在活跃 PDU 会话上注入 40/80 ms 星上传输时延并检查 PCAP/SLO。这解决“5G 只有配置骨架”，但不把 UERANSIM 称为真实 NTN 射频。

## 5G NTN 是否属于本工程

属于。它是卫星通信系统的业务接入与服务集成纵向，不是另一个与卫星网络无关的项目：

```text
UE/仿真器 → NG-RAN → N2/N3 → StarFabric 时变星地传输 → 5GC/UPF → 应用
```

但必须分开两层证据：单机可以真实验证 5G 协议和 IP/GTP-U 数据面；Release 17 NTN 的 SIB19、扩展定时器、HARQ 策略、变时延/Doppler 补偿需要 srsRAN＋合格信道仿真器或 HIL 证据。

## P2：没有星载、射频和硬件

这不是当前工程的缺陷，而是必须如实标记的边界。一台电脑可以交付：

- 星上 Rust runtime 的本机 SIL、断联自治、watchdog、持久化和签名 A/B 升级逻辑；
- ARM64 交叉编译、QEMU/Buildroot/Yocto 入口与故障重放；
- modem/OISL/antenna 输入的版本化接口、超时/失效语义、录制回放和 HIL 验收指标；
- FRR/SONiC/BMv2/Linux 软件数据面的功能与故障验证。

一台电脑不能交付：相控阵标定、真实 RF/baseband、modem/DSP/FPGA 时序与资源验证、OISL PAT/光机电、飞行 BSP/Secure Boot 或 ASIC 线速。本工程对它们的交付物是可执行的边界与 HIL 验收合同，而不是伪造“已实现”。详细指标见 `docs/hil/rf-phy-acceptance.md`。

单机总验收入口是 `make acceptance-single-pc`。它依次跑逻辑/回归门禁、控制器＋FRR＋OTG 包闭环、轨道输入闭环、OSPF/MPLS 协议闭环、SRv6/PCEP/BGP-LS/EVPN-VXLAN 高级协议闭环、Open5GS＋UERANSIM＋GTP-U 及星上传输时延实验。
