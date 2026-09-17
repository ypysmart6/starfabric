> 当前默认入口已切换到逐星物理模式，详见 [物理星座说明](physical-constellation.md)。本页原有 120 星通过和 360 星内存不足记录属于历史逻辑图运行，不能用作新物理模式的通过证据。节点数量保持配置驱动，连接数量和参数现在由物理输入计算。

可配置卫星与网关场景

现有 `sfctl` 新增 `constellation generate`，可独立配置卫星数、网关数、逻辑轨道面、网关接入数量和业务意图，生成现有控制器可直接读取的场景。默认提供 120 星＋4 网关、360 星＋8 网关。

同一配置现在也直接驱动真实 FRR＋5G＋Rust＋协议＋云＋NOC 统一平台，详见 [大星座直接驱动](platform-scale.md)。下面的 `test-constellation` / `experiment run` 是保留的 memory adapter 回归；真实集成验收使用：

```bash
make test-unified SATELLITES=120 GATEWAYS=4 PLANES=12 FLOWS=8
make test-unified SATELLITES=360 GATEWAYS=8 PLANES=18 FLOWS=16
make test-unified CONSTELLATION_CONFIG=scenarios/constellations/leo-360.config.json
```

统一入口会创建配置数量的 FRR 和逐星进程，并检查资源；不能把本页的内存回放结果当作真实平台通过。

**一次生成并运行两套示例**

在工程根目录执行：

```bash
make constellation-examples
```

| 示例 | 逻辑面×每面卫星 | 网关 | 总节点 | 星间双向连接 | 星地双向连接 | 有向链路 | 业务意图 |
|---|---|---|---|---|---|---|---|
| 120 星 | 12×10 | 4 | 124 | 240 | 12 | 504 | 8 |
| 360 星 | 18×20 | 8 | 368 | 720 | 24 | 1488 | 16 |

每条双向连接在拓扑中用两条有向链路表示。示例每颗卫星连接同面前后及相邻面的同槽卫星；每个网关连接三个不同卫星。这是逻辑环面网络，轨道面标签没有物理传播含义。

结果目录：

```text
reports/constellations/leo-120-4/
reports/constellations/leo-360-8/
  scenario.json          控制器输入（节点、链路、业务意图、故障时间线）
  topology-summary.json  配置、节点/链路计数、初始路由使用的卫星数、证据边界
  report.json            本轮内存设备实验结果
  report.html            可直接打开的报告
```

**按自己指定的数量生成和运行**

```bash
make test-constellation SATELLITES=120 GATEWAYS=4 PLANES=12
make test-constellation SATELLITES=360 GATEWAYS=8 PLANES=18
make test-constellation SATELLITES=360 GATEWAYS=12 PLANES=18 FLOWS=100
```

只生成配置，省略回放：

```bash
make constellation SATELLITES=240 GATEWAYS=6 PLANES=12
```

`PLANES=0` 自动选择能整除卫星数、接近方形的逻辑网格；不提供该参数时默认自动选择。指定的面数必须整除卫星总数，质数卫星数可用单环。`FLOWS=0` 默认生成网关数量两倍的有向业务意图。生成前检查初始、链路故障、卫星故障下所有业务的主备路径，带宽或连通性不足时明确失败。

默认输出目录是 `reports/constellations/leo-<卫星数>-<网关数>`；相同数量不同参数的运行会写到同一个目录。需要保留对照结果时指定：

```bash
make test-constellation SATELLITES=360 GATEWAYS=8 PLANES=18 FLOWS=100 \
  CONSTELLATION_DIR=reports/constellations/leo-360-8-flows-100
```

**通过 JSON 文件设置完整参数**

两个可编辑配置文件是：

- `scenarios/constellations/leo-120.config.json`
- `scenarios/constellations/leo-360.config.json`

例如：

```json
{
  "satellites": 360,
  "gateways": 8,
  "planes": 18,
  "gateway_uplinks": 3,
  "flows": 16,
  "capacity_bps": 10000000000,
  "demand_bps": 10000000,
  "isl_latency_us": 2000,
  "feeder_latency_us": 4000
}
```

这是生成器配置，不能直接作为 controller 的 scenario。先转换：

```bash
make build
./bin/sfctl constellation generate \
  --config scenarios/constellations/leo-360.config.json \
  --output reports/my-constellation/scenario.json \
  --summary reports/my-constellation/topology-summary.json

./bin/sfctl scenario validate --file reports/my-constellation/scenario.json
./bin/sfctl experiment run \
  --scenario reports/my-constellation/scenario.json \
  --output reports/my-constellation/report.json \
  --html reports/my-constellation/report.html
```

显式命令行参数覆盖 JSON；例如 `--gateways 12 --flows 0` 会重新按 12 个网关生成 24 条业务。若修改卫星数后沿用原配置的面数不能整除，使用 `--planes 0` 重新自动分配。未知 JSON 字段、非法值、多余 JSON 文档和输入输出文件名相同会拒绝。

| 参数 | 含义与边界 |
|---|---|
| `--satellites` | 卫星节点数，4–10000；上限是输入范围，不是已验证的实时规模 |
| `--gateways` | 网关节点数，2–1000 |
| `--planes` | 逻辑面数，必须整除卫星数；0 自动选择 |
| `--gateway-uplinks` | 每网关连接的不同卫星数，至少 3，最多卫星数；允许一个故障后仍寻找主备路径 |
| `--flows` | 有向业务意图数，1–10000；0 为两倍网关数，不代表持续报文流 |
| `--capacity-bps` | 每条有向链路的配置容量，默认 10 Gbit/s |
| `--demand-bps` | 每意图预留带宽，默认 10 Mbit/s |
| `--isl-latency-us` | 星间链路配置时延，默认 2000 µs |
| `--feeder-latency-us` | 网关接入配置时延，默认 4000 µs |

网关对按确定性顺序枚举，每条意图具有独立目标前缀，避免不同业务在内存 AFT 中冲突。生成器使用现有生产 planner 预检查可达性与容量，不实现另一套算路器。

**每轮实际验证什么**

默认故障时间线是 10 步：初始提交；关闭首条业务实际选中的双向 feeder；恢复链路；关闭首条路径上的卫星；恢复卫星；给源网关注入 commit 失败；再次关闭链路并要求回滚；清除故障；重试提交；恢复链路与原主备路径。

报告应满足 `success=true`、`failure_count=0`、`rollback_count=1`，最终主备路径恢复，设备状态数等于卫星加网关总数。预期 commit 失败被检查为 `rolled_back`，不能用普通的“算路无路径”错误代替回滚成功。

部分节点可能不在当前主备路径上，因此总节点数与实际装有业务路由的卫星数不必相同。`topology-summary.json` 的 `initial_routed_satellites` 单独给出初始主备路径涉及的卫星数，避免把存在于图中的所有卫星都说成承载了业务。

常驻控制器也可以读取生成结果：

```bash
./bin/sf-controller \
  --scenario reports/constellations/leo-360-8/scenario.json \
  --adapter memory --listen 127.0.0.1:18088 \
  --state-dir data/leo-360-8 --reconcile-interval 5s
```

第二个终端执行 `./bin/sfctl get status --endpoint http://127.0.0.1:18088` 等现有命令。更换场景使用独立 state-dir；同一场景重启恢复保留该目录。常驻 controller 不自动播放 timeline，自动故障回放使用上面的 `experiment run`。

**证据范围**

本页 memory 回放摘要明确写入 `real_packet_data_plane=false` 和 `unified_5g_integration=false`。生成步骤只检查输入和 planner，`experiment run` 验证 memory adapter 的提交、模型状态和回滚。真实 FRR 的扩容由 `make test-unified` 负责，其独立运行证据在 `reports/platforms/`。两者共享配置生成器，各自如实报告执行方式；均不能自动证明真实 IQ 信道、全星座物理轨道可见性或 24 小时稳定性已通过。
