# 整体闭环仍缺的系统

轨道预测 → FRR 实际 FIB → Open5GS/UERANSIM 业务 → Rust 断联自治 →
地面恢复 → NOC 后端关联，已形成同一运行编号下的单机业务闭环。
历史逻辑模式曾完成 120 星＋4 网关统一验收，360 星因当时可用内存不足未部署，见 [历史交付记录](../reports/platform-scale-delivery.md)。当前默认入口已接入逐星轨道、地理站点、动态物理连接和实际报文整形，详见 [物理星座说明](physical-constellation.md)；各规模最新实际结果由 `reports/platforms/` 下的报告记录。运行仍使用一套持续 UE 会话和本机管理心跳中继。这些实现与结果不代表历史设计全文已经完成。

仍需完成以下 **8 组软件系统或验收能力**。以
[`single-pc-closure.json`](single-pc-closure.json) 的逐项门禁为机器可读准则。

| 系统 | 当前仍缺的闭环 |
|---|---|
| 多厂商网络验证 | KNE、Ondatra、虚拟 DUT/ATE、SONiC 靶机及完整创建、测试、清理生命周期 |
| Release 17 NTN 接入 | 外部软件 UE 经真实 IQ 信道处理时延与 Doppler、解码 SIB19、完成接入和端到端业务；内部 test UE 不替代此项 |
| O-RAN 控制管理 | Near-RT RIC、E2AP、E2SM-KPM/RC 的指标订阅与控制反馈，以及 O1 软件管理链路 |
| 云平台交付 | Argo CD 从实际 Git 仓库同步、漂移修复、版本回退，以及 HA 滚动升级；已有模板不能代替运行验收 |
| 星载系统构建 | Buildroot 与 Yocto 真正生成并启动包含星载程序的镜像，以及 KVM 启动；已有 QEMU TCG 证据单独保留 |
| 可编程交换 | SONiC、SAI、软件交换 SDK 与带内遥测的配置、转发、读回和故障验证 |
| 高速数据面 | AF_XDP、DPDK、NUMA 与 hugepage 的真实软件包处理、回退和性能证据；已有 XDP/eBPF 计数不替代它们 |
| 全文规模与稳定性 | 节点部署规模已有 120 星＋4 网关实测；仍缺 100 条持续业务、每秒拓扑变化、算路 P95 < 200 ms、拓扑到 FIB P95 < 2 s、24 小时稳定运行，以及各延迟分位数、丢包、检测/恢复、震荡、利用率、CPU/内存、FIB 不一致时长及分布式/中央策略同输入对照 |

主机扩容后，2026-09-14 的 Kubernetes/Cilium/Hubble 三节点运行、Helm 单副本
升级回退、Lease 接管、网络策略与 Hubble 报文检查已通过，见
`reports/cloud-runtime.json`。Argo CD 与 HA 滚动升级仍是上表中的待完成项。
当前主机没有暴露 `/dev/kvm`；KVM 和完整 Yocto/Buildroot 构建仍需独立运行证据，
磁盘空间恢复不等于这些项目已经通过。

扩展的统一协议/云平台运行结果见 `reports/platform-runtime.json`，入口为
`make test-unified`，范围见 [统一业务接入说明](unified-integration.md)。原基础运行的
`reports/unified-runtime.json` 与独立云专项的 `reports/cloud-runtime.json` 分别保留，
不能替代新平台的同轮检查。总验收由 `make acceptance-single-pc` 重新执行全部阶段，
只使用该轮新生成、带哈希的报告与原始证据；只要上述任一项仍缺失，总结论就保持未完成。
