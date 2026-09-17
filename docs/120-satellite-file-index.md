# 120 星工程逐文件索引

[返回学习手册](120-satellite-learning-guide.md)

核对日期：2026-09-15。基准入口：`make test-physical-runtime CONSTELLATION_CONFIG=scenarios/constellations/leo-120.config.json`，默认物理模式。

下表逐个列出梳理开始时的 **405 个工程文件**（按项目忽略规则排除 reports、artifacts、依赖缓存和编译目录；包含已有未忽略日志）。本次新增的三份学习资料另列，不混入原工程统计。

**分类是相对于具体调用分支，不是删除建议。** 被导入、被构建、被哈希记录、被执行是不同事实。同一文件可能包含主线与可选分支，表内明确说明。源码静态追踪及已有证据核对不等于本次重新执行全网。

| 类别 | 数量 | 解释 |
|---|---:|---|
| A | 13 | 主线输入、编译和预览 |
| B | 67 | 主线运行代码与实际模板 |
| C | 46 | 主线构建／前置验收；业务阶段不调用的功能 |
| D | 15 | 完整协议分支或可选能力 |
| E | 179 | 独立实验／其他入口／其他规模 |
| F | 20 | 回归测试、质量验收和主机准备 |
| G | 53 | 文档、接口合同与工程维护 |
| H | 12 | 其他专项的已有生成物 |

## 新增：持续实时与前端文件

下列文件在原始 405 文件清单之后新增，不纳入上表的历史计数。实时主线的对应关系见 [学习手册第 0 节](120-satellite-learning-guide.md#0-新增持续实时模式从哪里开始)。原清单中的 `Makefile`、5G 启动脚本、平台部署、Go 拓扑存储和 Rust 路由代码也增加了实时模式支持。

| 文件 | 与持续实时流程的关系 |
|---|---|
| [lab/live/service.py](../lab/live/service.py) | 主线：单实例常驻服务，启动、查询、停止。 |
| [lab/live/runtime.py](../lab/live/runtime.py) | 主线：实际部署与持续计算、下发、采集、清理。 |
| [lab/live/model.py](../lab/live/model.py) | 主线：无有限帧列表的轨道时钟、动态接口与地址。 |
| [lab/live/heartbeat.py](../lab/live/heartbeat.py) | 主线：批量进入各星网络命名空间发送心跳。 |
| [lab/live/progress.py](../lab/live/progress.py) | 启动主线：记录实际检查点，定时读取本轮容器、节点、Pod 和警告信息。 |
| [lab/live/storage.py](../lab/live/storage.py) | 主线：原子快照、滚动日志与实际探测统计。 |
| [frontend/server.py](../frontend/server.py) | 展示入口：读取实时状态、提供固定启动／停止 API。 |
| [frontend/index.html](../frontend/index.html) | 展示入口：页面骨架。 |
| [frontend/app.js](../frontend/app.js) | 展示主线：实时／历史切换、业务页面与交互。 |
| [frontend/globe.js](../frontend/globe.js) | 展示主线：模型位置、轨道、链路、太阳标记与旋转／缩放交互。 |
| [frontend/solar.js](../frontend/solar.js) | 展示主线：按模型时刻计算近似太阳方向与日地距离，不参与轨道传播。 |
| [frontend/earth-surface.js](../frontend/earth-surface.js) | 展示主线：WebGL 地表与昼夜光照，无 WebGL 时由 Canvas 绘制。 |
| `frontend/assets/earth-day.png`、`frontend/assets/earth-night.png` | 展示素材：本地 NASA 历史地表与夜光贴图，不是实时遥感。 |
| [frontend/assets/CREDITS.md](../frontend/assets/CREDITS.md) | 图像来源、署名和太阳坐标近似说明，不在程序执行链中。 |
| [frontend/style.css](../frontend/style.css) | 展示样式与手机布局。 |
| [frontend/favicon.svg](../frontend/favicon.svg) | 页面图标。 |
| [lab/live/test_live.py](../lab/live/test_live.py) | 独立测试：时钟、地址、窗口、服务与故障处理。 |
| [lab/live/test_progress.py](../lab/live/test_progress.py) | 独立测试：检查点成功／失败、会话隔离、诊断失败与 Kubernetes 状态解析。 |
| [lab/live/validate_onboard_dynamic.py](../lab/live/validate_onboard_dynamic.py) | 独立真实内核验收：自治期间切换下一跳、撤回及恢复。 |
| [frontend/test_server.py](../frontend/test_server.py) | 独立测试：API、文件访问与状态边界。 |
| [frontend/browser_check.py](../frontend/browser_check.py) | 独立验收：历史页面回归。 |
| [frontend/live_browser_check.py](../frontend/live_browser_check.py) | 独立验收：已运行星座的实时浏览器检查。 |
| [frontend/globe_browser_check.py](../frontend/globe_browser_check.py) | 独立验收：太阳位置／光照、缩放、全屏、双指交互与无 WebGL 降级。 |
| [frontend/startup_browser_check.py](../frontend/startup_browser_check.py) | 独立验收：启动等待、失败、诊断过期、断连、导出与移动布局；使用明确的隔离测试数据。 |
| [lab/live/README.md](../lab/live/README.md) | 实时运行说明，不在程序执行链中。 |
| [frontend/README.md](../frontend/README.md) | 前端使用说明，不在程序执行链中。 |

## A. 主线输入、编译和预览

| 文件 | 与流程的关系 |
|---|---|
| [Makefile](../Makefile) | 目标调度；physical-runtime → build／test-onboard／platform-configuration → 5G 外层脚本。 |
| [cmd/sfctl/constellation.go](../cmd/sfctl/constellation.go) | 解析生成器配置与 CLI；调用 constellation.Generate。 |
| [cmd/sfctl/main.go](../cmd/sfctl/main.go) | 命令分派和 scenario validate；experiment 子命令属于内存回归分支。 |
| [go.mod](../go.mod) | Go 模块和依赖版本。 |
| [go.sum](../go.sum) | Go 依赖校验和。 |
| [internal/constellation/constellation.go](../internal/constellation/constellation.go) | 生成身份／面槽／逻辑种子与意图，并预检查逻辑主备可行性。 |
| [lab/platform/render.py](../lab/platform/render.py) | 通过 Fabric 生成部署预览；预览文件不直接用于启动。 |
| [scenarios/constellations/leo-120.config.json](../scenarios/constellations/leo-120.config.json) | 当前 120 星、12 面、4 网关、8 条网关意图的规模输入。 |
| [scenarios/constellations/physical-defaults.json](../scenarios/constellations/physical-defaults.json) | 当前轨道、时间、地理站点、终端和链路参数输入。 |
| [tools/ephemeris_contacts.py](../tools/ephemeris_contacts.py) | 复用几何／TEME／测量函数；OEM 模式还使用 OEM 读取。 |
| [tools/physical_constellation.py](../tools/physical_constellation.py) | 逐星传播、可见性／终端调度、物理帧和连接并集生成。 |
| [tools/requirements-orbit.txt](../tools/requirements-orbit.txt) | Python 轨道依赖准备清单。 |
| [tools/tle_to_oem.py](../tools/tle_to_oem.py) | 物理生成器复用 propagate 函数；当前未执行此文件的 OEM 导出 CLI。 |

## B. 主线运行代码与实际模板

| 文件 | 与流程的关系 |
|---|---|
| [cmd/sf-controller/main.go](../cmd/sf-controller/main.go) | 真实 Go 控制器入口，启用 FRR、HTTP、Lease 和持久化。 |
| [deploy/compose/docker-compose.yaml](../deploy/compose/docker-compose.yaml) | NOC 读取监控镜像部分，动态生成本轮 Compose；不直接启动其 controller 服务。 |
| [deploy/helm/starfabric/Chart.yaml](../deploy/helm/starfabric/Chart.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/NOTES.txt](../deploy/helm/starfabric/templates/NOTES.txt) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/_helpers.tpl](../deploy/helm/starfabric/templates/_helpers.tpl) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/cilium-network-policy.yaml](../deploy/helm/starfabric/templates/cilium-network-policy.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/configmap.yaml](../deploy/helm/starfabric/templates/configmap.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/deployment.yaml](../deploy/helm/starfabric/templates/deployment.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/leader-rbac.yaml](../deploy/helm/starfabric/templates/leader-rbac.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/pdb.yaml](../deploy/helm/starfabric/templates/pdb.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/pvc.yaml](../deploy/helm/starfabric/templates/pvc.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/service.yaml](../deploy/helm/starfabric/templates/service.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/templates/serviceaccount.yaml](../deploy/helm/starfabric/templates/serviceaccount.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/helm/starfabric/values.yaml](../deploy/helm/starfabric/values.yaml) | Helm chart 默认值／模板；Cloud.start() 安装，并由动态场景和参数覆盖。 |
| [deploy/observability/grafana/dashboards/starfabric.json](../deploy/observability/grafana/dashboards/starfabric.json) | 本轮 Grafana 看板模板。 |
| [deploy/observability/loki.yaml](../deploy/observability/loki.yaml) | 本轮 Loki 模板。 |
| [deploy/observability/otel-collector.yaml](../deploy/observability/otel-collector.yaml) | 本轮 Collector 配置的模板。 |
| [deploy/observability/tempo.yaml](../deploy/observability/tempo.yaml) | 本轮 Tempo 模板。 |
| [internal/adapter/adapter.go](../internal/adapter/adapter.go) | 设备接口／注册表被使用；内存设备实现不用于本次实网。 |
| [internal/adapter/frr/frr.go](../internal/adapter/frr/frr.go) | FRR 路由写入、状态读取和回滚。 |
| [internal/adapter/frr/http.go](../internal/adapter/frr/http.go) | 控制器访问 Python FRR Agent 的 HTTP 客户端。 |
| [internal/adapter/frr/ping.go](../internal/adapter/frr/ping.go) | 事务内的真实路由器可达性 ping；不是 UE 持续流量生成器。 |
| [internal/api/server.go](../internal/api/server.go) | HTTP 拓扑批量更新、计划预览、提交和状态服务。 |
| [internal/app/app.go](../internal/app/app.go) | 协调 Store／Planner／Reconciler／设备／指标。 |
| [internal/durable/file.go](../internal/durable/file.go) | 持久状态写盘。 |
| [internal/leadership/kubernetes.go](../internal/leadership/kubernetes.go) | Lease 单写者选举。 |
| [internal/model/model.go](../internal/model/model.go) | 节点、链路、意图、计划、事件、设备状态数据结构。 |
| [internal/observability/log.go](../internal/observability/log.go) | 结构化日志。 |
| [internal/observability/metrics.go](../internal/observability/metrics.go) | 指标。 |
| [internal/observability/otel.go](../internal/observability/otel.go) | OpenTelemetry 追踪。 |
| [internal/planner/planner.go](../internal/planner/planner.go) | 容量约束、主备路径、有效期及逐节点路由计划。 |
| [internal/policy/path.go](../internal/policy/path.go) | 最短路、过滤和主备约束。 |
| [internal/policy/plugin.go](../internal/policy/plugin.go) | 策略注册和调用。 |
| [internal/reconcile/reconciler.go](../internal/reconcile/reconciler.go) | 准备、分批提交、读回、验证、回滚、持久化。 |
| [internal/scenario/scenario.go](../internal/scenario/scenario.go) | 场景加载校验供主线使用；独立实验运行器是其他分支。 |
| [internal/topology/store.go](../internal/topology/store.go) | 拓扑版本、去重、事件顺序、批量原子更新与恢复。 |
| [internal/validation/validation.go](../internal/validation/validation.go) | 提交前验证计划、拓扑版本和有效性。 |
| [internal/verification/routeprobe.go](../internal/verification/routeprobe.go) | 读实际路由验证路径；组合多个验证器。 |
| [lab/cloud/runtime.py](../lab/cloud/runtime.py) | 复用 Cluster 构建、kind、Cilium／Hubble、镜像和存储工具；不调用其独立 main。 |
| [lab/containerlab/protocol-matrix/configs/vtysh.conf](../lab/containerlab/protocol-matrix/configs/vtysh.conf) | 当前全星座 FRR 容器实际挂载的 CLI 配置。 |
| [lab/platform/capture.py](../lab/platform/capture.py) | 在所有卫星命名空间抓取实际包。 |
| [lab/platform/channel.py](../lab/platform/channel.py) | 物理量转换成 netem 命令和 FRR 带宽／度量。 |
| [lab/platform/cloud.py](../lab/platform/cloud.py) | 云控制器／Agent／动态 Helm 值；failover 和 policy 仅 full 执行。 |
| [lab/platform/constellation_runtime.py](../lab/platform/constellation_runtime.py) | 全量 FRR、接线、探测、N3、每星自治及清理。 |
| [lab/platform/fabric.py](../lab/platform/fabric.py) | 物理场景到地址／接口／FRR／N3 业务的共同绑定器。 |
| [lab/platform/frr_agent.py](../lab/platform/frr_agent.py) | 有认证和限制范围的 vtysh／ping 执行服务。 |
| [lab/platform/packets.py](../lab/platform/packets.py) | PCAP 解码，同包封装和原 N3 GTP-U 验证。 |
| [lab/platform/physics_jobs.py](../lab/platform/physics_jobs.py) | 批量队列更新、命名空间执行、接口和队列读回。 |
| [lab/platform/physics_runtime.py](../lab/platform/physics_runtime.py) | 31 帧 1:1 回放、发布拓扑、按需提交、保持快照刷新。 |
| [lab/platform/protocols.py](../lab/platform/protocols.py) | 两种模式共用 setup 和 native 抓包；exercise 仅 full 执行。 |
| [lab/platform/resources.py](../lab/platform/resources.py) | 本轮 CPU／inotify 预算分配和恢复。 |
| [lab/platform/run.py](../lab/platform/run.py) | 主函数和 Platform；选择 physics／full、编排阶段、核验和写报告。 |
| [lab/platform/wire.py](../lab/platform/wire.py) | 执行实际命名空间接线任务。 |
| [lab/unified/noc.py](../lab/unified/noc.py) | 本轮监控配置生成、服务启动与关联查询。 |
| [lab/unified/run.py](../lab/unified/run.py) | 父类 Runtime 的命令／HTTP／时间线／ping 等；旧四节点方法被子类覆盖。 |
| [ntn/single-pc/core-images.yaml](../ntn/single-pc/core-images.yaml) | 核心网 Compose 覆盖。 |
| [ntn/single-pc/gnb-image.yaml](../ntn/single-pc/gnb-image.yaml) | gNB Compose 镜像覆盖。 |
| [ntn/single-pc/prepare_lab.py](../ntn/single-pc/prepare_lab.py) | 检查同名容器的所有权与停止状态。 |
| [ntn/single-pc/run.sh](../ntn/single-pc/run.sh) | 5G 生命周期和 --platform 分派，最后清理 5G 栈。 |
| [ntn/single-pc/ue-image.yaml](../ntn/single-pc/ue-image.yaml) | UE Compose 镜像覆盖。 |
| [ntn/single-pc/verify.py](../ntn/single-pc/verify.py) | 5G 已建立会话及 N2／N3 的基线验收。 |
| [ntn/single-pc/versions.env](../ntn/single-pc/versions.env) | 上游 commit 与镜像固定版本。 |
| [onboard/config.example.json](../onboard/config.example.json) | 每星配置模板，运行器覆盖 ID 和实际备用路由。 |
| [onboard/src/lib.rs](../onboard/src/lib.rs) | 导出模块及原子写工具。 |
| [onboard/src/main.rs](../onboard/src/main.rs) | 心跳、状态 API、自治超时及接管撤回。 |
| [onboard/src/routing.rs](../onboard/src/routing.rs) | Linux 备用路由安装和撤回。 |
| [onboard/src/state.rs](../onboard/src/state.rs) | 各星连接／generation／fallback 持久状态。 |

## C. 主线构建／前置验收；业务阶段不调用的功能

| 文件 | 与流程的关系 |
|---|---|
| [.dockerignore](../.dockerignore) | 控制镜像构建上下文。 |
| [Dockerfile](../Dockerfile) | 云平台构建镜像；构建阶段包括 go test 和 Go 二进制编译。 |
| [cmd/sf-inventory/main.go](../cmd/sf-inventory/main.go) | 随 build 编译的资产导入工具，业务阶段不执行。 |
| [cmd/sf-openconfig-emulator/main.go](../cmd/sf-openconfig-emulator/main.go) | 随 build 编译的 OpenConfig 模拟设备，业务阶段不执行。 |
| [cmd/sf-openconfig-probe/main.go](../cmd/sf-openconfig-probe/main.go) | 随 build 编译的 OpenConfig 探测器，业务阶段不执行。 |
| [cmd/sf-quic-probe/main.go](../cmd/sf-quic-probe/main.go) | 随 build 编译的 QUIC 专项工具，业务阶段不执行。 |
| [cmd/sfctl/constellation_test.go](../cmd/sfctl/constellation_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [cmd/sfctl/scenario_test.go](../cmd/sfctl/scenario_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/adapter/frr/frr_test.go](../internal/adapter/frr/frr_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/adapter/frr/http_test.go](../internal/adapter/frr/http_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/adapter/frr/reconcile_drift_test.go](../internal/adapter/frr/reconcile_drift_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/adapter/openconfig/adapter.go](../internal/adapter/openconfig/adapter.go) | 控制器可选 OpenConfig adapter 的编译／测试代码；本轮选择 FRR。 |
| [internal/adapter/openconfig/adapter_persistence_test.go](../internal/adapter/openconfig/adapter_persistence_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/adapter/openconfig/client.go](../internal/adapter/openconfig/client.go) | 控制器可选 OpenConfig adapter 的编译／测试代码；本轮选择 FRR。 |
| [internal/adapter/openconfig/client_test.go](../internal/adapter/openconfig/client_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/adapter/openconfig/gribi.go](../internal/adapter/openconfig/gribi.go) | 控制器可选 OpenConfig adapter 的编译／测试代码；本轮选择 FRR。 |
| [internal/adapter/openconfig/transport.go](../internal/adapter/openconfig/transport.go) | 控制器可选 OpenConfig adapter 的编译／测试代码；本轮选择 FRR。 |
| [internal/api/grpc_test.go](../internal/api/grpc_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/api/server_test.go](../internal/api/server_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/app/app_test.go](../internal/app/app_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/constellation/constellation_test.go](../internal/constellation/constellation_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/inventory/netbox.go](../internal/inventory/netbox.go) | 资产导入工具依赖及测试模块，本轮不导入。 |
| [internal/inventory/netbox_test.go](../internal/inventory/netbox_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/leadership/kubernetes_test.go](../internal/leadership/kubernetes_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/ois/runtime.go](../internal/ois/runtime.go) | 独立 OIS／分片模块；镜像构建测试涉及，当前主控制链不调用。 |
| [internal/ois/runtime_test.go](../internal/ois/runtime_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/planner/planner_test.go](../internal/planner/planner_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/policy/path_test.go](../internal/policy/path_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/policy/plugin_test.go](../internal/policy/plugin_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/predictive/engine_test.go](../internal/predictive/engine_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/reconcile/reads_test.go](../internal/reconcile/reads_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/reconcile/reconciler_test.go](../internal/reconcile/reconciler_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/report/html.go](../internal/report/html.go) | sfctl experiment 的 HTML 输出能力，编入 sfctl；不生成平台 JSON 报告。 |
| [internal/sharding/rendezvous.go](../internal/sharding/rendezvous.go) | 独立 OIS／分片模块；镜像构建测试涉及，当前主控制链不调用。 |
| [internal/sharding/rendezvous_test.go](../internal/sharding/rendezvous_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/testutil/topology.go](../internal/testutil/topology.go) | Go 测试使用的拓扑样例工具，不是运行时输入。 |
| [internal/topology/batch_test.go](../internal/topology/batch_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/topology/store_test.go](../internal/topology/store_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/verification/otg_test.go](../internal/verification/otg_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [internal/verification/routeprobe_test.go](../internal/verification/routeprobe_test.go) | Go 包内回归测试；云镜像构建阶段执行（可能命中构建层缓存），不是运行期业务步骤。 |
| [lab/containerlab/protocol-matrix/configs/daemons](../lab/containerlab/protocol-matrix/configs/daemons) | 本轮仅被源码哈希清单记录；节点 daemons 由 network() 动态生成。 |
| [onboard/Cargo.lock](../onboard/Cargo.lock) | Rust 依赖锁定；test-onboard 使用 --locked。 |
| [onboard/Cargo.toml](../onboard/Cargo.toml) | Rust 项目和依赖。 |
| [onboard/run-closed-loop.py](../onboard/run-closed-loop.py) | Make 前置专项：真实 Rust 进程／状态／签名升级，路由为 dry-run；后续全星座执行真实路由。 |
| [onboard/src/update.rs](../onboard/src/update.rs) | 签名 A/B 更新模块，前置星上测试覆盖；业务阶段不做逐星升级。 |
| [scenarios/leo-resilient.json](../scenarios/leo-resilient.json) | 被复制进镜像的默认样例；本轮实际场景由 Helm ConfigMap 覆盖。 |

## D. 完整协议分支或可选能力

| 文件 | 与流程的关系 |
|---|---|
| [deploy/helm/starfabric/templates/servicemonitor.yaml](../deploy/helm/starfabric/templates/servicemonitor.yaml) | 仅存在 monitoring.coreos.com/v1 API 时生成；本轮 NOC 直接抓取。 |
| [deploy/helm/starfabric/values-ha.yaml](../deploy/helm/starfabric/values-ha.yaml) | 另一部署入口的 HA 示例；本轮动态生成 platform-values.json。 |
| [gen/starfabric/v1/control.pb.go](../gen/starfabric/v1/control.pb.go) | 预生成的 Protobuf／gRPC Go 代码，编译依赖；当前业务走 HTTP API。 |
| [gen/starfabric/v1/control_grpc.pb.go](../gen/starfabric/v1/control_grpc.pb.go) | 预生成的 Protobuf／gRPC Go 代码，编译依赖；当前业务走 HTTP API。 |
| [gen/starfabric/v1/topology.pb.go](../gen/starfabric/v1/topology.pb.go) | 预生成的 Protobuf／gRPC Go 代码，编译依赖；当前业务走 HTTP API。 |
| [internal/api/grpc.go](../internal/api/grpc.go) | 可选 gRPC 服务，当前平台未设置 gRPC listener。 |
| [internal/app/predictive.go](../internal/app/predictive.go) | 控制器预测调度／恢复模块；物理回放通过 batch API，不创建预测 schedules。 |
| [internal/predictive/engine.go](../internal/predictive/engine.go) | 接触窗口预测算路；当前物理业务没有运行该切换分支。 |
| [internal/verification/otg.go](../internal/verification/otg.go) | 可选 OTG 流量验证，本轮未设置 --otg-api。 |
| [lab/platform/pce.py](../lab/platform/pce.py) | 被导入；仅完整协议 exercise() 实例化 PCEP 服务。 |
| [lab/platform/validate_physics_packets.py](../lab/platform/validate_physics_packets.py) | 独立 netem 时延／容量／丢包报文专项，由 test-physical-packets 执行。 |
| [lab/platform/validate_rpf_packets.py](../lab/platform/validate_rpf_packets.py) | 独立非对称路径 rp_filter 复现验证。 |
| [ntn/single-pc/down.sh](../ntn/single-pc/down.sh) | 手工停止保留的 5G 实验；默认 run.sh 有自己的 EXIT 清理函数。 |
| [onboard/.cargo/config.toml](../onboard/.cargo/config.toml) | aarch64 交叉编译链接器和 QEMU runner；当前本机目标未启用该 target 设置。 |
| [tools/preflight_physical.py](../tools/preflight_physical.py) | 手工可选：使用绑定后的业务逐帧预检查可行路径。 |

## E. 独立实验／其他入口／其他规模

| 文件 | 与流程的关系 |
|---|---|
| [deploy/cilium/values.yaml](../deploy/cilium/values.yaml) | 其他部署／主机／静态监控配置；当前运行器生成或使用自己的对应配置。 |
| [deploy/gitops/application.yaml](../deploy/gitops/application.yaml) | 其他部署／主机／静态监控配置；当前运行器生成或使用自己的对应配置。 |
| [deploy/host/starfabric-mpls.conf](../deploy/host/starfabric-mpls.conf) | 其他部署／主机／静态监控配置；当前运行器生成或使用自己的对应配置。 |
| [deploy/observability/alerts/starfabric.yml](../deploy/observability/alerts/starfabric.yml) | 其他部署／主机／静态监控配置；当前运行器生成或使用自己的对应配置。 |
| [deploy/observability/grafana/provisioning/dashboards/starfabric.yaml](../deploy/observability/grafana/provisioning/dashboards/starfabric.yaml) | 其他部署／主机／静态监控配置；当前运行器生成或使用自己的对应配置。 |
| [deploy/observability/grafana/provisioning/datasources/prometheus.yaml](../deploy/observability/grafana/provisioning/datasources/prometheus.yaml) | 其他部署／主机／静态监控配置；当前运行器生成或使用自己的对应配置。 |
| [deploy/observability/prometheus.yml](../deploy/observability/prometheus.yml) | 其他部署／主机／静态监控配置；当前运行器生成或使用自己的对应配置。 |
| [lab/api/validate_contract.py](../lab/api/validate_contract.py) | API 合同专项；不由当前 120 星物理主入口执行。 |
| [lab/batfish/model-configs/r1.conf](../lab/batfish/model-configs/r1.conf) | 独立 Batfish 静态分析；不由当前 120 星物理主入口执行。 |
| [lab/batfish/model-configs/r2.conf](../lab/batfish/model-configs/r2.conf) | 独立 Batfish 静态分析；不由当前 120 星物理主入口执行。 |
| [lab/batfish/model-configs/r3.conf](../lab/batfish/model-configs/r3.conf) | 独立 Batfish 静态分析；不由当前 120 星物理主入口执行。 |
| [lab/batfish/model-configs/r4.conf](../lab/batfish/model-configs/r4.conf) | 独立 Batfish 静态分析；不由当前 120 星物理主入口执行。 |
| [lab/batfish/prepare_snapshot.sh](../lab/batfish/prepare_snapshot.sh) | 独立 Batfish 静态分析；不由当前 120 星物理主入口执行。 |
| [lab/batfish/requirements.txt](../lab/batfish/requirements.txt) | 独立 Batfish 静态分析；不由当前 120 星物理主入口执行。 |
| [lab/batfish/run.sh](../lab/batfish/run.sh) | 独立 Batfish 静态分析；不由当前 120 星物理主入口执行。 |
| [lab/batfish/validate.py](../lab/batfish/validate.py) | 独立 Batfish 静态分析；不由当前 120 星物理主入口执行。 |
| [lab/cloud/observability.py](../lab/cloud/observability.py) | 独立云或监控验收；不由当前 120 星物理主入口执行。 |
| [lab/cloud/validate.py](../lab/cloud/validate.py) | 独立云或监控验收；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/aggregate.py](../lab/containerlab/advanced-protocols/aggregate.py) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/bgpls-head.conf](../lab/containerlab/advanced-protocols/configs/bgpls-head.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/control-backup.conf](../lab/containerlab/advanced-protocols/configs/control-backup.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/control-head.conf](../lab/containerlab/advanced-protocols/configs/control-head.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/control-pce.conf](../lab/containerlab/advanced-protocols/configs/control-pce.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/control-primary.conf](../lab/containerlab/advanced-protocols/configs/control-primary.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/control-tail.conf](../lab/containerlab/advanced-protocols/configs/control-tail.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/daemons-head](../lab/containerlab/advanced-protocols/configs/daemons-head) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/daemons-router](../lab/containerlab/advanced-protocols/configs/daemons-router) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/daemons-vtep](../lab/containerlab/advanced-protocols/configs/daemons-vtep) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/pathd-head.conf](../lab/containerlab/advanced-protocols/configs/pathd-head.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/vtep-a.conf](../lab/containerlab/advanced-protocols/configs/vtep-a.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/vtep-b.conf](../lab/containerlab/advanced-protocols/configs/vtep-b.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/configs/vtysh.conf](../lab/containerlab/advanced-protocols/configs/vtysh.conf) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/control.clab.yml](../lab/containerlab/advanced-protocols/control.clab.yml) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/control_loop.py](../lab/containerlab/advanced-protocols/control_loop.py) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/evpn.clab.yml](../lab/containerlab/advanced-protocols/evpn.clab.yml) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/evpn_loop.py](../lab/containerlab/advanced-protocols/evpn_loop.py) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/pcep_server.py](../lab/containerlab/advanced-protocols/pcep_server.py) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/run-closed-loop.sh](../lab/containerlab/advanced-protocols/run-closed-loop.sh) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/setup-srv6.sh](../lab/containerlab/advanced-protocols/setup-srv6.sh) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/advanced-protocols/setup-vxlan.sh](../lab/containerlab/advanced-protocols/setup-vxlan.sh) | 独立高级协议实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/Makefile](../lab/containerlab/phase1-frr-otg/Makefile) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/assert_closed_loop.py](../lab/containerlab/phase1-frr-otg/assert_closed_loop.py) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/assert_metrics.py](../lab/containerlab/phase1-frr-otg/assert_metrics.py) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/configs/daemons](../lab/containerlab/phase1-frr-otg/configs/daemons) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/configs/r1.conf](../lab/containerlab/phase1-frr-otg/configs/r1.conf) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/configs/r2.conf](../lab/containerlab/phase1-frr-otg/configs/r2.conf) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/configs/r3.conf](../lab/containerlab/phase1-frr-otg/configs/r3.conf) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/configs/r4.conf](../lab/containerlab/phase1-frr-otg/configs/r4.conf) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/configs/vtysh.conf](../lab/containerlab/phase1-frr-otg/configs/vtysh.conf) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/events/r1-r2-down.json](../lab/containerlab/phase1-frr-otg/events/r1-r2-down.json) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/events/r2-r1-down.json](../lab/containerlab/phase1-frr-otg/events/r2-r1-down.json) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/otg.yaml](../lab/containerlab/phase1-frr-otg/otg.yaml) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/render_otg.py](../lab/containerlab/phase1-frr-otg/render_otg.py) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/run-closed-loop.sh](../lab/containerlab/phase1-frr-otg/run-closed-loop.sh) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/run.sh](../lab/containerlab/phase1-frr-otg/run.sh) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/topology.clab.yml](../lab/containerlab/phase1-frr-otg/topology.clab.yml) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/wait_converged.py](../lab/containerlab/phase1-frr-otg/wait_converged.py) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/phase1-frr-otg/wait_ready.py](../lab/containerlab/phase1-frr-otg/wait_ready.py) | 独立 FRR／OTG 四节点实验；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/closed_loop.py](../lab/containerlab/protocol-matrix/closed_loop.py) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/apply-ldp.sh](../lab/containerlab/protocol-matrix/configs/apply-ldp.sh) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/bgpls.reference.conf](../lab/containerlab/protocol-matrix/configs/bgpls.reference.conf) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/evpn.reference.conf](../lab/containerlab/protocol-matrix/configs/evpn.reference.conf) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/gw-a.conf](../lab/containerlab/protocol-matrix/configs/gw-a.conf) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/pcep.reference.conf](../lab/containerlab/protocol-matrix/configs/pcep.reference.conf) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/sat-a.conf](../lab/containerlab/protocol-matrix/configs/sat-a.conf) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/sat-b.conf](../lab/containerlab/protocol-matrix/configs/sat-b.conf) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/sat-c.conf](../lab/containerlab/protocol-matrix/configs/sat-c.conf) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/configs/srv6.reference.conf](../lab/containerlab/protocol-matrix/configs/srv6.reference.conf) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/run-closed-loop.sh](../lab/containerlab/protocol-matrix/run-closed-loop.sh) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/topology.clab.yml](../lab/containerlab/protocol-matrix/topology.clab.yml) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/containerlab/protocol-matrix/verify.sh](../lab/containerlab/protocol-matrix/verify.sh) | 独立基础协议矩阵；不由当前 120 星物理主入口执行。 |
| [lab/dataplane/run_xdp.py](../lab/dataplane/run_xdp.py) | 独立 XDP 数据面；不由当前 120 星物理主入口执行。 |
| [lab/dataplane/xdp_probe.bpf.c](../lab/dataplane/xdp_probe.bpf.c) | 独立 XDP 数据面；不由当前 120 星物理主入口执行。 |
| [lab/inventory/validate.py](../lab/inventory/validate.py) | 资产导入专项；不由当前 120 星物理主入口执行。 |
| [lab/kne/starfabric.textproto](../lab/kne/starfabric.textproto) | KNE 设备测试床；不由当前 120 星物理主入口执行。 |
| [lab/kne/testbed.textproto](../lab/kne/testbed.textproto) | KNE 设备测试床；不由当前 120 星物理主入口执行。 |
| [lab/linux-networking/l2_loop_prevention.sh](../lab/linux-networking/l2_loop_prevention.sh) | Linux 网络专项；不由当前 120 星物理主入口执行。 |
| [lab/linux-networking/probe.py](../lab/linux-networking/probe.py) | Linux 网络专项；不由当前 120 星物理主入口执行。 |
| [lab/linux-networking/run.sh](../lab/linux-networking/run.sh) | Linux 网络专项；不由当前 120 星物理主入口执行。 |
| [lab/linux-networking/validate.py](../lab/linux-networking/validate.py) | Linux 网络专项；不由当前 120 星物理主入口执行。 |
| [lab/openconfig/assert_closed_loop.py](../lab/openconfig/assert_closed_loop.py) | 独立 OpenConfig 实验；不由当前 120 星物理主入口执行。 |
| [lab/openconfig/event-primary-degrade.json](../lab/openconfig/event-primary-degrade.json) | 独立 OpenConfig 实验；不由当前 120 星物理主入口执行。 |
| [lab/openconfig/run-closed-loop.sh](../lab/openconfig/run-closed-loop.sh) | 独立 OpenConfig 实验；不由当前 120 星物理主入口执行。 |
| [lab/openconfig/scenario.json](../lab/openconfig/scenario.json) | 独立 OpenConfig 实验；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/assemble_report.py](../lab/orbit-closed-loop/assemble_report.py) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/assert_closed_loop.py](../lab/orbit-closed-loop/assert_closed_loop.py) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/compile_replay.py](../lab/orbit-closed-loop/compile_replay.py) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/inputs/contact-plan-base.json](../lab/orbit-closed-loop/inputs/contact-plan-base.json) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/inputs/ground-stations.json](../lab/orbit-closed-loop/inputs/ground-stations.json) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/inputs/tle-catalog.json](../lab/orbit-closed-loop/inputs/tle-catalog.json) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/render_prediction.py](../lab/orbit-closed-loop/render_prediction.py) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/run-closed-loop.sh](../lab/orbit-closed-loop/run-closed-loop.sh) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/wait_contact_end.py](../lab/orbit-closed-loop/wait_contact_end.py) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/wait_converged.py](../lab/orbit-closed-loop/wait_converged.py) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/orbit-closed-loop/wait_prediction.py](../lab/orbit-closed-loop/wait_prediction.py) | 独立小网络轨道闭环；当前物理编译不读其 inputs；不由当前 120 星物理主入口执行。 |
| [lab/p4/Makefile](../lab/p4/Makefile) | 独立 P4 数据面；不由当前 120 星物理主入口执行。 |
| [lab/p4/packet_probe.py](../lab/p4/packet_probe.py) | 独立 P4 数据面；不由当前 120 星物理主入口执行。 |
| [lab/p4/program.py](../lab/p4/program.py) | 独立 P4 数据面；不由当前 120 星物理主入口执行。 |
| [lab/p4/requirements.txt](../lab/p4/requirements.txt) | 独立 P4 数据面；不由当前 120 星物理主入口执行。 |
| [lab/p4/run-closed-loop.sh](../lab/p4/run-closed-loop.sh) | 独立 P4 数据面；不由当前 120 星物理主入口执行。 |
| [lab/p4/starfabric.p4](../lab/p4/starfabric.p4) | 独立 P4 数据面；不由当前 120 星物理主入口执行。 |
| [lab/qos/qos_probe.py](../lab/qos/qos_probe.py) | QoS 专项；不由当前 120 星物理主入口执行。 |
| [lab/qos/run.sh](../lab/qos/run.sh) | QoS 专项；不由当前 120 星物理主入口执行。 |
| [lab/qos/validate.py](../lab/qos/validate.py) | QoS 专项；不由当前 120 星物理主入口执行。 |
| [lab/reliability/ha_closed_loop.py](../lab/reliability/ha_closed_loop.py) | 可靠性／HA 专项；不由当前 120 星物理主入口执行。 |
| [lab/reliability/validate.py](../lab/reliability/validate.py) | 可靠性／HA 专项；不由当前 120 星物理主入口执行。 |
| [lab/security/validate.py](../lab/security/validate.py) | 安全专项；不由当前 120 星物理主入口执行。 |
| [lab/sonic/testbed.textproto](../lab/sonic/testbed.textproto) | SONiC 设备测试床；不由当前 120 星物理主入口执行。 |
| [lab/srsran/Dockerfile](../lab/srsran/Dockerfile) | srsRAN 专项；不由当前 120 星物理主入口执行。 |
| [lab/srsran/geo_ntn_testmode.yml](../lab/srsran/geo_ntn_testmode.yml) | srsRAN 专项；不由当前 120 星物理主入口执行。 |
| [lab/srsran/validate.py](../lab/srsran/validate.py) | srsRAN 专项；不由当前 120 星物理主入口执行。 |
| [learning/lesson01-data-plane/Makefile](../learning/lesson01-data-plane/Makefile) | 小网络数据面教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson01-data-plane/topology.clab.yml](../learning/lesson01-data-plane/topology.clab.yml) | 小网络数据面教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson01-data-plane/verify.sh](../learning/lesson01-data-plane/verify.sh) | 小网络数据面教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/Makefile](../learning/lesson02-isis-control-plane/Makefile) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/configs/daemons](../learning/lesson02-isis-control-plane/configs/daemons) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/configs/r1/frr.conf](../learning/lesson02-isis-control-plane/configs/r1/frr.conf) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/configs/r2/frr.conf](../learning/lesson02-isis-control-plane/configs/r2/frr.conf) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/configs/r3/frr.conf](../learning/lesson02-isis-control-plane/configs/r3/frr.conf) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/configs/r4/frr.conf](../learning/lesson02-isis-control-plane/configs/r4/frr.conf) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/configs/vtysh.conf](../learning/lesson02-isis-control-plane/configs/vtysh.conf) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/measure-failover.py](../learning/lesson02-isis-control-plane/measure-failover.py) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/topology.clab.yml](../learning/lesson02-isis-control-plane/topology.clab.yml) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/verify.py](../learning/lesson02-isis-control-plane/verify.py) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson02-isis-control-plane/wait-ready.sh](../learning/lesson02-isis-control-plane/wait-ready.sh) | IS-IS 教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/Makefile](../learning/lesson03-bfd-convergence/Makefile) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/compare-results.py](../learning/lesson03-bfd-convergence/compare-results.py) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/configs/daemons](../learning/lesson03-bfd-convergence/configs/daemons) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/configs/r1/frr.conf](../learning/lesson03-bfd-convergence/configs/r1/frr.conf) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/configs/r2/frr.conf](../learning/lesson03-bfd-convergence/configs/r2/frr.conf) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/configs/r3/frr.conf](../learning/lesson03-bfd-convergence/configs/r3/frr.conf) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/configs/r4/frr.conf](../learning/lesson03-bfd-convergence/configs/r4/frr.conf) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/configs/vtysh.conf](../learning/lesson03-bfd-convergence/configs/vtysh.conf) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/measure-failover.py](../learning/lesson03-bfd-convergence/measure-failover.py) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/topology.clab.yml](../learning/lesson03-bfd-convergence/topology.clab.yml) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/verify.py](../learning/lesson03-bfd-convergence/verify.py) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson03-bfd-convergence/wait-ready.py](../learning/lesson03-bfd-convergence/wait-ready.py) | BFD 收敛教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/Makefile](../learning/lesson04-isis-lfa/Makefile) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/compare-results.py](../learning/lesson04-isis-lfa/compare-results.py) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/configs/daemons](../learning/lesson04-isis-lfa/configs/daemons) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/configs/r1/frr.conf](../learning/lesson04-isis-lfa/configs/r1/frr.conf) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/configs/r2/frr.conf](../learning/lesson04-isis-lfa/configs/r2/frr.conf) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/configs/r3/frr.conf](../learning/lesson04-isis-lfa/configs/r3/frr.conf) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/configs/r4/frr.conf](../learning/lesson04-isis-lfa/configs/r4/frr.conf) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/configs/vtysh.conf](../learning/lesson04-isis-lfa/configs/vtysh.conf) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/measure-failover.py](../learning/lesson04-isis-lfa/measure-failover.py) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/prove-local-repair.py](../learning/lesson04-isis-lfa/prove-local-repair.py) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/topology.clab.yml](../learning/lesson04-isis-lfa/topology.clab.yml) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/verify.py](../learning/lesson04-isis-lfa/verify.py) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [learning/lesson04-isis-lfa/wait-ready.py](../learning/lesson04-isis-lfa/wait-ready.py) | LFA 本地修复教学；不由当前 120 星物理主入口执行。 |
| [ntn/channel_emulator.py](../ntn/channel_emulator.py) | 其他 NTN 独立实验或 R17 配置；不由当前 120 星物理主入口执行。 |
| [ntn/experiment.py](../ntn/experiment.py) | 其他 NTN 独立实验或 R17 配置；不由当前 120 星物理主入口执行。 |
| [ntn/experiments/01-gateway-switch.json](../ntn/experiments/01-gateway-switch.json) | NTN 独立实验清单；不由当前 120 星物理主入口执行。 |
| [ntn/experiments/02-interruption-recovery.json](../ntn/experiments/02-interruption-recovery.json) | NTN 独立实验清单；不由当前 120 星物理主入口执行。 |
| [ntn/experiments/03-latency-variation.json](../ntn/experiments/03-latency-variation.json) | NTN 独立实验清单；不由当前 120 星物理主入口执行。 |
| [ntn/experiments/04-qos-classes.json](../ntn/experiments/04-qos-classes.json) | NTN 独立实验清单；不由当前 120 星物理主入口执行。 |
| [ntn/open5gs/amf.yaml](../ntn/open5gs/amf.yaml) | 其他 Open5GS 集成配置；本轮实际配置来自上游缓存；不由当前 120 星物理主入口执行。 |
| [ntn/open5gs/smf.yaml](../ntn/open5gs/smf.yaml) | 其他 Open5GS 集成配置；本轮实际配置来自上游缓存；不由当前 120 星物理主入口执行。 |
| [ntn/open5gs/upf.yaml](../ntn/open5gs/upf.yaml) | 其他 Open5GS 集成配置；本轮实际配置来自上游缓存；不由当前 120 星物理主入口执行。 |
| [ntn/qos-mapping.json](../ntn/qos-mapping.json) | 其他 NTN 独立实验或 R17 配置；不由当前 120 星物理主入口执行。 |
| [ntn/single-pc/geo-ntn-r17.yaml](../ntn/single-pc/geo-ntn-r17.yaml) | 其他 NTN 独立实验或 R17 配置；不由当前 120 星物理主入口执行。 |
| [ntn/single-pc/ntn-transport.json](../ntn/single-pc/ntn-transport.json) | 其他 NTN 独立实验或 R17 配置；不由当前 120 星物理主入口执行。 |
| [ntn/single-pc/validate_r17_channel.py](../ntn/single-pc/validate_r17_channel.py) | 其他 NTN 独立实验或 R17 配置；不由当前 120 星物理主入口执行。 |
| [ntn/srsran/geo_ntn.yml](../ntn/srsran/geo_ntn.yml) | 其他 srsRAN／O-RAN 集成配置；不由当前 120 星物理主入口执行。 |
| [ntn/srsran/gnb_transport.yml](../ntn/srsran/gnb_transport.yml) | 其他 srsRAN／O-RAN 集成配置；不由当前 120 星物理主入口执行。 |
| [ntn/srsran/oran_e2.yml](../ntn/srsran/oran_e2.yml) | 其他 srsRAN／O-RAN 集成配置；不由当前 120 星物理主入口执行。 |
| [ntn/starfabric-transport.json](../ntn/starfabric-transport.json) | 其他 NTN 独立实验或 R17 配置；不由当前 120 星物理主入口执行。 |
| [onboard/packaging/buildroot/starfabric-node.mk](../onboard/packaging/buildroot/starfabric-node.mk) | systemd／Buildroot／Yocto 打包；不由当前 120 星物理主入口执行。 |
| [onboard/packaging/systemd/starfabric-node.service](../onboard/packaging/systemd/starfabric-node.service) | systemd／Buildroot／Yocto 打包；不由当前 120 星物理主入口执行。 |
| [onboard/packaging/yocto/starfabric-node_0.1.0.bb](../onboard/packaging/yocto/starfabric-node_0.1.0.bb) | systemd／Buildroot／Yocto 打包；不由当前 120 星物理主入口执行。 |
| [onboard/qemu/Dockerfile](../onboard/qemu/Dockerfile) | QEMU ARM64 SIL 专项；不由当前 120 星物理主入口执行。 |
| [onboard/qemu/run-arm64-sil.sh](../onboard/qemu/run-arm64-sil.sh) | QEMU ARM64 SIL 专项；不由当前 120 星物理主入口执行。 |
| [onboard/qemu/validate.py](../onboard/qemu/validate.py) | QEMU ARM64 SIL 专项；不由当前 120 星物理主入口执行。 |
| [scenarios/constellations/leo-360.config.json](../scenarios/constellations/leo-360.config.json) | 其他规模或示例场景；不由当前 120 星物理主入口执行。 |
| [scenarios/contact-plan.example.csv](../scenarios/contact-plan.example.csv) | 其他规模或示例场景；不由当前 120 星物理主入口执行。 |
| [scenarios/frr-diamond.json](../scenarios/frr-diamond.json) | 其他规模或示例场景；不由当前 120 星物理主入口执行。 |
| [scenarios/frr-phase1-controller.json](../scenarios/frr-phase1-controller.json) | 其他规模或示例场景；不由当前 120 星物理主入口执行。 |
| [tests/ondatra/go.mod](../tests/ondatra/go.mod) | Ondatra 外部设备测试床；不由当前 120 星物理主入口执行。 |
| [tests/ondatra/go.sum](../tests/ondatra/go.sum) | Ondatra 外部设备测试床；不由当前 120 星物理主入口执行。 |

## F. 回归测试、质量验收和主机准备

| 文件 | 与流程的关系 |
|---|---|
| [lab/platform/test_contracts.py](../lab/platform/test_contracts.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [lab/platform/test_fabric.py](../lab/platform/test_fabric.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [lab/platform/test_physics_runtime.py](../lab/platform/test_physics_runtime.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [lab/platform/test_resources.py](../lab/platform/test_resources.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [ntn/test_experiment.py](../ntn/test_experiment.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [ntn/test_lab_lifecycle.py](../ntn/test_lab_lifecycle.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [scripts/bootstrap-host.sh](../scripts/bootstrap-host.sh) | 独立预处理／质量验收／主机准备工具；主物理入口未调用。 |
| [scripts/verify-host.sh](../scripts/verify-host.sh) | 独立预处理／质量验收／主机准备工具；主物理入口未调用。 |
| [tests/chaos/chaos_test.go](../tests/chaos/chaos_test.go) | 独立 Go 测试；由相应 test 入口执行，非物理回放步骤。 |
| [tests/ondatra/starfabric_test.go](../tests/ondatra/starfabric_test.go) | 独立 Go 测试；由相应 test 入口执行，非物理回放步骤。 |
| [tests/scale/scale_test.go](../tests/scale/scale_test.go) | 独立 Go 测试；由相应 test 入口执行，非物理回放步骤。 |
| [tools/contactplan.py](../tools/contactplan.py) | 独立预处理／质量验收／主机准备工具；主物理入口未调用。 |
| [tools/core_acceptance.py](../tools/core_acceptance.py) | 独立预处理／质量验收／主机准备工具；主物理入口未调用。 |
| [tools/dependency_security.py](../tools/dependency_security.py) | 独立预处理／质量验收／主机准备工具；主物理入口未调用。 |
| [tools/single_pc.py](../tools/single_pc.py) | 独立预处理／质量验收／主机准备工具；主物理入口未调用。 |
| [tools/test_ephemeris_contacts.py](../tools/test_ephemeris_contacts.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [tools/test_physical_constellation.py](../tools/test_physical_constellation.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [tools/test_single_pc.py](../tools/test_single_pc.py) | Python 回归测试，由 test-python 等质量入口执行；主物理入口不自动运行。 |
| [tools/verify_coverage.py](../tools/verify_coverage.py) | 独立预处理／质量验收／主机准备工具；主物理入口未调用。 |
| [tools/verify_single_pc_closure.py](../tools/verify_single_pc_closure.py) | 独立预处理／质量验收／主机准备工具；主物理入口未调用。 |

## G. 文档、接口合同与工程维护

| 文件 | 与流程的关系 |
|---|---|
| [# 结论：做一个“动态星地网络控制与验证平台”.md](../%23%20%E7%BB%93%E8%AE%BA%EF%BC%9A%E5%81%9A%E4%B8%80%E4%B8%AA%E2%80%9C%E5%8A%A8%E6%80%81%E6%98%9F%E5%9C%B0%E7%BD%91%E7%BB%9C%E6%8E%A7%E5%88%B6%E4%B8%8E%E9%AA%8C%E8%AF%81%E5%B9%B3%E5%8F%B0%E2%80%9D.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [.github/dependabot.yml](../.github/dependabot.yml) | CI／依赖维护或版本管理文件，不处理卫星业务。 |
| [.github/workflows/ci.yml](../.github/workflows/ci.yml) | CI／依赖维护或版本管理文件，不处理卫星业务。 |
| [.gitignore](../.gitignore) | CI／依赖维护或版本管理文件，不处理卫星业务。 |
| [README.md](../README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [api/openapi.yaml](../api/openapi.yaml) | HTTP／Protobuf 接口合同源文件；运行时不直接读取该合同文件。 |
| [api/proto/starfabric/v1/control.proto](../api/proto/starfabric/v1/control.proto) | HTTP／Protobuf 接口合同源文件；运行时不直接读取该合同文件。 |
| [api/proto/starfabric/v1/topology.proto](../api/proto/starfabric/v1/topology.proto) | HTTP／Protobuf 接口合同源文件；运行时不直接读取该合同文件。 |
| [docs/120-satellite-quickstart.md](../docs/120-satellite-quickstart.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/architecture.md](../docs/architecture.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/constellation.md](../docs/constellation.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/coverage-manifest.json](../docs/coverage-manifest.json) | 全工程覆盖／验收工具的数据清单，不是 120 星物理场景输入。 |
| [docs/coverage.md](../docs/coverage.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/hil/rf-phy-acceptance.md](../docs/hil/rf-phy-acceptance.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/independent-closures.md](../docs/independent-closures.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/one-computer-scope.md](../docs/one-computer-scope.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/physical-constellation.md](../docs/physical-constellation.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/platform-scale.md](../docs/platform-scale.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/production-readiness.md](../docs/production-readiness.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/remaining-systems.md](../docs/remaining-systems.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/runbooks/controller-recovery.md](../docs/runbooks/controller-recovery.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/runbooks/gnoi-maintenance.md](../docs/runbooks/gnoi-maintenance.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/single-pc-acceptance.md](../docs/single-pc-acceptance.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [docs/single-pc-closure.json](../docs/single-pc-closure.json) | 全工程覆盖／验收工具的数据清单，不是 120 星物理场景输入。 |
| [docs/single-pc-suite.json](../docs/single-pc-suite.json) | 全工程覆盖／验收工具的数据清单，不是 120 星物理场景输入。 |
| [docs/unified-integration.md](../docs/unified-integration.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/api/README.md](../lab/api/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/batfish/README.md](../lab/batfish/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/cloud/README.md](../lab/cloud/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/containerlab/advanced-protocols/README.md](../lab/containerlab/advanced-protocols/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/containerlab/phase1-frr-otg/README.md](../lab/containerlab/phase1-frr-otg/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/containerlab/protocol-matrix/README.md](../lab/containerlab/protocol-matrix/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/dataplane/README.md](../lab/dataplane/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/inventory/README.md](../lab/inventory/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/kne/README.md](../lab/kne/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/linux-networking/README.md](../lab/linux-networking/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/openconfig/README.md](../lab/openconfig/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/orbit-closed-loop/README.md](../lab/orbit-closed-loop/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/p4/README.md](../lab/p4/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/platform/README.md](../lab/platform/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/qos/README.md](../lab/qos/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/reliability/README.md](../lab/reliability/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/security/README.md](../lab/security/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/sonic/README.md](../lab/sonic/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [lab/unified/README.md](../lab/unified/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [learning/lesson01-data-plane/README.md](../learning/lesson01-data-plane/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [learning/lesson02-isis-control-plane/README.md](../learning/lesson02-isis-control-plane/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [learning/lesson03-bfd-convergence/README.md](../learning/lesson03-bfd-convergence/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [learning/lesson04-isis-lfa/README.md](../learning/lesson04-isis-lfa/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [ntn/README.md](../ntn/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [ntn/single-pc/README.md](../ntn/single-pc/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [onboard/README.md](../onboard/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |
| [tests/ondatra/README.md](../tests/ondatra/README.md) | 阅读材料；流程、边界或独立实验说明，不由主程序执行。 |

## H. 其他专项的已有生成物

| 文件 | 与流程的关系 |
|---|---|
| [lab/batfish/snapshot/configs/r1.conf](../lab/batfish/snapshot/configs/r1.conf) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/batfish/snapshot/configs/r2.conf](../lab/batfish/snapshot/configs/r2.conf) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/batfish/snapshot/configs/r3.conf](../lab/batfish/snapshot/configs/r3.conf) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/batfish/snapshot/configs/r4.conf](../lab/batfish/snapshot/configs/r4.conf) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/p4/build/starfabric.json](../lab/p4/build/starfabric.json) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/p4/build/starfabric.p4info.txt](../lab/p4/build/starfabric.p4info.txt) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/p4/build/starfabric.p4info.txtpb](../lab/p4/build/starfabric.p4info.txtpb) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/unified/5g-cleanup.log](../lab/unified/5g-cleanup.log) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/unified/validation-capabilities.log](../lab/unified/validation-capabilities.log) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/unified/validation-routing.log](../lab/unified/validation-routing.log) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/unified/validation-veth.log](../lab/unified/validation-veth.log) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |
| [lab/unified/validation.log](../lab/unified/validation.log) | 已有独立实验日志／编译／快照产物，不作为当前 120 星主线输入。 |

## I. 被忽略目录和生成文件也有明确位置

| 文件／目录 | 归属与阅读方法 |
|---|---|
| `reports/physical-config/120-4/seed*.json` | 本轮生成的逻辑种子与摘要。 |
| `reports/physical-config/120-4/scenario.json` | 生成后被主线读取的物理输入。 |
| `reports/physical-config/120-4/topology-summary.json` | 物理帧／连接数量摘要。 |
| `reports/physical-config/120-4/path-preflight.json` | 手工预检查结果，不是运行输入。 |
| `reports/physical-config/120-4/deployment/` | 124 份预览 frr.conf 和清单；实际运行会重生成。 |
| `reports/physical-platforms/leo-120-4/latest.json`、`reports/physical-platform-runtime.json` | 物理专项结果；优先读带规模路径。 |
| `reports/platforms/leo-120-4/latest.json`、`reports/platform-runtime.json` | 完整协议结果；不能用专项通过替代。 |
| `lab/unified/artifacts/sf-unified-d180f84868/` | 本次已通过运行的 872 个实际文件，见 TSV 明细；本文手册第 12 节按用途解释。 |
| `reports/onboard-runtime.json`、`reports/5g-sa-baseline.json`、`reports/5g-cleanup.log` | 前置 Rust 专项、5G 基线、外层清理证据；固定文件名可能被后续运行覆盖。 |
| 其他 `reports/`、`**/artifacts/` | 按 run_id／规模／实验归属，是历史或其他专项证据；主线不读取这些报告决定路由。 |
| `bin/sfctl` | 生成和校验场景的实际可执行文件。 |
| `bin/sf-controller` | 本机编译产物；本条主线的控制器运行于另外构建的云镜像中。 |
| `bin/sf-inventory`、`sf-openconfig-*`、`sf-quic-probe`、`otgen` | 其他工具产物；本轮不运行相应独立工具。 |
| `onboard/target/release/satellite-node-runtime` | 本轮 120 个自治实例实际挂载的 Rust 可执行文件。 |
| `.cache/ntn/docker_open5gs/` | 5G 上游实际 Compose 与核心网／UE／gNB 配置。不是无关缓存。 |
| `.cache/cloud-runtime/<cluster-id>/` | 动态 kind／Helm 配置、临时工具、共享持久状态；本轮会使用。 |
| `.cache/tools/` 等云工具缓存 | kind、Helm／Cilium 工具和 chart 按运行器路径读取；其余工具按各专项使用。 |
| `.cache/go-build*`、`__pycache__`、`.ruff_cache`、Rust target 其他文件 | 编译／解释器／静态检查缓存，不逐文件作为业务逻辑学习。 |
| `.cache/srsran-project`、`.cache/oran-sc-ric`、P4 虚拟环境等 | 其他专项依赖，当前 UERANSIM／FRR 物理主线不调用相应专项。 |
| `route-agent/`、`.agents/`、`.codex/`、`.git/` | 检查时无可读业务文件；route-agent 不是实际 FRR Agent。 |

## J. 本次新增学习资料

- [学习手册](120-satellite-learning-guide.md)：流程、数据结构、关键函数、实例、证据和学习顺序。
- [逐文件索引](120-satellite-file-index.md)：本文件。
- [证据 TSV 索引](120-satellite-evidence-index.tsv)：872 行实际原始文件，含工程相对路径、字节数和用途。
