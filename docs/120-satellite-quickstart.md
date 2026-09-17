# 120 星系统运行与改规模

## 已验证的入口

在工程目录运行：

```bash
cd /home/ypy/Desktop/卫星工程
make test-physical-runtime CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json PLATFORM_DIR=reports/physical-config/120-4
```

这一条命令会生成物理场景，启动 120 个卫星 FRR 节点、4 个地面站、云控制器和 5G，
执行 5 分钟／31 帧物理回放，再验证每星自治、地面恢复与 NOC，最后自动清理本轮资源。
它是一次完整启动、运行和验收流程，结束后不会作为常驻服务继续运行。

2026-09-15 的 `sf-unified-d180f84868` 实测通过：31 帧、10 次改路，最慢帧 8.08 秒；
同一 5G 会话持续报文 3,860 发／3,853 收，丢包率约 0.18%，源码哈希一致且资源清理完成。
结果见 [120 星专项报告](../reports/physical-platforms/leo-120-4/latest.json)。
此次通过的是三项物理改造及 5G／自治／NOC 流程；六种承载的逐项故障回归与云故障矩阵有独立入口：

```bash
make test-unified CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json PLATFORM_DIR=reports/physical-config/120-4
```

后者更耗时，当前版本尚无全部协议矩阵的完整通过报告。
360 星实网测试已按用户要求取消并清理，不在这台电脑继续运行。

## 是否只改 JSON

调整规模通常只改输入配置，无须改 Python、Go、FRR 或星上源代码。
使用当前自动生成轨道模式、保留 4 个网关及现有轨道条件时，修改
[`leo-120.config.json`](../scenarios/constellations/leo-120.config.json) 后重新运行即可：

| 字段 | 含义／约束 |
|---|---|
| `satellites` | 卫星总数 |
| `planes` | 轨道面数，必须整除卫星总数；`0` 表示自动选择面数 |
| `gateways` | 地面站数量；改变时须保证物理配置中有对应 ID 的坐标 |
| `flows` | 网关业务意图数量；`0` 表示自动取网关数的两倍 |
| `demand_bps` | 每条业务需求 |

当前为 120 星／12 面／每面 10 星。若保留 12 面，星数必须为 12 的整数倍。
源文件名含 `120` 不限制实际规模；以文件内容为准。使用 `CONSTELLATION_CONFIG` 后，
不要再用 `SATELLITES=...` 覆盖它，该变量不会覆盖 JSON 中的星数。

轨道高度、倾角、历元、时长、站点经纬度、仰角门限、终端数量、容量与丢包参数在
[`physical-defaults.json`](../scenarios/constellations/physical-defaults.json) 中。
物理模式下，逻辑种子里的 `isl_latency_us`／`feeder_latency_us`／`gateway_uplinks`
不决定真实物理链路；时延和连接由物理输入重新计算。若使用自己的 TLE／OEM，改变星数还须更新逐星数据目录。

改星数会改变覆盖和可用路径，不能保证任意规模都满足现有站点与主备业务要求。
可先只生成配置并检查全时段路径，不启动几百个容器：

```bash
make platform-configuration CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json PLATFORM_DIR=reports/physical-config/120-4
python3 tools/preflight_physical.py --scenario reports/physical-config/120-4/scenario.json --output reports/physical-config/120-4/path-preflight.json
```

生成的 `reports/.../scenario.json` 是输出，不应当作长期维护的输入配置修改。
更多模型边界见 [物理星座说明](physical-constellation.md)。
