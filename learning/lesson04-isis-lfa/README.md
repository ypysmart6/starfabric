# Lesson 04：IS-IS LFA——把备用路径提前算好并安装好

## 本课最终能力

前三课已经回答了“包如何转发”“路由从哪里来”“怎样快速发现静默故障”。本课继续追问：

> BFD 报告故障以后，r1 是临时等待一次新的 SPF，还是已经有一个可以立即使用的无环备用下一跳？

完成本课后，你必须能够独立完成并解释：

1. 区分故障检测、局部保护和全网收敛；
2. 写出 RFC 5286 的 LFA 严格不等式，并用本拓扑的 metric 手算；
3. 解释为什么 `r1 -> r3 -> r4` 是 `198.51.100.0/24` 的 LFA；
4. 解释为什么同一拓扑中的 `10.255.0.2/32` 没有 LFA；
5. 在 FRR 中找到主下一跳、`backupIndex`、`backupNexthops` 和已安装 nexthop-group；
6. 证明 LFA 是按前缀、按受保护接口计算的，不是“有第二条物理路径就自动受保护”；
7. 用静默黑洞验证 BFD 检测和 LFA 切换；
8. 把完整 SPF 故意延迟 5 秒，用实验事实证明局部修复发生在 SPF 之前；
9. 正确解读 A/B 数字，不把一次外部轮询结果误当成生产性能结论。

规范与实现依据：

- [RFC 5286：Basic Specification for IP Fast Reroute: Loop-Free Alternates](https://www.rfc-editor.org/rfc/rfc5286.html)
- [FRR IS-IS 文档：Fast-Reroute 配置与查看命令](https://docs.frrouting.org/en/latest/isisd.html#isis-fast-reroute)

## 1. 本课只改变一个关键变量

拓扑、地址、IS-IS metric、BFD 定时器、FRR 镜像、故障方式和流量都继承 Lesson 03：

```text
                         10                 10
                    +---------- r2 ---------------+
                    |                              |
h1 ---- r1 ---------+                              +--------- r4 ---- h2
         S / PLR    |                              |                    D
                    +---------- r3 ---------------+
                         20                 20
                                  N

主路径：h1 -> r1 -> r2 -> r4 -> h2，metric = 10 + 10 + 10 = 30
备选路径：h1 -> r1 -> r3 -> r4 -> h2，metric = 20 + 20 + 10 = 50
```

只在 r1 的主出接口 `eth2` 增加：

```text
isis fast-reroute lfa level-2
```

r1 是本实验的 **PLR（Point of Local Repair，本地修复点）**。命令配置在需要保护的主接口
`eth2` 上，不是配置在作为备用出口的 `eth3` 上。

为什么只配置 r1？因为本课观察的是 r1 到 h2 LAN 的转发，以及 r1-r2 邻接故障后的本地保护。
LFA 是局部机制；生产网络若要保护其他方向和其他链路，必须在对应 PLR 的受保护接口上分别启用
并检查覆盖率。

## 2. 三个时间概念不能再混用

本课的故障链应画成：

```text
0 ms                约 300 ms                      随后
r1-r2 静默黑洞  ->  BFD 检测 session 失效  ->  r1 激活预装 LFA
                         |                         |
                         |                         +-> 数据先改走 r3
                         v
                    IS-IS 撤邻接、生成/泛洪 LSP、运行 SPF
                                                   |
                                                   +-> 全网得到新稳态
```

三个量分别是：

```text
Tdetect       故障发生到 BFD 检测到故障
Tlocal-repair PLR 收到故障信号到预装备份接管
Tconvergence  各路由器 LSDB、SPF、RIB、FIB 达到新稳态
```

端到端观测常常近似为：

```text
Toutage ≈ Tdetect + Tlocal-repair + Tobserver
```

没有 LFA 时则要依赖新的 SPF/RIB/FIB 结果：

```text
Toutage ≈ Tdetect + Tadj/LSP + TSPF + TRIB/FIB + Tobserver
```

注意“局部保护”不等于“不再需要全网收敛”。LFA 负责在收敛窗口中先保持转发，新的 SPF 仍会
在后台产生最终路由。

## 3. LFA 为什么不会把包送回 r1

定义：

```text
S = Source，计算 LFA 的路由器，本实验是 r1
E = 主下一跳，本实验是 r2
N = 候选备用邻居，本实验是 r3
D = 目的前缀，本节先取 198.51.100.0/24
D(X,Y) = X 到 Y 的最短路径距离
```

候选邻居 N 成为 LFA 的基本条件是严格不等式：

```text
D(N,D) < D(N,S) + D(S,D)
```

右侧表示“N 先把包送回 S，再由 S 去 D”的代价。只有 N 自己到 D 的最短路严格更短，S 才能
确信 N 不会把包退回 S。必须是 `<`，不能是 `<=`。

更严格的 downstream path 判据是：

```text
D(N,D) < D(S,D)
```

它要求 N 比 S 更接近 D。所有 downstream path 都是 LFA，但不是所有 LFA 都满足 downstream
判据；更严格通常意味着面对复杂故障时更容易推理，也意味着可用候选和覆盖率可能更少。

### 3.1 对 h2 LAN 手算

```text
D(r3, 198.51.100.0/24) = 20 + 10 = 30
D(r3, r1)              = 20
D(r1, 198.51.100.0/24) = 10 + 10 + 10 = 30

30 < 20 + 30
30 < 50                 成立
```

所以 `r3` 是该前缀的 loop-free alternate。r1 预计算出的备份总代价为：

```text
r1 -> r3 -> r4 -> h2 LAN = 20 + 20 + 10 = 50
```

### 3.2 节点保护判据

若还要避免主邻居 E，而不只是避免 S-E 链路，则需满足：

```text
D(N,D) < D(N,E) + D(E,D)
```

本拓扑对 h2 LAN：

```text
D(r3,D)  = 30
D(r3,r2) = 30
D(r2,D)  = 20

30 < 30 + 20            成立
```

因此这个候选也能避开 r2。必须记住：节点保护是相对于具体的 `S、E、D` 判断，不是给一条路径
永久贴上“节点保护”标签。

## 4. 为什么有第二条物理路仍可能没有 LFA

现在把 D 改成 r2 的 loopback `10.255.0.2/32`。物理上当然可以绕行：

```text
r1 -> r3 -> r4 -> r2
```

但在故障前，r3 到 r2 loopback 的最短代价为 40；它可以经 r1，也可以经 r4。计算：

```text
D(r3, 10.255.0.2/32) = 40
D(r3, r1)             = 20
D(r1, 10.255.0.2/32) = 20

40 < 20 + 20
40 < 40                不成立
```

等号不够。若 r1 在全网尚未收敛时把包交给 r3，r3 的有效最短路径可能仍指回 r1，于是形成
微环。FRR 因此不为这个前缀安装 classic LFA。

这就是本课最重要的一句话：

> 物理可达的备用路径是收敛后的可能路径；LFA 是故障发生瞬间就能证明不会回环的备用下一跳。

LFA 的资格是 **per-prefix、per-PLR、per-protected-interface** 的。

## 5. 启动与自动验收

```bash
cd /home/ypy/Desktop/卫星工程/learning/lesson04-isis-lfa
make up
make test
```

如果实验已经运行：

```bash
make test
```

修改配置后完整重建：

```bash
make reconfigure
make test
```

自动验收会检查：

- 六个容器、四个 `isisd` 和四个 `bfdd`；
- 所有配置语法和启动日志；
- 管理面默认路由已删除；
- 邻接、LSDB 和 8 个 BFD session；
- r1 `eth2` 确实启用了 Level-2 classic LFA；
- h2 LAN 的主下一跳为 r2，备份下一跳为 r3；
- 主下一跳通过 `backupIndex` 关联到备份；
- zebra nexthop-group 中备份状态为 Installed；
- r2 loopback 没有 LFA；
- 健康态真实流量仍走 metric-30 主路径。

最后两项故意同时存在：LFA 不能改变健康态的最优路径，也不能谎报没有满足不等式的前缀。

## 6. 逐层证明“备用已经准备好”

执行：

```bash
make protection
```

### 6.1 先读覆盖率，不要只看一个前缀

本机当前结果：

```text
Classic LFA total: 4
ECMP total:        1
Unprotected total: 3
Protection coverage: 62.50%
```

`62.50%` 直接否定了“拓扑有菱形，所以所有路由都有保护”。FRR 默认把 loopback 归为 medium
优先级、非 loopback 归为 low；表格既按保护类型统计，也按前缀优先级统计。

### 6.2 再读 IS-IS 的备份路由表

在 `show isis route level-2 backup` 中找到：

```text
198.51.100.0/24  50  eth3  10.0.13.1
```

这里的 50 是备份路径 metric，不是当前主路径 metric 30。

### 6.3 再读 FRR RIB 的关联关系

`show ip route ... json` 的关键结构为：

```json
{
  "metric": 30,
  "nexthops": [
    {
      "ip": "10.0.12.1",
      "interfaceName": "eth2",
      "fib": true,
      "backupIndex": [0]
    }
  ],
  "backupNexthops": [
    {
      "ip": "10.0.13.1",
      "interfaceName": "eth3",
      "active": true
    }
  ]
}
```

必须逐个解释：

- `nexthops` 是健康态主下一跳；
- `fib: true` 表示主下一跳当前用于转发；
- `backupIndex: [0]` 把主下一跳关联到第 0 个备份；
- `backupNexthops` 不是 ECMP 主路径成员；
- `active: true` 表示备份本身可解析、可用，不表示健康态流量正在走它。

### 6.4 最后检查 zebra nexthop-group

`verify.py` 会读取路由中的动态 `installedNexthopGroupId`，再执行：

```text
show nexthop-group rib <动态 ID>
```

并要求出现：

```text
Valid, Installed
Backups:
  via 10.0.13.1, eth3
```

不要把某次运行中的 nexthop-group ID 写死；重建实验后 ID 可以变化。

本实验镜像里的 `iproute2` 对较新的 backup nexthop netlink 属性可能显示
`Error: Unknown attribute type`。因此不能只依赖旧版 `ip nexthop` 的格式；本课同时使用 FRR
RIB、zebra nexthop-group、故障后的真实 FIB 和真实流量闭环验证。

## 7. 健康态路径为什么仍走 r2

```bash
make route
make path
```

预期：

```text
h1 -> r1 -> r2 -> r4 -> h2
```

LFA 是备份，不是把 metric-50 路径加入 metric-30 的 ECMP。若健康态 traceroute 随机走 r3，
说明你配置的是等价路径、修改了 metric，或错误地把备份当成普通主下一跳；这不是本课目标。

## 8. 常规定时条件下的故障实验

```bash
make failover
```

脚本会先拒绝没有预装 LFA 的实验，然后：

1. 启动 100 ms 间隔 ping；
2. 在 r1 `eth2` 和 r2 `eth1` 同时注入 100% `netem loss`；
3. 并发观察 BFD、IS-IS adjacency 和 FIB；
4. 保持接口 administratively Up；
5. 清理 qdisc；
6. 等待主路径恢复和 LFA 重新 armed；
7. 写入 `reports/latest.json`。

本机 2026-09-03 的一次结果：

| 事件 | 第一次被外部观察到的时间 |
|---|---:|
| BFD Down/removed | 276.9 ms |
| IS-IS adjacency Down | 339.9 ms |
| 备用 FIB | 357.8 ms |
| ping 丢包 | 3 / 140 |
| 主 FIB 恢复 | 15320.3 ms |
| LFA 重新 armed | 15359.0 ms |

事件显示顺序可能有几十毫秒抖动。三个观察器各自执行 `docker exec + vtysh`，它们记录的是
“外部第一次看到”，不是 FRR 内部回调的绝对顺序。

## 9. 为什么普通 A/B 不能单独证明 LFA 更快

已有 Lesson 03 报告时执行：

```bash
make compare
```

或重新做同条件 A/B：

```bash
make ab
```

当前结果：

| 模式 | FIB 切换 | 丢包 |
|---|---:|---:|
| IS-IS + BFD | 307.9 ms | 3 |
| IS-IS + BFD + LFA | 357.8 ms | 3 |

不能据此宣布“LFA 慢了 50 ms”。原因是：

- 两组都先等待同一个约 300 ms BFD 检测；
- 四路由器拓扑的 SPF 极快；
- 外部轮询误差与剩余差值同量级；
- 两次实验不是大量重复后的分布；
- LFA 的核心新增属性是故障前已安装的无环备份。

若想证明“局部修复不依赖完整 SPF”，必须主动把这两个事件在时间上拉开。

## 10. 本课关键证明：让 SPF 故意慢 5 秒

执行：

```bash
make prove-local
```

脚本只在 r1 的运行态临时配置：

```text
spf-delay-ietf init-delay 5000
               short-delay 5000
               long-delay 5000
               holddown 10000
               time-to-learn 5000
```

然后注入同一个静默黑洞。脚本要求在 4.5 秒内同时证明：

- r1 活跃 FIB 已改为 `10.0.13.1`；
- h1 到 h2 ping 成功；
- traceroute 已经过 r3；
- r1 仍显示 `SPF delay status: Pending`。

本机实测：

```text
backup FIB active: 308.8 ms
observed path:      h1 -> r1 -> r3 -> r4 -> h2
r1 full SPF:        Pending, due in 4888 ms
```

这才是有区分力的证据：在 r1 的完整 SPF 还要等待约 4.9 秒时，数据面已经恢复。脚本无论成功
失败都会删除 qdisc 和临时 SPF-delay，并等待主路径与 LFA 恢复；结果写入：

```text
reports/local-repair-proof.json
```

如果脚本被 `kill -9` 或宿主机断电，自动清理无法执行。重新部署即可恢复：

```bash
make reconfigure
make test
```

## 11. LFA 的局部边界

“local” 有严格含义：PLR 必须能直接感知它所保护的主邻接或主链路故障。

在本实验中，r1 是 r1-r2 故障的 PLR。r4 并不与 r1-r2 链路相邻；r4 对这个远端故障仍要依赖
新的 LSP 和 SPF。LFA 不是一条端到端隧道，也不是全网同时瞬移到备用树。

因此还要区分：

```text
link protection  避免故障链路 S-E
node protection  连主邻居 E 也避免
local LFA        候选必须是 S 的直连邻居
remote LFA       通过隧道到远端 PQ 节点，扩大覆盖率
TI-LFA           使用 Segment Routing 修复路径，进一步提高覆盖率与约束能力
```

本课只实现 classic local LFA。不要看到 FRR 支持 remote LFA/TI-LFA 就立刻开启；先把覆盖率、
底层 MPLS/SR 能力和实际修复路径解释清楚。

## 12. 必做破坏性练习

每个练习都必须按“预测 → 修改 → 观察 → 解释 → 恢复 → `make test`”完成。

### 练习 A：命令加在错误接口

从 r1 `eth2` 删除：

```text
isis fast-reroute lfa level-2
```

把它错误地加到 `eth3`。重建后回答：

1. 健康态 ping 是否仍成功？
2. h2 LAN 是否仍有经 r3 的 `backupNexthops`？
3. `make test` 在哪一层失败？
4. 为什么“备用接口上启用 LFA”这个直觉是错的？

恢复后必须通过 `make test`。

### 练习 B：保留第二条路，但破坏 LFA 不等式

把 r3 `eth2` 与 r4 `eth2` 的 metric 从 20 同时改为 100。先手算：

```text
D(r3,D)
D(r3,r1)
D(r1,D)
```

你会得到严格不等式的两侧相等。验证：

- 物理备用路径仍存在；
- 健康主路径仍经 r2；
- h2 LAN 的 classic LFA 消失；
- 主链路故障后最终仍能经新的 SPF 绕行；
- 但不能再声称有预装的无环局部保护。

恢复两个 metric 为 20。

### 练习 C：显式排除唯一候选

在 r1 `eth2` 的 LFA 配置旁加入：

```text
isis fast-reroute lfa level-2 exclude interface eth3
```

解释为什么 IS-IS adjacency、BFD、主路由和健康 ping 都可以正常，而保护验收失败。然后删除
exclude 并恢复。

### 练习 D：覆盖率审计

不看自动验收代码，逐个解释 summary 中：

- 4 个 Classic LFA 前缀；
- 1 个 ECMP 前缀；
- 3 个 Unprotected 前缀。

对每个前缀写出主下一跳、候选下一跳和不等式。只复述 `62.50%` 不算完成。

### 练习 E：阅读自动化代码

阅读 `verify.py`、`measure-failover.py`、`prove-local-repair.py`，回答：

1. 为什么 nexthop-group ID 不能写死？
2. 为什么所有故障脚本都必须在 `finally` 清理 qdisc？
3. 为什么 `backupNexthops` 存在还不等于当前流量正在走备份？
4. 为什么证明实验把超时设为 4.5 秒，而 SPF delay 是 5 秒？
5. 如果只有 ping 成功，没有 `SPF Pending` 证据，实验少证明了什么？

## 13. 本课口试题

请先独立回答，再对照命令输出：

1. BFD 与 LFA 各自解决什么问题？
2. 为什么 LFA 不能替代 IS-IS SPF？
3. 为什么基本判据必须使用严格小于号？
4. `backupIndex: [0]` 表达了什么关系？
5. metric-50 备份存在时，健康流量为什么仍走 metric-30 主路径？
6. 同一台 r1 上为什么 h2 LAN 受保护，而 r2 loopback 不受保护？
7. 普通 A/B 中 LFA 比无 LFA 多 50 ms，为什么不能得出“LFA 更慢”？
8. `make prove-local` 中哪三项证据共同证明了“先局部修复、后完整 SPF”？
9. r4 为什么不能对远端 r1-r2 故障直接做本地修复？
10. LFA 覆盖率不是 100% 时，生产网络应做什么，而不是忽略这个数字？

## 14. 过关标准

只有同时做到以下事项才算通过：

- 不看 README，画出 S、E、N、D 和主/备 metric；
- 写出基本 LFA、downstream path、node-protecting 三个判据；
- 手算 h2 LAN 通过、r2 loopback 不通过；
- 从配置指出命令为什么位于 r1 `eth2`；
- 从 summary、backup table、RIB JSON、nexthop-group 四层证明预装保护；
- 运行普通故障实验并解释三个外部观察时间；
- 运行延迟 SPF 实验并解释它为什么具有更强的因果证明力；
- 完成练习 A、B、C，恢复后让 `make test` 全部通过；
- 能解释局部保护与最终全网收敛的边界；
- 不把单次实验数字包装成 SLA。

结束实验：

```bash
make down
```

下一课将从路由协议机制转向 StarFabric 的第一个产品组件：用 C++20 和 Netlink 读取真实 Linux
接口与路由，建立可测试的 Route Agent 状态模型。前三课和本课中的 RIB/FIB、幂等、验证与故障
思维会直接进入 Agent，而不是另起一条互不相干的 C++ 学习线。
