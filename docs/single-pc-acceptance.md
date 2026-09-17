# 全文单机闭环验收

用户要求的最终交付范围是：历史设计文档中的全部卫星相关非 AI 技术，凡能用软件在一台电脑实现，均进入统一系统并完成运行验收。历史里程碑只决定实施顺序，不构成最终排除项。地面 Go、星上 Rust、Python 编排沿用当前架构。

只有依赖真实器件才能建立的 RF/光机电/ASIC/FPGA/BSP 等物理性质可以排除。Kubernetes、KNE、Ondatra、O-RAN 软件、SONiC 虚拟交换、Buildroot、Yocto、AF_XDP 和 DPDK 不能因为尚未安装而改称“必须实物”。KVM 需要宿主暴露虚拟化支持；TCG 通过不能替代 KVM 通过。

## 唯一总验收入口

```bash
make acceptance-plan       # 只显示真实命令和报告生产关系
make acceptance-single-pc  # 顺序运行所有已接入阶段，并强制检查完整闭环清单
make acceptance-status    # 查看当前/最近一次运行
make closure              # 复核最近一次完整运行的快照和当前源码
```

所有实际容器/网络操作由各实验已有的隔离和清理逻辑执行。总入口不自动安装依赖，也不把失败降级为通过。独立阶段失败后继续收集其他阶段结果；有依赖的阶段标记为 blocked。

输出位于 `reports/runs/<UTC时间-唯一ID>/`：

- `run.json`：阶段命令、开始/结束时间、退出码、错误、源码哈希、范围和全系统结论。
- `logs/`：每个命令的完整标准输出/错误及哈希。
- `evidence/`：该阶段刚生成的报告快照及哈希。
- `artifacts/`：统一运行与云平台阶段本轮产生的原始 PCAP、FIB、日志、配置和后端查询结果，以及每个文件的哈希。
- `closure.json`：逐能力门禁与未闭环软件项。
- `report.html`：可直接打开的统一验收报告。

报告必须由本次成功阶段重新生成；仅存在旧文件、仅命令返回零、JSON 字符串 `"false"`、报告哈希不匹配、运行中源码变化均不能通过。`make closure-audit` 仅审计历史报告，不证明当前运行通过。

`make acceptance-core` 提供不依赖 Docker 的核心回归，报告明确标记 `profile=core`、`system_closed=false`。它包括 Go 构建/单元/race、Python、API、确定性场景；实际 FRR FIB、无线/5GC、嵌入式和云平台仍由完整运行验收。

## 组件通过与统一系统通过

完整成功必须同时满足三个层次：

1. 每个非硬件能力都有通过的运行门禁，且来自当前运行。
2. 轨道窗口、控制器、FRR/OpenConfig 实际 FIB、5G 用户面、星上断联自治和 NOC 共享场景标识、拓扑/计划版本、故障时间线，真实业务经历故障切换和恢复。独立实验报告的汇总不能代替这条连接。
3. 原文性能与可靠性预算有直接证据：24 颗转发卫星、4 个网关、100 条持续业务流、每秒至少一次拓扑变化、路径计算 P95 < 200 ms、拓扑到 FIB P95 < 2 s、24 小时稳定运行，以及原文要求的延迟分位数、丢包、发现/恢复时间、震荡、利用率、CPU/内存和 FIB 不一致时长。分布式基线与中央策略使用同一输入比较。

后二者已明确加入 `coverage-manifest.json` 和 `single-pc-closure.json`。统一连接由 `p6-unified-system` 的运行门禁验证；全文规模与稳定性保持 partial。现有四路由器包闭环、1000 星纯算路测试和短时间恢复测试不能替代全文预算。

`make test-unified`（同 `make test-platform`）使用 `lab/platform/run.py`，将 OSPFv2/v3、LDP、SR-MPLS、SRv6、PCEP、BGP-LS、EVPN/VXLAN，以及 Kubernetes/Helm/Cilium/Hubble/Lease HA，接入预测切换、真实 FRR/5G GTP-U、Rust 自治和 NOC 的同一运行。卫星、网关与链路由配置直接决定，默认 120 星＋4 网关，也提供 360 星＋8 网关配置，见 [大星座直接驱动](platform-scale.md)。门禁读取 `reports/platform-runtime.json`，要求全节点部署与探测、每星进程、每种承载的逐阶段同包 GTP-U、故障恢复、实际云端 FRR 路由操作、主备切换、同一 PDU 和本轮源码一致。原基础实验 `make test-unified-base` 的 `reports/unified-runtime.json` 不能替代新门禁。单套持续 UE 会话、逻辑环面图、原两星轨道时序模板和本机管理心跳中继，不替代全文持续流、全星座物理可见性及 24 小时验收。NOC 必须采集同一控制器的指标、追踪、日志及故障告警，关联同一计划与恢复时间线。

## srsRAN 的已验证边界

`make test-srsran` 使用固定源码提交 `d2f4b70dda8e2c557d5b05a0ac5f92dbddda19bc` 的 srsRAN 25.10。该版本 TA 配置位于 `cell_cfg.ta` 和 `ntn.ta_info`。验收等待 `gNB started` 后持续运行至少 15 秒，要求真实调度指标和非空 MAC PCAP，并保存配置、日志、镜像和源码标识。

软件 RU/test UE 的用途参见 [srsRAN 官方 testmode 文档](https://docs.srsran.com/projects/project/en/latest/tutorials/source/testmode/source/index.html)。此运行证明真实 gNB 软件和 NTN 配置可运行。其内部测试 UE 不证明外部软件 UE 经 IQ 信道解码 SIB19、完成 NTN 接入及端到端业务；`p5-ntn-radio-effects` 因此仍保留未完成的 Release 17 NTN 项。完整软件 NTN 与必须实物的商用 UE/OTA 认证分别验收。
