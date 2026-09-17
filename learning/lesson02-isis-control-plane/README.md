# Lesson 02：FRR、IS-IS、RIB 与 FIB

## 本课最终能力

学完后，你不能只会输入 `show` 命令，而要能独立解释并验证：

```text
接口与邻居
  -> IS-IS IIH 建立 adjacency
  -> LSP 泛洪形成 LSDB
  -> SPF 计算最短路径
  -> isisd 把路由交给 zebra
  -> zebra 选择并安装路由
  -> Linux FIB 转发真实数据包
```

任何一个箭头都可能失败。“邻居是 Up”只能证明第一段成功，不能证明端到端网络可用。

## 拓扑与代价

```text
                      10          10
                 +---------- r2 ----------+
                 |                         |
h1 ---- r1 ------+                         +------ r4 ---- h2
                 |                         |
                 +---------- r3 ----------+
                      20          20

192.0.2.0/24                                      198.51.100.0/24
```

节点还有稳定的 `/32` loopback：

| 路由器 | Loopback / IS-IS system ID |
|---|---|
| r1 | `10.255.0.1/32` / `0000.0000.0001` |
| r2 | `10.255.0.2/32` / `0000.0000.0002` |
| r3 | `10.255.0.3/32` / `0000.0000.0003` |
| r4 | `10.255.0.4/32` / `0000.0000.0004` |

先计算再运行：

- r1 -> r2 -> r4 -> h2 LAN：`10 + 10 + 10 = 30`；
- r1 -> r3 -> r4 -> h2 LAN：`20 + 20 + 10 = 50`。

因此正常路径应经过 r2。最后一个 `10` 是 r4 对被动接口 `eth3` 上
`198.51.100.0/24` 的前缀度量。

## 1. 启动与自动验收

```bash
cd /home/ypy/Desktop/卫星工程/learning/lesson02-isis-control-plane
make up
make test
```

如果实验已经运行，只执行 `make test`。自动验收会检查：

- 六个容器都在运行；
- 四份 FRR 配置能通过 `vtysh -C`；
- 启动日志没有 `Unknown command` 或配置处理失败；
- 业务流不能通过管理网默认路由逃逸；
- 每个路由器有两个 Up 邻居和四份 LSP；
- r1 的 FRR RIB 与 Linux FIB 都选择 r2；
- 双向 ping 和 traceroute 使用真实业务链路。

## 2. 邻接不是路由

```bash
make neighbors
```

逐列解释：

- `System Id`：邻居的 IS-IS 系统标识；
- `Interface`：本地承载邻接的接口；
- `L`：邻接层级，本实验只有 Level 2；
- `State`：邻接状态；
- `Holdtime`：没有继续收到 IIH 时还能保留邻接多久；
- `SNPA`：链路层地址。

关键点：IS-IS PDU 直接封装在二层，不依赖 IP/UDP/TCP 建立邻接。因此即使邻接 Up，
IPv4 前缀发布、SPF、zebra 或内核安装仍可能失败。

## 3. 从 NET 到 LSDB

打开任一配置，例如：

```bash
sed -n '1,240p' configs/r1/frr.conf
```

r1 的 NET 是：

```text
49.0001 . 0000.0000.0001 . 00
 area       system-id       NSEL
```

查看所有 LSP 及其 TLV：

```bash
make database
```

在输出中亲自找到：

- 四个路由器各自生成的 LSP；
- `Extended Reachability` 中的邻接及 metric；
- `Extended IP Reachability` 中的 loopback、链路前缀和端网前缀；
- r4 发布的 `198.51.100.0/24`。

LSDB 是拓扑事实数据库，不等于最终路由表。每台路由器以自己为根运行 SPF，结果可以不同。

## 4. 协议路由、FRR RIB 与 Linux FIB

依次执行：

```bash
docker exec clab-sf-l02-isis-r1 vtysh -c 'show isis route level-2'
docker exec clab-sf-l02-isis-r1 vtysh -c 'show ip route 198.51.100.0/24'
docker exec clab-sf-l02-isis-r1 vtysh -c 'show ip route 198.51.100.0/24 json'
docker exec clab-sf-l02-isis-r1 ip -j route show 198.51.100.0/24
make path
```

分别回答：

1. `isisd` 计算出的 metric 是多少？
2. zebra 的 RIB 为什么显示代码 `I`？
3. `>` 与 `*` 各自表示什么？
4. Linux FIB 中为什么出现 `protocol isis`？
5. traceroute 的三台中间路由器是否与 RIB/FIB 一致？

注意：FRR RIB 的协议 metric 与 Linux `ip route` 输出中的 metric 字段不应机械地视为
同一层语义。判断真实转发至少要核对目的前缀、下一跳、出接口以及数据包证据。

## 5. 被动接口

r1 的 `eth1` 和 r4 的 `eth3` 都配置了：

```text
ip router isis SF
isis passive
```

它的含义是：把接口前缀发布进 IS-IS，但不在该接口发送 IIH、建立路由器邻接。

请证明，而不是背诵：

```bash
docker exec clab-sf-l02-isis-r1 vtysh -c 'show isis interface detail'
docker exec clab-sf-l02-isis-r1 vtysh -c 'show isis neighbor'
docker exec clab-sf-l02-isis-r1 vtysh -c 'show isis database detail'
```

你应看到 `eth1` 是 Passive、没有 h1 邻居，但 `192.0.2.0/24` 出现在 LSP 中。

## 6. 配置正确性事故复盘

本实验第一次启动时曾使用错误顺序：

```text
isis metric 10 level-2
```

FRR 报 `Unknown command` 和 `processing failure`。当时邻接可以 Up，四份 LSP 也最终能够
出现，但端到端路由一度没有进入 RIB。正确形式是：

```text
isis metric level-2 10
```

这件事说明：

- 不能把容器 Running 当作服务健康；
- 不能把邻接 Up 当作路由健康；
- 配置必须做语法检查；
- 启动日志必须纳入验收；
- readiness 必须等待最终路由进入 FIB，而不是只等进程或邻接。

## 7. 无 BFD 的静默黑洞实验

四条核心链路使用 3 秒 IIH 与 3 倍 hold multiplier，理论失效检测约为 9 秒。

执行：

```bash
make failover
```

实验不会把接口 administratively down，而是在 r1-r2 两端加入 100% 丢包。这样本地接口仍然
显示 Up，IS-IS 只能等待 Hello hold timer 到期。这比 `ip link set down` 更接近单向/静默链路
故障，也为下一课比较 BFD 提供基线。

记录输出中的：

- `topology-event-to-FIB`；
- transmitted / received / lost；
- 恢复主路径所需时间。

成功后，脚本还会把同样的数据写入 `reports/latest.json`，供 Lesson 03 做同条件 A/B 对比。

不要期待每次数字完全相同。调度、轮询间隔和协议定时器都会产生波动；我们关心的是量级、
分布和因果链。恢复可能明显慢于失效切换，因为它还要经历邻接重新建立、新 LSP 生成与泛洪、
SPF 和 FIB 更新；实验为此允许最长 45 秒，但会报告真实时间。

## 8. 抓取 IS-IS PDU

```bash
make capture-isis
```

另开终端执行：

```bash
docker exec clab-sf-l02-isis-r1 vtysh -c 'clear isis neighbor'
```

观察 IIH、LSP、CSNP、PSNP。停止抓包按 `Ctrl-C`。本实验用共享 r1 网络命名空间的临时
工具容器抓包，所以不需要修改官方 FRR 镜像。

## 9. 本课过关标准

你必须能够脱离 README 完成：

- 从空白画出六节点拓扑、所有接口地址和链路 metric；
- 根据 metric 手算主路径和备用路径；
- 从邻接、LSDB、SPF、RIB、FIB、traceroute 六层逐层定位故障；
- 解释为什么 IS-IS 邻接不依赖 IPv4 地址；
- 解释 passive 接口“发布前缀但不建立邻接”；
- 识别配置部分失败而不是误报系统健康；
- 运行静默黑洞实验并解释约 9 秒收敛来自哪里；
- 修改一个 metric，使路径改走 r3，再恢复并让 `make test` 全部通过。

实验结束后可执行：

```bash
make down
```

下一课会在同一拓扑启用 `bfdd` 和 `isis bfd`，用相同故障、相同流量和相同测量程序做
受控 A/B 对比。
