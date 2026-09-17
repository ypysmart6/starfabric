# Lesson 01：先真正看见一个数据包怎样被转发

## 本课目标

完成本课后，你必须能够从空白画出并解释下面的路径：

```text
h1 10.0.1.2/24
       |
       | eth1  10.0.1.0/24
       |
r1 10.0.1.1/24 ── IPv4 forwarding ── 10.0.2.1/24
                                                |
                                                | eth1  10.0.2.0/24
                                                |
                                         h2 10.0.2.2/24
```

你需要用事实回答五个问题：

1. h1 为什么把包交给 r1，而不是直接寻找 h2 的 MAC？
2. h1 在链路上真正解析的是谁的 MAC 地址？
3. 经过 r1 时，源/目的 IP、源/目的 MAC 和 TTL 分别怎样变化？
4. 去程成功为什么不代表回程一定成功？
5. 路由存在、ARP 成功时，包为什么仍可能无法转发？

## 学习纪律

每组命令都按下面顺序执行：

1. 先写下你预测会看到什么；
2. 再执行命令；
3. 如果结果不同，先解释差异，再继续；
4. 不把 `ping` 成功当作唯一证据。

## 0. 环境验收

在仓库根目录执行：

```bash
bash scripts/verify-host.sh
```

五项全部 `PASS` 后进入实验。

## 1. 启动并验证基线

```bash
cd learning/lesson01-data-plane
make up
make inspect
make test
```

`make test` 检查的不只是 ping，还检查：

- 三个节点进程状态；
- r1 的内核转发开关；
- h1 和 h2 的路由选择；
- 双向可达性；
- 邻居表是否解析出下一跳 MAC。

## 2. 先读接口、地址和路由

```bash
docker exec clab-sf-l01-data-plane-h1 ip -br link
docker exec clab-sf-l01-data-plane-h1 ip -br address
docker exec clab-sf-l01-data-plane-h1 ip route
docker exec clab-sf-l01-data-plane-h1 ip route get 10.0.2.2
```

必须区分：

- `ip route` 展示路由表条目；
- `ip route get <destination>` 展示内核针对一个具体目的地址做出的查找结果；
- `via 10.0.1.1` 是三层下一跳；
- `dev eth1` 是出接口；
- 下一跳的二层地址还要通过 ARP 获得。

## 3. 观察 ARP 和 ICMP，而不是只看 ping

打开终端 A：

```bash
cd learning/lesson01-data-plane
make capture-r1
```

打开终端 B：

```bash
docker exec clab-sf-l01-data-plane-h1 ip neigh flush dev eth1
docker exec clab-sf-l01-data-plane-h1 ping -c 3 10.0.2.2
docker exec clab-sf-l01-data-plane-h1 ip neigh show dev eth1
```

你应当观察并记录：

- h1 广播询问的是 `10.0.1.1`，不是 `10.0.2.2`；
- r1 的两个接口使用不同的二层头；
- IP 源地址和目的地址端到端保持不变；
- 每经过一次三层转发，TTL 减 1；
- 第二次 ping 通常不再需要完整 ARP 交换，因为邻居缓存已经存在。

## 4. 故障实验 A：删除去程路由

先预测失败发生在 h1、r1 还是 h2，然后执行：

```bash
docker exec clab-sf-l01-data-plane-h1 \
  ip route del 10.0.2.0/24 via 10.0.1.1 dev eth1
docker exec clab-sf-l01-data-plane-h1 ip route get 10.0.2.2
docker exec clab-sf-l01-data-plane-h1 ping -c 2 -W 1 10.0.2.2
```

注意：容器仍有一条经管理口 `eth0` 的默认路由。因此失败不一定显示为
`Network unreachable`；内核可能把包送向错误的管理网络。这正是为什么必须查看
`ip route get`，不能只看 ping。

恢复：

```bash
docker exec clab-sf-l01-data-plane-h1 \
  ip route replace 10.0.2.0/24 via 10.0.1.1 dev eth1
```

## 5. 故障实验 B：保留路由，但关闭路由器转发

```bash
docker exec clab-sf-l01-data-plane-r1 sysctl -w net.ipv4.ip_forward=0
docker exec clab-sf-l01-data-plane-h1 ping -c 2 -W 1 10.0.2.2
docker exec clab-sf-l01-data-plane-h1 ip neigh show 10.0.1.1
```

此时可以同时出现：

- h1 有正确路由；
- h1 已解析出 r1 的 MAC；
- r1 能收到帧；
- 端到端 ping 仍失败。

这证明“路由选择”“邻居解析”和“转发动作”是三个不同阶段。

恢复：

```bash
docker exec clab-sf-l01-data-plane-r1 sysctl -w net.ipv4.ip_forward=1
```

## 6. 最长前缀匹配实验

先加入一个更宽、但指向错误下一跳的 `/8`：

```bash
docker exec clab-sf-l01-data-plane-h1 \
  ip route add 10.0.0.0/8 via 10.0.1.254 dev eth1
docker exec clab-sf-l01-data-plane-h1 ip route get 10.0.2.2
```

现有正确 `/24` 仍然获胜。然后加入错误的 `/32`：

```bash
docker exec clab-sf-l01-data-plane-h1 \
  ip route add 10.0.2.2/32 via 10.0.1.254 dev eth1
docker exec clab-sf-l01-data-plane-h1 ip route get 10.0.2.2
docker exec clab-sf-l01-data-plane-h1 ping -c 2 -W 1 10.0.2.2
```

错误 `/32` 会覆盖正确 `/24`，因为路由选择先比较匹配前缀长度。恢复：

```bash
docker exec clab-sf-l01-data-plane-h1 ip route del 10.0.2.2/32
docker exec clab-sf-l01-data-plane-h1 ip route del 10.0.0.0/8
make test
```

## 7. 本课过关标准

只有同时做到下面几项才算通过：

- 不看 README，能重新画出地址、前缀、出接口和下一跳；
- 能在抓包中指出 ARP、ICMP request、ICMP reply；
- 能解释每一跳二层头为什么改变；
- 能解释为什么删除专用路由后默认路由可能“吸走”流量；
- 能独立修复两种故障并让 `make test` 重新通过；
- 能说明原 `FindLargestPrefix()` 为什么不是真实 LPM。

实验结束：

```bash
make down
```

不要删除实验文件。下一课会把静态路由替换为 FRR/IS-IS，并比较控制平面学习到的
RIB 与 Linux 内核 FIB。
