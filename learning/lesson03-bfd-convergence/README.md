# Lesson 03：BFD 驱动的快速故障检测与收敛

## 本课最终能力

本课不是“打开 BFD 开关”。完成后，你要能独立回答并用数据证明：

1. BFD 负责检测什么，IS-IS、zebra 和 Linux FIB 又分别负责什么；
2. 为什么 `100 ms × 3` 的名义检测时间是 300 ms，但 FIB 切换不必精确等于 300 ms；
3. 为什么 `isis bfd profile FAST` 不等于已经启用 BFD；
4. 如何证明 BFD peer 是由 IS-IS adjacency 动态创建、定时器已在两端协商且真正 Up；
5. 如何注入“接口仍 Up、业务包全丢”的静默黑洞，并分别测量 BFD、邻接和 FIB；
6. 为什么故障撤路很快，而主路径恢复可能慢得多；
7. 为什么单次实验只能说明本次观测，不能直接当作生产 SLA。

## 1. 实验不改变第二课的其他变量

拓扑、地址、metric、FRR 镜像和业务流量都与 Lesson 02 相同，只增加 `bfdd` 与
IS-IS/BFD 绑定：

```text
                      metric 10         metric 10
                 +------------- r2 -------------+
                 |                                |
h1 ----- r1 -----+                                +----- r4 ----- h2
                 |                                |
                 +------------- r3 -------------+
                      metric 20         metric 20

正常主路径：h1 -> r1 -> r2 -> r4 -> h2，IS-IS metric = 30
故障备用路径：h1 -> r1 -> r3 -> r4 -> h2，IS-IS metric = 50
```

这种控制变量设计很重要。若拓扑、流量频率、故障方式和观察点同时变化，就不能把结果差异
归因于 BFD。

## 2. 先建立正确的心智模型

BFD 不是路由协议，不发现拓扑、不生成 LSP、不运行 SPF，也不把 IP 路由直接写入内核。
本实验的失效链是：

```text
r1-r2 不再收到 BFD Control
        |
        v
bfdd: session Up -> Down
        |
        v
isisd: 收到 BFD 通知，撤销 r2 adjacency
        |
        v
LSP 变化/泛洪 -> SPF 重新计算
        |
        v
zebra 更新 FRR RIB -> netlink 更新 Linux FIB
        |
        v
业务流量改走 r3
```

因此总收敛时间应理解为：

```text
Ttotal = Tdetect + Tnotify + Tadj/LSP + TSPF + TRIB/FIB + Tobserver
```

其中只有 `Tdetect` 直接来自 BFD 定时器。`make failover` 的数字还包含外部观察器通过
`docker exec + vtysh` 采样的延迟。

## 3. BFD 协议要掌握到什么程度

本实验使用单跳、异步模式 BFD：双方周期性发送 BFD Control；连续一段检测时间没有收到
合法控制包，接收端把 session 置为 Down。常见状态是：

```text
Down -> Init -> Up
 ^             |
 +-------------+
```

另有管理态 `AdminDown`。控制包中的 `My Discriminator` 唯一标识本地 session，
`Your Discriminator` 回显对端标识；它们用于解复用，不是 IP 地址或路由器 ID。

异步模式下，本地检测时间为：

```text
对端通告的 Detect Mult
  × max(本地 Required Min RX, 对端 Desired Min TX)
```

本实验两端对称配置 `100 ms` 和 multiplier `3`，所以协商结果是 `300 ms`。发送间隔会加入
jitter，不能用抓包中任意两个包的间隔机械地判定配置错误。

单跳 BFD Control 使用 UDP 目的端口 `3784`，IPv4 TTL / IPv6 Hop Limit 必须为 `255`。
这能限制来自非直连路径的伪造报文，但不替代认证与控制平面保护。

规范依据：

- [RFC 5880：BFD 基础协议、状态机和检测时间](https://www.rfc-editor.org/info/rfc5880/)
- [RFC 5881：IPv4/IPv6 单跳 BFD](https://www.rfc-editor.org/info/rfc5881/)
- [FRR BFD 文档：profile、状态命令与 IS-IS 集成](https://docs.frrouting.org/en/latest/bfd.html)

## 4. 配置解剖

### 4.1 启动守护进程

打开 `configs/daemons`，确认：

```text
isisd=yes
bfdd=yes
```

`bfdd` 未运行时，接口配置看似存在也不会产生正常 session。自动验收会同时检查两个进程。

### 4.2 定义可复用 profile

四台路由器都有：

```text
bfd
 profile FAST
  detect-multiplier 3
  receive-interval 100
  transmit-interval 100
  log-session-changes
 exit
exit
```

profile 是参数模板，不等于 peer，也不等于某个路由协议已经订阅 BFD 状态。

### 4.3 在核心接口启用并选择 profile

每条路由器间链路必须同时出现：

```text
isis bfd
isis bfd profile FAST
```

这是本课第一个真实故障点。初版实验只有第二行；FRR 10.7 接受配置、`bfdd` 也在运行，
但 `show bfd peers brief json` 返回 `[]`。原因是该版本把 monitoring 的 `enabled` 与
`profile` 保存为两个独立配置项：选择 profile 不会隐式启用监测。

不要用“配置能解析”代替“功能已生效”。至少要同时证明：

- `show running-config` 中有启用与 profile 两行；
- `show isis neighbor detail` 显示 `BFD is active, status Up`；
- `show bfd peers` 中存在 `Peer Type: dynamic` 且状态 Up；
- 本地、远端协商后的 interval 和 multiplier 正确。

h1/h2 的被动接口没有 BFD，因为它们不建立 IS-IS adjacency。

## 5. 启动与自动验收

```bash
cd /home/ypy/Desktop/卫星工程/learning/lesson03-bfd-convergence
make up
make test
```

实验已经运行时，只需：

```bash
make test
```

修改配置后完整重建：

```bash
make reconfigure
make test
```

验收必须全部通过。它会检查：

- 六个容器运行，四台路由器各有 `isisd` 与 `bfdd`；
- FRR 配置可解析，日志没有配置处理失败；
- 管理网默认路由已删除，业务流不能从管理面逃逸；
- 每台路由器两个 Up adjacency、完整四 LSP LSDB；
- 8 个有向动态 BFD session 全部 Up；
- 每个 session 本地与远端都协商为 100 ms × 3，detection timeout 为 300 ms；
- FRR RIB 与 Linux FIB 都使用 metric-30 的 r2 主路径；
- 双向 ping 与 traceroute 真正通过业务链路。

注意：一条双向链路在两台设备上各显示一个本地 session，因此四条路由器间链路共看到
8 个“有向状态条目”，不是 8 条物理链路。

## 6. 逐层读取健康态

### 6.1 BFD session

```bash
make bfd
docker exec clab-sf-l03-bfd-r1 \
  vtysh -c 'show bfd peer 10.0.12.1 json'
```

你必须能解释：

- `peer/local/interface`：对端地址、本地源地址和承载接口；
- `id/remote-id`：两端 discriminator；
- `type: dynamic`：session 由 IS-IS adjacency 请求，不是静态 `bfd peer`；
- `status: up`：双向控制报文交换成功；
- `profile: FAST`：模板确实应用；
- `receive/transmit-interval`：本地参数；
- `remote-*`：对端通告参数；
- `detection-timeout: 300`：协商后的本地检测超时；
- `transmit-interval-actual`：含实现 jitter 的当前实际发送间隔。

### 6.2 协议绑定

```bash
make neighbors
```

在 r2/r3 两个邻居下都找到：

```text
BFD is active, status Up
```

只看 `show bfd peers` 还不够：静态 BFD peer 也可能 Up，但没有任何路由协议消费它的状态。
必须从 IS-IS 侧证明绑定关系。

### 6.3 路由与真实路径

```bash
make route
make path
```

预期下一跳为 `10.0.12.1 dev eth2`，路径经过 r2。BFD 健康不代表 RIB/FIB 正确，所以这两层
仍必须单独验证。

## 7. 抓包验证

终端 A：

```bash
make capture-bfd
```

终端 B：

```bash
make bfd
```

在 r1 `eth2` 上确认：

- IPv4 TTL 为 255；
- UDP 目的端口为 3784；
- 两个方向都有周期性 Control；
- session Up 后的报文包含非零 My/Your Discriminator；
- 相邻发送间隔围绕 100 ms 波动，而不是严格固定 100.000 ms。

这一步把 CLI 状态与线上的真实报文闭环。停止抓包按 `Ctrl-C`。

## 8. 同条件静默黑洞实验

```bash
make failover
```

脚本在 r1 `eth2` 与 r2 `eth1` 两端执行：

```text
tc qdisc replace dev <interface> root netem loss 100%
```

关键性质：接口仍 administratively/carrier Up，本地 connected route 也仍存在，只有穿过链路的
包被静默丢弃。因此这不是 `ip link down` 提供的瞬时本地信号，BFD 必须靠控制报文超时发现。

脚本同时启动三个独立观察循环，分别记录：

1. BFD peer Down 或被 IS-IS 删除；
2. r2 adjacency 不再 Up；
3. r1 FIB 下一跳变成 `10.0.13.1`。

三个输出是“第一次被外部轮询观察到的时间”，不是 FRR 内部纳秒级事件时间。因此几十毫秒
范围内出现邻接观察值略早于 BFD peer 观察值，不表示因果倒置；动态 peer 在 adjacency 被撤销
后也可能立即消失。

脚本无论成功失败都会清除 qdisc，随后等待 BFD、邻接和主 FIB 恢复，并把结果写入：

```text
reports/latest.json
```

## 9. A/B 对比

先确保 Lesson 02 与 Lesson 03 两个实验都在运行：

```bash
make -C ../lesson02-isis-control-plane up
make up
```

然后在 Lesson 03 目录执行：

```bash
make ab
```

它依次运行无 BFD 基线、BFD 实验和比较器。2026-09-03 本机的一次真实结果为：

| 模式 | r1 FIB 切到备用路径 | 140 个 ping 丢包 |
|---|---:|---:|
| IS-IS only，3 s × 3 Hello hold | 9044.0 ms | 87 |
| IS-IS + BFD，100 ms × 3 | 307.9 ms | 3 |
| 本次改善 | 29.38× 更快 | 少丢 84 包 |

原始机器可读记录位于两课各自的 `reports/latest.json`。重新运行会覆盖 `latest.json`，数字会随
调度、jitter 和观察延迟变化。正确结论是“从约 9 秒量级降到约 0.3 秒量级”，不是承诺每次
都精确等于表中小数。

## 10. 为什么恢复不是故障过程的倒放

本次 BFD 实验还观察到：

- IS-IS adjacency 恢复：约 0.36 s；
- 动态 BFD session 恢复 Up：约 1.34 s；
- 主 FIB 再次成为 r2：约 16.23 s。

BFD session 是 IS-IS 发现 adjacency 后动态创建的。因此：

```text
恢复方向：先收到 IS-IS IIH/建邻 -> 再创建并拉起 BFD -> LSP/SPF/FIB 收敛
```

BFD 的核心价值是快速报告一个已经监测的路径失效；它不负责发现一个尚不存在的 IS-IS
邻居，也不保证恢复时间与失效时间对称。生产系统必须分别定义并测量 failure convergence 与
recovery convergence，不能只报告一个“收敛时间”。

## 11. 生产工程中的定时器取舍

更小的 interval 不总是更好。session 数量、每秒包数、`bfdd` 调度延迟、CPU 抢占、控制面
保护、链路抖动和误报风险会一起变化。粗略估算单向发送速率：

```text
每台设备 BFD Control pps ~= session 数 / interval_seconds
```

例如 10,000 个 session、100 ms interval，约为 100,000 pps 的控制包发送量；两端接收、状态
维护、日志和其他协议负载还未计入。生产前至少要做：

- 在目标 session 规模和真实 CPU 压力下测 p50/p95/p99 检测与 FIB 收敛；
- 注入短时抖动、突发丢包、单向丢包、CPU 饥饿与 `bfdd` 重启；
- 监控 session flap、控制包丢弃、进程 CPU/调度延迟和 FIB 编程延迟；
- 根据业务 SLA 与误切换代价选择 interval/multiplier，而不是照抄 100 ms × 3；
- 评估认证、CoPP/ACL、TTL 255 校验以及管理面访问控制。

本课的 100 ms × 3 是用于理解和测量的明确实验点，不是对所有生产网络的通用推荐。

## 12. 必做破坏性练习

每次修改后都执行 `make reconfigure && make test`，失败时按进程→配置→BFD→IS-IS→RIB/FIB
→数据面的顺序定位。

### 练习 A：只有 profile，没有 enable

从 r1 `eth2` 暂时删除 `isis bfd`，保留 `isis bfd profile FAST`。

预测并验证：

- 配置是否仍能解析？
- BFD peer 是否存在？
- `show isis neighbor detail` 如何显示？
- `make test` 在哪一层失败？

然后恢复配置。

### 练习 B：两端定时器不对称

新建 `SLOW` profile，例如 r2 使用 300 ms receive/transmit、multiplier 5，r1 仍使用 FAST。
先按 RFC 公式分别计算两个方向的 Detection Time，再从 `show bfd peer ... json` 验证本地和
remote 字段。不要假设两个方向的检测时间相同。

### 练习 C：单向黑洞

手工只在 r1 `eth2` 加 `loss 100%`，观察 r1 与 r2 哪一侧先报告 Down、两边 IS-IS adjacency
如何变化。结束后务必执行：

```bash
docker exec clab-sf-l03-bfd-r1 tc qdisc del dev eth2 root
make test
```

### 练习 D：构造误报边界

把 `loss 100%` 改成可控的 delay/jitter/loss 组合，至少运行 20 次，记录：

- BFD 检测时间分布；
- FIB 切换 p50/p95/p99；
- 丢包分布；
- false positive 次数；
- 恢复时间分布。

如果只保存平均值，本练习不算完成。

## 13. 本课过关标准

不看 README，你必须能够：

- 从零写出 `bfdd`、profile、`isis bfd` 与 profile 绑定配置；
- 解释为什么 profile 与 enable 是两个动作；
- 根据两端通告值计算两个方向的 Detection Time；
- 从 CLI 和抓包同时证明 session 的状态、类型、地址、端口、TTL 和 discriminator；
- 画出 BFD Down 到 Linux FIB 改写的完整组件链；
- 用静默黑洞而不是接口 down 做受控实验；
- 解释外部轮询时间为什么不能等同于内部事件时间；
- 用相同实验条件比较 IS-IS-only 与 IS-IS+BFD；
- 分开解释 failure convergence 和 recovery convergence；
- 完成 A/B/C 三个破坏性练习，恢复后让 `make test` 全部通过；
- 为 10,000 个 session 估算控制包速率，并说明 100 ms × 3 的资源与误报风险。

结束实验：

```bash
make down
```

下一课将把“单链路故障后重新跑 SPF”推进到 FRR IS-IS LFA：预先计算无环备用下一跳，区分
故障检测、局部保护与全网收敛，并验证为什么“有第二条路径”不等于“该前缀受保护”。
