# 逐星物理模型与实际报文

`make platform-configuration` 和 `make test-unified` 默认使用 `PLATFORM_MODEL=physical`。
120／360 星共用 `tools/physical_constellation.py`、`Fabric` 和 `PhysicalReplay`。
每颗卫星具有独立 TEME 位置、速度和轨道输入；网关具有经纬度和高度。
逻辑生成器仅提供节点身份、面／槽分组及业务意图，原有连接全部被物理编译结果替换。

## 输入与运行

```bash
make platform-configuration CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json PLATFORM_DIR=reports/physical-config/120-4
make platform-configuration CONSTELLATION_CONFIG=scenarios/constellations/leo-360.config.json PLATFORM_DIR=reports/physical-config/360-8
make test-physical-packets CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json
make test-physical-runtime CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json
make test-physical-runtime CONSTELLATION_CONFIG=scenarios/constellations/leo-360.config.json
make test-unified CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json
make test-unified CONSTELLATION_CONFIG=scenarios/constellations/leo-360.config.json
```

安装轨道依赖：`python3 -m pip install -r tools/requirements-orbit.txt`。
Docker、FRR、内核 netem、MPLS、kind 和 5G 依赖仍按平台 README 准备。
实际运行至少需要两个可用逻辑 CPU。平台为本轮 UERANSIM gNB／UE 预留一个 CPU，
把驱动器、星座、星上进程、云节点和捕获进程放到其余 CPU，避免批量启动时软件无线链路计时器失去调度。
`radio-cpu-budget.json` 记录分配与恢复；退出时恢复 gNB／UE 原有 CPU 设置。
`PLATFORM_MODEL=logical` 显式选择历史逻辑图回归；该模式不能满足新的物理验收项。
`make test-constellation` 保留原内存回放。

`test-physical-runtime` 是此次三项改造的专项入口，直接调用同一个 `Platform`，部署全部配置节点、
云控制器、5G 和每星自治，执行全时段物理回放、持续报文、地面中断恢复与 NOC 验证。
它省去六种承载的逐项故障回归和云策略／主实例切换，报告单独写入
`reports/physical-platform-runtime.json` 及 `reports/physical-platforms/leo-<星数>-<网关数>/latest.json`。
通过时 `physical_runtime_integration=true`，`full_protocol_integration=false`；
完整协议入口 `test-unified` 的验收项保持不变。

`PHYSICS_CONFIG=自己的文件.json` 指定物理参数。默认文件为
`scenarios/constellations/physical-defaults.json`，明确标为设计示例：1200 km 高度参数、53° 倾角、
Walker 相位因子 1、固定 UTC 历元、8 个可编辑城市位置，按配置网关 ID 选取所需站点。
这些位置不是已部署地面站的声明，轨道也不是某个真实运营星座的任务星历。
缺少任何一个所需网关的位置会拒绝编译。

轨道输入支持：

- `orbit.source=walker_sgp4`：为每颗星生成不同 RAAN／平近点角，使用 SGP4/WGS72 传播；保存全部输入根数。
- `orbit.source=tle`：`orbit.catalog` 指向 `{id,line1,line2}` 数组；必须恰好覆盖全部卫星 ID。
- `orbit.source=oem`：`orbit.catalog` 指向 `{id,path}` 数组；OEM 必须为 TEME/UTC。使用位置和速度做三次 Hermite 插值，禁止外推，记录文件 SHA-256。

catalog 相对物理配置文件解析；OEM 路径相对 catalog 解析。OEM 还必须覆盖记录中明确列出的捕获预热时间。
历史 `gateway_uplinks`、`isl_latency_us` 和 `feeder_latency_us` 仅属于逻辑种子配置。
物理模式的终端数由 `links` 设置，时延由几何距离计算。

## 链路如何产生

每个采样时刻传播所有卫星，计算地面站随地球自转在 TEME 中的位置和速度。
星地链路须同时满足最小仰角及最大距离；星间链路须同时满足地球遮挡净空和最大距离。
在这些候选中按稳定、确定性的调度选择链路，并限制每星光终端、同面光终端、馈电终端和每站终端数量。
同面链路有调度优先级，但仍需通过几何与数量限制；接触中断立即撤销，重新接触须再次等待捕获时间。
采样时刻之间采用保持模型，接触边界精度受 `step_seconds` 限制；默认步长 10 秒。
地面站故障通过网关节点失效建模；同一源／目的网关是主备路径的共同端点，不把该端点错误声明成所有馈电链路都必须避开的 SRLG。

每个有效方向具有：

- 距离、带符号的一阶 Doppler、`ceil(range/c)` 加处理时延；
- 显式参考距离／参考 SNR 的自由空间衰减模型：`SNR = reference_SNR × (reference_range/range)^2`；
- 容量为 `min(max_capacity, efficiency × bandwidth × log2(1+SNR))`；
- 显式配置的报文丢包率和队列长度。

参考 SNR、带宽、效率和上限全部在配置中。该模型不包含天气、干扰、姿态或真实光学捕获性能；
`packet_loss_ppm` 是独立配置参数，不能解释为已由射频误码率推导。Doppler 保留为信道元数据，
Linux IP 报文不会被伪称为完成了射频频偏处理。独立 IQ 专项继续保留。

## 怎样作用于真实网络

部署时按整个时间范围内的连接并集预建 veth；当前不可用的连接保持 down。
每个方向安装 netem 的时延、速率、丢包和队列参数。Go 控制器根据逐帧物理时延和可用容量选路，
下发的下一跳绑定所选相邻节点的链路接口地址，避免经由 loopback 递归解析后偏离选定路径。
FRR OSPF／IS-IS 为底层可达性使用固定跳数度量，TE `max-bw` 表示配置中的端口额定上限，
从 bit/s 转换为 byte/s；瞬时可用容量由控制器约束与内核整形执行，不逐帧重写全网 IGP／TE 配置。
每次更新先关闭失效链路、更新参数，再打开新链路。同一节点的连续整形命令使用 `tc -batch`；
执行器只进入本轮创建的容器命名空间。全体节点更新完成后才读回接口和队列，报告保存每帧应用及通道耗时。
星座命名空间关闭默认及逐接口 `rp_filter` 并在物理读回时核验，支持业务正反向路径经过不同卫星。
只设置 `conf.all.rp_filter=0` 不足以覆盖接口继承值；实际运行曾出现请求到达目的端、回复被中间节点丢弃。
该设置仅作用于本轮自有星座及新建 N3 接口，不修改宿主机接口。

全图同一采样时刻通过 `POST /api/v1/topology/events/batch` 原子写入一个拓扑版本。
请求最多 10000 个事件／8 MiB；全部事件遵守序号、去重和主实例检查，任一事件或持久化失败则整体不生效。
路由发生变化时使用现有控制器重新提交；路由不变时记录新预览与已有路由一致，避免把版本变化当作必须重写每个 FIB。
设备健康检查和状态读取按既有批大小并发执行，最多 32 个；全部读取完成后才继续。
路由提交仍保持原顺序，并保留每批核验和失败回滚，降低逐帧更新的串行读取开销。

统一协议回归先保持历元快照，完成 OSPF/MPLS/SRv6/PCEP/EVPN 和云故障验证。
随后 `PhysicalReplay` 在同一 FRR／5G 网络上按显式的 1:1 时间运行全部物理采样。
驱动器如果落后一个完整步长就明确失败，不会静默跳帧或加速。
最后在结束时刻的快照上验证原有星上自治和地面恢复。报告分别说明这些运行阶段。
地面恢复后先重新读回全部实际接口的启停状态和队列参数，再发布这一保持快照的新观测时间。
控制器的遥测过期保护继续有效；`physical-held-snapshot.json` 区分保持的轨道时刻与实际观测时间。

## 验收证据

- `scenario.json`：逐星根数／输入哈希、每帧位置速度、物理连接、参数、连通分量。
- `deployment/deployment-manifest.json`：可能连接数量与初始有效连接数量分别记录。
- `tools/preflight_physical.py --scenario ... --output ...`：逐帧调用实际 Go 算路器，校验业务和 N3 意图的主备可行性；只属于离线预检查。平台还在创建云资源前强制检查初始业务路径。
- `reports/physical-packets.json` 和 `.pcap`：独立真实报文验证。使用编译出的距离／时延和显式 2／8 Mbit/s 容量上限变体，检查 RTT、传输速率、计算出的接触撤销、配置丢包和恢复。
- 平台原始目录的 `physical-model.json`、`physical-jobs-*.json`、`physical-channels-*.json`、`physical-replay.json`：全图更新命令、内核读回、模型／墙钟时间、更新耗时和路由变化。
- `reports/platform-runtime.json`：同轮整体运行。新增四项物理检查全部通过才允许物理模式报告完整集成成功。

生成配置或独立通道通过不代表 120／360 星完整平台运行通过；历史逻辑图报告也不能作为新物理模式证据。
可见性只是建链的必要条件，参考链路预算需要任务数据校准。单机内核限速与延迟有调度误差，不能作为硬件线速保证。

实现参考：[SGP4 库的根数接口](https://github.com/brandon-rhodes/python-sgp4)、
[Linux netem 参数及限制](https://man7.org/linux/man-pages/man8/tc-netem.8.html)、
[FRR 链路参数](https://docs.frrouting.org/en/latest/zebra.html)。
