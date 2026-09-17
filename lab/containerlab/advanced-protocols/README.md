# SRv6 / PCEP / BGP-LS / EVPN-VXLAN 单机闭环

`make test-advanced-live` 运行两个隔离的 containerlab 子闭环，并汇总到
`reports/advanced-protocols-closed-loop.json`。它不把“FRR 能解析配置”写成“数据面已实测”。

## 子闭环 1：SRv6 + BGP-LS + PCEP

```text
                    eBGP BGP-LS + TCP/4189 PCEP
                 +-------------------------------- PCE/collector
                 |
head --- primary --- tail
  |                    |
  +------ backup ------+
```

- FRR IS-IS 通告四个 SRv6 locator，Linux `seg6local End` 处理 SID。
- head 以 SRH type 4 指定 primary SID；故障后由确定性控制器切到 backup SID，两条路径都抓取真实 SRH 报文。
- IS-IS TED 经 opaque ZAPI 导出成 BGP-LS Node/Link/IPv4 Prefix/IPv6 Prefix NLRI，外部 collector 验证主邻接子网 NLRI 撤销和恢复。
- FRR `pathd -M pathd_pcep` 作为 PCC，与本实验的最小 PCE 完成 OPEN/KEEPALIVE。动态候选路径产生真实 PCReq，PCE 返回含 RP + NO-PATH 对象的 PCRep。

PCEP 的 NO-PATH 是有意的负向计算结果：这个最小 PCE 不伪装成生产级算路器；
SRv6 数据面另由真实内核路由与报文证据闭环。

BGP 配置在 IS-IS TED 就绪后通过 `vtysh -c` 增量应用。不要使用
`vtysh -f` 将仅含 BGP 的片段作为全守护进程配置加载：固定 FRR 镜像中，
这条加载路径曾出现 BGP 会话已建立、但 TED 没有进入发送端 BGP-LS RIB 的问题。
验收分别要求两端会话建立、发送端包含四个节点和八条有向链路、collector
收到 Node/Link/IPv4/IPv6 NLRI，再执行撤销和恢复。报告保留两端邻居状态和
故障前后 RIB；失败时也会保存发送端及 Zebra 客户端诊断。

## 子闭环 2：EVPN/VXLAN

```text
host-a -- VTEP-A == primary/backup IPv4 underlay == VTEP-B -- host-b
                 iBGP EVPN over loopbacks; VNI 100; UDP/4789
```

- Linux 创建 bridge/VXLAN netdev，FRR 只通过 Netlink 发现它们。
- 宿主 MAC 被编码成 EVPN Type-2 route，远端 VTEP 由 Zebra 将 `extern_learn` FDB 表项下发到 Linux。
- 主 underlay 故障后 OSPF FIB 切到备路，iBGP EVPN 会话与 Type-2/FDB 保持，脚本在备链路抓取真实 VXLAN UDP/4789 报文。

## 证据与边界

三份详细 JSON 报告分别保留 SRH/VXLAN 抓包文本、FRR JSON 状态、Linux FIB/FDB、
PCEP 对象类型、NLRI 撤销集合、收敛时间和持续 ping 丢包。这是一台电脑上的真实软件协议/内核数据面，
不等于硬件线速、厂商 NOS 互操作、星载 ASIC 或生产 PCE 认证。

每次运行先将三份报告标记为未完成；前置子实验失败时，不会留下旧的成功
聚合结果或把未执行的 EVPN 子实验误认为本次通过。PCEP 事件文件也按本次运行重建。
