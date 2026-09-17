import {
  Globe
} from './globe.js';

const paths = {
  orbit: '<ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(-35 12 12)"/><circle cx="12" cy="12" r="5"/><circle cx="20" cy="6" r="1.3"/>',
  overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  satellite: '<path d="m9 8 7 7-3 3-7-7zM5 4l4 4-4 4-4-4zM16 15l4 4-4 4-4-4zM14 4a6 6 0 0 1 6 6M14 1a9 9 0 0 1 9 9M14 10l3-3"/>',
  routing: '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M7 5h10M6 7l5 10M18 7l-5 10"/>',
  business: '<path d="M3 9a14 14 0 0 1 18 0M6 12a9 9 0 0 1 12 0M9 15a5 5 0 0 1 6 0"/><circle cx="12" cy="19" r="1"/>',
  autonomy: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/>',
  protocols: '<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
  cloud: '<path d="M6 18a5 5 0 0 1-1-10 7 7 0 0 1 13-1 5 5 0 0 1 0 11z"/>',
  capabilities: '<path d="M12 3 3 7v6c0 5 9 9 9 9s9-4 9-9V7zM8 12l3 3 5-6"/>',
  evidence: '<path d="M6 3h8l4 4v14H6zM14 3v5h5M9 12h6m-6 4h6"/>',
  learning: '<path d="M12 5C8 2 3 3 2 4v16c4-2 7-1 10 1 3-2 6-3 10-1V4c-1-1-6-2-10 1Zm0 0v16"/>',
  configuration: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2" fill="currentColor"/><circle cx="16" cy="12" r="2" fill="currentColor"/><circle cx="10" cy="18" r="2" fill="currentColor"/>',
  live: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  play: '<path d="m8 5 11 7-11 7Z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M8 5v14M16 5v14" stroke-width="3"/>',
  refresh: '<path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  external: '<path d="M14 3h7v7m0-7L10 14M11 3H3v18h18v-8"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 3"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9 8a3 3 0 0 1 6 1c0 2-3 2-3 5m0 3v.1"/>',
  bell: '<path d="M5 17h14l-2-4V8A5 5 0 0 0 7 8v5Zm5 3h4"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  ground: '<path d="M12 9v12M5 21h14M8 4a6 6 0 0 0 9 8M4 1a11 11 0 0 0 16 14M9 8l7-7"/>',
  globe: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18M5 6h14M5 18h14"/>',
  cube: '<path d="m12 2 9 5v10l-9 5-9-5V7Zm0 10v10M3 7l9 5 9-5M7 4l10 6"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/>',
  folder: '<path d="M3 6V3h7l3 3h8v15H3z"/>',
  link: '<path d="m10 13 4-4m-6 7-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 10a4 4 0 0 0 6 0l5-5a4 4 0 0 0-6-6l-2 2"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 10v7m0-11v1"/>',
  file: '<path d="M5 2h9l5 5v15H5zM14 2v6h5M8 12h8m-8 4h6"/>',
  flag: '<path d="M5 22V3m0 1c5-4 9 4 15 0v10c-6 4-10-4-15 0"/>',
  chart: '<path d="M3 3v18h18M7 15l4-5 4 3 5-7"/>',
  expand: '<path d="M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
};
const icon = (name, size = 17) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.file}</svg>`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [c]));
const num = (v, d = 0) => v == null ? '—' : Number(v).toLocaleString('en-US', {
  maximumFractionDigits: d,
  minimumFractionDigits: d
});
const time = (value) => value ? new Date(value).toISOString().slice(11, 19) : '—';
const date = value => value ? new Date(value).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' :
'暂无时间记录';
const bytes = value => value > 1024 * 1024 ? num(value / 1024 / 1024, 1) + ' MB' : value > 1024 ? num(value /
  1024, 1) + ' KB' : value + ' B';
const badge = (text, type = '') => `<span class="badge ${type}"><i class="dot"></i>${esc(text)}</span>`;
const button = (text, action, cls = '', ic = '') =>
  `<button class="btn ${cls}" data-action="${action}">${ic?icon(ic,13):''}${text}</button>`;
const fileButton = (path, label = path) =>
  `<button class="link-button" data-file="${esc(path)}">${esc(label)}</button>`;
const downloadURL = path => '/api/file?download=1&path=' + encodeURIComponent(path);

// Background refresh keeps existing DOM nodes, canvas, focus and scroll areas.
// The templates remain shared with navigation; only changed content is patched.
function patchHTML(target, html, preserve = new Set()) {
  if (!target) return;
  const range = document.createRange();
  range.selectNodeContents(target);
  const incoming = range.createContextualFragment(html);
  const key = node => {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return null;
    for (const name of ['id','data-dom-key','data-node','data-startup-stage']) {
      if (node.hasAttribute(name)) return name+':'+node.getAttribute(name);
    }
    return null;
  };
  const compatible = (a,b) => a && a.nodeType===b.nodeType && a.nodeName===b.nodeName && a.namespaceURI===b.namespaceURI;
  function patchNode(current, next) {
    if (current.nodeType !== Node.ELEMENT_NODE) {
      if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      return;
    }
    if (current.tagName === 'CANVAS' || preserve.has(current.id) || current.isEqualNode(next)) return;
    const keepAttribute = name => current.tagName==='DETAILS' && name==='open' ||
      current===document.activeElement && ['INPUT','TEXTAREA','SELECT'].includes(current.tagName) && ['value','checked','selected'].includes(name);
    for (const attr of [...current.attributes]) {
      if (!next.hasAttribute(attr.name) && !keepAttribute(attr.name)) current.removeAttribute(attr.name);
    }
    for (const attr of next.attributes) {
      if (current.getAttribute(attr.name)!==attr.value && !keepAttribute(attr.name)) {
        current.setAttributeNS(attr.namespaceURI,attr.name,attr.value);
      }
    }
    // User-owned editor values and composition stay intact during polling.
    if (current.tagName !== 'TEXTAREA') patchChildren(current,next);
  }
  function patchChildren(current, next) {
    const keyed = new Map([...current.childNodes].filter(n=>key(n)).map(n=>[key(n),n]));
    let cursor = current.firstChild;
    for (const desired of [...next.childNodes]) {
      const id = key(desired);
      let node = id ? keyed.get(id) : !key(cursor) && compatible(cursor,desired) ? cursor : null;
      if (!compatible(node,desired)) node = desired.cloneNode(true);
      if (node !== cursor) current.insertBefore(node,cursor);
      patchNode(node,desired);
      if (id) keyed.delete(id);
      cursor = node.nextSibling;
    }
    while (cursor) {
      const nextSibling = cursor.nextSibling;
      cursor.remove();
      cursor = nextSibling;
    }
  }
  patchChildren(target,incoming);
}

function writeHTML(target, html, incremental=false, preserve) {
  if (incremental) patchHTML(target,html,preserve);
  else target.innerHTML=html;
}

const filteredRegions = new Set(['node-table','node-count','cap-list','cap-count',
  'evidence-table','evidence-count','evidence-pagination','source-table','source-count','source-pagination','sun-readout','globe-zoom']);

const pages = {
  overview: ['任务总览', 'MISSION OVERVIEW', '一屏掌握星座、业务与运行状态'],
  orbit: ['星座与轨道', 'CONSTELLATION & ORBITS', '120 颗卫星的逐帧物理状态与实际链路'],
  routing: ['路由与策略', 'ROUTING & PATHS', '从业务意图，追踪到每一跳的主备路由'],
  business: ['全球业务', 'GLOBAL WORKLOADS', '全球网关业务分布、多协议承载与 5G 基线会话'],
  autonomy: ['星上自治', 'ONBOARD AUTONOMY', '每星运行时、心跳超时、备用路由与恢复状态'],
  protocols: ['协议与承载', 'PROTOCOL PROFILES', '从底层路由到封装，追踪每项协议的验收范围'],
  cloud: ['云控与可观测', 'CLOUD CONTROL & NOC', 'Kubernetes、Lease、指标、日志与追踪的统一视图'],
  capabilities: ['完整功能图谱', 'CAPABILITY ATLAS', '覆盖清单中的全部技术、独立证据与待完成能力'],
  evidence: ['实验与证据', 'RUNS & EVIDENCE', '保留运行来源，让每一个结论可以回溯'],
  learning: ['文件与学习', 'EXPLORE THE SYSTEM', '沿着系统流程，理解每一个文件的作用'],
  configuration: ['星座配置', 'CONSTELLATION CONFIGURATION', '编辑实验输入，检查参数并导出配置副本'],
  live: ['实时控制器', 'LIVE CONTROLLER', '连接正在运行的控制器，读取最新拓扑和路由状态']
};
const checkNames = {
  all_configured_nodes_deployed: '全部配置节点已部署',
  all_configured_links_have_live_ospf_neighbors: '活动链路具有真实 OSPF 邻接',
  every_configured_node_passes_real_packet_probe: '全节点实际报文探测',
  configured_gateway_intents_carry_real_packets: '网关业务意图通过实际探测',
  same_pdu_session_across_runtime: '全流程保持同一 PDU 会话',
  continuous_gtpu_business_across_faults: '持续 GTP-U 业务覆盖故障阶段',
  satellite_bidirectional_gtpu_capture: '卫星抓到双向 GTP-U 报文',
  per_satellite_orbits_drive_contacts: '逐星轨道驱动接触变化',
  geographic_gateway_visibility: '地理站点可见性参与计算',
  computed_physical_parameters_in_kernel: '物理参数已写入内核队列',
  physical_replay_on_same_frr_and_5g: '物理回放复用同一 FRR 和 5G',
  onboard_runtime_on_every_configured_satellite: '每颗卫星运行 Rust 自治进程',
  missing_ground_heartbeat_triggers_autonomy: '心跳丢失触发自治',
  onboard_installs_real_kernel_routes: '星上程序安装真实内核路由',
  gtpu_survives_ground_route_withdrawal: '撤去地面路由后业务可达',
  ground_reconnect_withdraws_fallback: '地面重连后撤回备用路由',
  kubernetes_controls_same_frr_constellation: '云控制器管理同一 FRR 星座',
  helm_deploys_real_frr_adapter: 'Helm 部署真实 FRR adapter',
  lease_single_ready_writer: 'Lease 保持单一就绪写入者',
  cilium_cni_ready: 'Cilium 网络插件就绪',
  hubble_relay_ready: 'Hubble Relay 就绪',
  noc_scrapes_same_controller_topology: 'Prometheus 采集同一控制器拓扑',
  noc_correlates_plan_fault_recovery_logs: 'Loki 关联计划、故障和恢复日志',
  noc_receives_actual_controller_trace: 'Tempo 收到实际控制器追踪',
  noc_observes_ground_outage_alert: '监控捕获地面中断告警',
  noc_grafana_backends_connected: 'Grafana 后端连接通过',
  owned_resources_cleaned: '本轮平台资源清理完成',
  real_frr_isis_network: '真实 FRR / IS-IS 网络',
  ospfv3_routes_generated_sid_locators: 'OSPFv3 发布 SID 可达性',
  evpn_all_configured_gateways: '网关 EVPN 信息交换',
  shared_scenario_and_fault_timeline: '共享场景和故障时间线'
};
const eventNames = {
  constellation_configuration_bound: '星座配置绑定完成',
  cloud_infrastructure_ready: '云基础设施就绪',
  create_frr_forwarding_network: '创建全星座转发网络',
  shared_constellation_protocols_configured: '底层协议配置完成',
  fleet_packets_verified: '全节点报文探测通过',
  'initial-commit': '初始路由计划提交',
  continuous_5g_business_started: '持续 5G 业务开始',
  business_packets_verified: '业务报文验证通过',
  physical_replay_started: '物理轨道回放开始',
  physical_frame_applied: '物理帧应用完成',
  start_real_onboard_runtimes: '启动逐星自治程序',
  ground_controller_stopped: '地面控制器中断',
  onboard_heartbeat_timeout_installed_routes: '心跳超时，星上路由接管',
  ground_owned_route_withdrawn: '撤去选定星的地面路由',
  physical_held_snapshot_reobserved: '最终物理快照重新观测',
  'ground-restored-commit': '地面恢复，路由重新提交',
  cloud_controller_started: '云控制器启动',
  continuous_runtime_started: '持续实时运行开始',
  live_contacts_created: '新的接触链路已创建',
  live_contacts_retired: '过期链路接口已回收',
  live_controller_degraded: '控制器暂时降级',
  'live-commit': '实际路由已更新'
};
let data, globe, timer, toastTimer, liveTimer, lastFocus;
let continuousTimer, liveBusy = false, loadGeneration = 0;
let state = {
  mode: new URLSearchParams(location.search).get('mode') || localStorage.getItem('sf-mode') || 'live',
  page: 'overview',
  frame: 0,
  playing: false,
  speed: 10,
  node: null,
  query: '',
  plane: 'all',
  kind: 'all',
  intent: 'n3-forward',
  workloadGateway: 'all',
  workloadPeer: 'all',
  workloadCarrier: 'all',
  workloadPage: 0,
  autonomyStage: 'autonomous',
  sourceQuery: '',
  sourceCategory: 'all',
  sourcePage: 0,
  evidenceQuery: '',
  evidenceType: 'all',
  evidencePage: 0,
  capQuery: '',
  config: null,
  run: null
};
const frame = () => data.frames[state.frame];
const plan = () => data.plans[state.frame];
const traffic = () => data.run.traffic?.find(t => t.phase === 'continuous_across_all_faults');
const artFile = name => data.run.artifacts + '/' + name;
const check = value => value === true || typeof value === 'number' && value > 0;
const satelliteNodes = () => data.nodes.filter(n => n.kind === 'satellite');
const activeNeighbors = id => [...new Set(frame().links.filter(l => l.operational_up && (l.source === id || l
  .target === id)).map(l => l.source === id ? l.target : l.source))];

function toast(message) {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
}

function saveJSON(value, name) {
  const blob = new Blob([JSON.stringify(value, null, 2) + '\n'], {
      type: 'application/json'
    }),
    url = URL.createObjectURL(blob),
    a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function spark(values, color = '#74ac99', width = 90, height = 30) {
  const valid = values.filter(v => Number.isFinite(v));
  if (valid.length < 2) return '';
  const min = Math.min(...valid),
    max = Math.max(...valid);
  const points = valid.map((v, i) =>
    `${i/(valid.length-1)*width},${height-4-(v-min)/(max-min||1)*(height-8)}`).join(' ');
  return `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
}

function chart(values, {
  color = '#77a89b',
  unit = '',
  height = 150
} = {}) {
  const valid = values.map(Number).filter(Number.isFinite);
  if (!valid.length) return '<div class="empty-state">本轮没有此项测量数据</div>';
  const w = 600,
    h = height,
    max = Math.max(...valid) * 1.16 || 1;
  const pts = valid.map((v, i) => [72 + i / (valid.length - 1 || 1) * (w - 84), h - 18 - v / max * (h - 35)]);
  const path = pts.map((p, i) => (i ? 'L' : 'M') + p.join(',')).join(' ');
  return `<div class="chart-wrap"><svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="实际测量曲线，最大值 ${num(Math.max(...valid),2)} ${esc(unit)}">${[0,.5,1].map(f=>`<line x1="72" x2="${w-12}" y1="${h-18-f*(h-35)}" y2="${h-18-f*(h-35)}" stroke="#edf1f5"/><text x="0" y="${h-15-f*(h-35)}" font-size="18" fill="#a2afbb">${num(f*max,max<10?1:0)}</text>`).join('')}<path d="${path}L${pts.at(-1)[0]},${h-18}L72,${h-18}Z" fill="${color}" opacity=".08"/><path d="${path}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg></div>`;
}

function card(title, body, link = '', subtitle = '', cls = '') {
  return `<section class="card ${cls}"><div class="card-head"><div><h2>${title}</h2>${subtitle?`<p>${subtitle}</p>`:''}</div>${link}</div><div class="card-body">${body}</div></section>`;
}

function empty(title, description, ic = 'folder') {
  return `<div class="empty-state">${icon(ic,32)}<h3>${title}</h3><p>${description}</p></div>`;
}

function stat(label, value, unit, foot, ic, values = []) {
  return `<div class="stat"><div class="stat-label">${label}${icon(ic,16)}</div><div class="stat-value">${value}<small>${unit}</small></div><div class="stat-foot">${foot}</div><div class="stat-spark">${spark(values)}</div></div>`;
}

function checkList(keys) {
  return `<div class="checklist">${keys.map(key=>`<div class="check-row"><span class="check-symbol ${check(data.run.checks?.[key])?'':'pending'}">${icon(check(data.run.checks?.[key])?'check':'clock',14)}</span><div>${esc(checkNames[key]||key)}<div class="mono">${esc(key)}</div></div><span class="push">${badge(check(data.run.checks?.[key])?'已验证':'本轮未验证',check(data.run.checks?.[key])?'green':'amber')}</span></div>`).join('')}</div>`;
}

function renderShell(incremental=false) {
  const nav = (ids) => ids.map(id =>
    `<a class="nav-link ${state.page===id?'active':''}" href="#${id}" ${state.page===id?'aria-current="page"':''}>${icon(id)}<span>${pages[id][0]}</span>${id==='orbit'?'<span class="nav-count">120</span>':''}</a>`
    ).join('');
  writeHTML(document.querySelector('#sidebar'), `<a class="brand" href="#overview">${icon('orbit',35)}<div><div class="brand-name">StarFabric<span style="color:#7eaeaa">.</span></div><p>SATELLITE OPERATIONS</p></div></a><div class="mission-pill">${icon('globe',14)}<strong>LEO · 120 星任务</strong><span>⌄</span></div><div class="nav-label">MISSION CONTROL</div><nav class="nav-group" aria-label="任务控制">${nav(['overview','orbit','routing','business','autonomy','protocols','cloud'])}</nav><div class="nav-label">WORKSPACE</div><nav class="nav-group" aria-label="工作空间">${nav(['capabilities','evidence','configuration','learning'])}</nav><div class="sidebar-bottom"><a class="nav-link ${state.page==='live'?'active':''}" href="#live" style="padding:6px 0 16px">${icon('live',16)}实时控制器 ${icon('arrow',13)}</a><div class="archive-note"><strong>${icon('clock',12)} ${state.mode==='live'?'持续实时 · 后台常驻':'历史运行 · 可交互回放'}</strong>${state.mode==='live'?'轨道与实际网络持续更新':'物理模型与验收记录'}<br>${state.mode==='live'?'关闭浏览器不会停止运行':check(data.run.checks?.owned_resources_cleaned)?'报告确认平台资源已清理':'资源清理状态以本轮报告为准'}</div><div class="flex"><div class="avatar">SF</div><div><div style="font-size:20px;color:#c6d2df">本地任务工作空间</div><div style="font-size:16px;margin-top:4px">STARFABRIC / 2.0</div></div></div></div>`, incremental);
  writeHTML(document.querySelector('#topbar'), `<div class="flex"><button class="icon-btn menu-button" data-action="menu" aria-label="打开导航">${icon('menu')}</button><div class="breadcrumb"><span>工作空间</span>${icon('chevron',11)}<span>LEO 星座</span>${icon('chevron',11)}<strong>${pages[state.page][0]}</strong></div></div><div class="top-actions"><div class="tab-segment mode-switch" aria-label="数据模式"><button data-action="mode-live" class="${state.mode==='live'?'active':''}">实时</button><button data-action="mode-history" class="${state.mode==='history'?'active':''}">历史</button></div><form class="search-box" id="global-search-form">${icon('search',14)}<input id="global-search" placeholder="搜索卫星、功能或文件" aria-label="搜索卫星、功能或文件"><kbd>⌘ K</kbd></form><div class="top-status">${badge(state.mode==='live'?'实时模式':'历史回放',state.mode==='live'?'green':'blue')}</div><div class="separator"></div><button class="icon-btn" data-action="help" aria-label="学习手册">${icon('help',17)}</button><button class="icon-btn" data-action="events" aria-label="运行事件">${icon('bell',17)}</button><div class="avatar" style="background:#eef3f8;color:#6984a0;border-color:#d7e0eb">SF</div></div>`, incremental);
  writeHTML(document.querySelector('#footer'), `<span>© 2026 StarFabric · 动态星地网络控制与验证平台</span><span>${state.mode==='live'?'实时软件在环 · 按实际采样时间更新':'数据来源：本地物理模型与运行证据'} <span class="mono">v2.0</span></span>`, incremental);
}

const runtimePhases = {
  stopped:'已停止', interrupted:'运行进程已中断', failed:'启动或运行失败', stopping:'正在停止与清理',
  starting_5g:'启动 5G 核心网与 UE', initializing:'初始化运行服务', preparing_host:'准备运行资源',
  computing_initial_orbits:'计算初始轨道', checking_resources:'检查资源容量', starting_cloud:'启动 Kubernetes 云控制平面',
  creating_124_routers:'创建本轮全部 FRR 路由器', checking_protocol_neighbors:'等待实际协议邻居收敛',
  starting_observability:'启动可观测服务', starting_controller:'启动地面控制器',
  committing_initial_routes:'提交初始业务路由', starting_5g_business:'启动持续 5G 业务',
  starting_120_onboard:'启动 120 个星上实例', running:'持续运行中', degraded:'运行中 · 控制路径降级'
};
const runtimeName = value => runtimePhases[value] || value || '等待启动';
const uptime = () => Math.max(0,Math.floor((Date.now()-new Date(data.runtime.started_at).getTime())/1000));
const durationLabel = seconds => `${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m ${Math.floor(seconds%60)}s`;

function runtimeControls(runtime) {
  return runtime.active ? button('停止运行','runtime-stop','','pause') : button('启动 120 星','runtime-start','primary','play');
}

function renderPending(value, incremental=false) {
  const scroll = scrollY;
  const oldLog = document.querySelector('#startup-log');
  const logScroll = oldLog && oldLog.scrollTop;
  const followLog = !oldLog || oldLog.scrollHeight-oldLog.scrollTop-oldLog.clientHeight < 30;
  const taskScroll = document.querySelector('.startup-task-list')?.scrollTop || 0;
  const tableScroll = [...document.querySelectorAll('#main .table-wrap')].map(e=>e.scrollLeft);
  clearInterval(liveTimer);
  globe?.destroy(); globe=null;
  data = {...value,pending:true,run:{checks:{}},nodes:[],sources:[]};
  renderShell(incremental);
  const r=value.runtime;
  writeHTML(document.querySelector('#main'), `<div class="page-header"><div><div class="eyebrow">CONTINUOUS MISSION · STARTUP</div><h1>120 星持续实时运行</h1><p class="page-subtitle">启动过程自动刷新 · 所有完成状态来自后台实际检查点</p></div><div class="header-actions">${button('导出启动诊断','export-startup','','download')}${runtimeControls(r)}</div></div><div id="live-freshness" class="callout ${r.phase==='failed'?'amber':''}">${icon('info',16)}<span>${r.phase==='stopping'?'正在停止并清理本轮资源，当前命令结束后继续清理。':r.active?'后台正在部署，完成后自动进入实时态势。关闭网页后部署继续。':'服务当前未运行。启动后可在这里查看每个步骤及 Kubernetes 状态。'}${r.error?' '+esc(r.error):''}</span></div>${startupPanel(r)}<div class="section-gap">${button('查看历史记录','mode-history','','clock')}</div>`, incremental);
  const log = document.querySelector('#startup-log');
  if (log) log.scrollTop = followLog ? log.scrollHeight : logScroll;
  const tasks = document.querySelector('.startup-task-list');
  if (tasks) tasks.scrollTop = taskScroll;
  document.querySelectorAll('#main .table-wrap').forEach((e,i)=>e.scrollLeft=tableScroll[i] || 0);
  window.scrollTo(0,scroll);
}

function startupPanel(r, archived=false) {
  const p=r.startup, d=r.diagnostics || {};
  if (!p) return card('等待启动记录',`<p class="feature-note">${r.active?'这轮启动尚未提供细分检查点。新启动的会话会记录各子步骤。':'点击“启动 120 星”后，将显示 5G、Kubernetes、FRR 和星上实例的部署进展。'}</p>`);
  const seconds = (start,end) => start ? Math.max(0,(new Date(end || Date.now())-new Date(start))/1000) : null;
  const age = value => value ? num(seconds(value),0)+' 秒前' : '尚未采集';
  const stage=(p.stages || []).find(s=>s.id===p.current_stage);
  const task=(p.tasks || []).findLast(t=>t.stage===p.current_stage && t.state==='running');
  const completed=p.stages.filter(s=>s.state==='done').length;
  const alive=Boolean(r.active && !archived);
  const diagnosticAge=seconds(d.observed_at);
  const diagnosticFresh=alive && diagnosticAge!=null && diagnosticAge<20;
  const terminal=['failed','stopped','interrupted'].includes(r.phase);
  const activeTask=task && alive && !terminal ? task : null;
  const labels={pending:'等待',running:'执行中',done:'完成',failed:'失败',cancelled:'已中止'};
  const colors={pending:'',running:'blue',done:'green',failed:'red',cancelled:'amber'};
  const rows=p.stages.map((s,i)=>{
    const status=s.state==='running' && terminal ? 'cancelled' : s.state;
    const elapsed=seconds(s.started_at,s.finished_at || (alive?null:r.updated_at));
    return `<li class="startup-step ${esc(status)}" data-startup-stage="${esc(s.id)}"><span class="startup-step-marker">${status==='done'?icon('check',13):i+1}</span><div><strong>${esc(s.title)}</strong><small>${s.started_at?time(s.started_at)+' UTC · '+durationLabel(elapsed):'等待前序步骤完成'}</small>${s.error?`<p class="startup-error">${esc(s.error)}</p>`:''}</div>${badge(labels[status]||status,colors[status])}</li>`;
  }).join('');
  const operation=activeTask || stage || {};
  const operationElapsed=seconds(operation.started_at,operation.finished_at || (alive?null:r.updated_at));
  const longWait=alive && activeTask && operationElapsed>60;
  const timeout=activeTask?.timeout_seconds;
  const operationCard=card('当前操作',`<div class="startup-operation" id="startup-operation"><span class="eyebrow">${esc(archived?'本轮启动记录':runtimeName(r.phase))}</span><h2>${esc(r.phase==='stopping'?'等待当前操作结束并清理':operation.title || runtimeName(r.phase))}</h2><p class="mono">${esc(operation.detail || (p.events || []).at(-1)?.message || '')}</p><div class="startup-metrics"><div><span>本步骤耗时</span><strong>${operationElapsed==null?'—':durationLabel(operationElapsed)}</strong></div><div><span>此操作超时上限</span><strong>${timeout?durationLabel(timeout):'按具体检查设置'}</strong></div></div>${longWait?`<p class="callout amber">该操作已等待 ${num(operationElapsed)} 秒${timeout?'，超时上限 '+timeout+' 秒':''}。请结合下方节点、Pod 和警告信息判断；耗时较长不代表已经失败。</p>`:''}</div>`);
  const signals=card(archived?'启动期间最后观测':'后台活动与观测',`<div class="keyval"><span>运行服务进程</span><strong>${r.active?'存活':'已结束'}</strong></div><div class="keyval"><span>最近诊断采集</span><strong id="startup-observation-age">${age(d.observed_at)}</strong></div><div class="keyval"><span>最近步骤进展</span><strong>${age(p.last_progress_at)}</strong></div><div class="keyval"><span>主阶段完成数</span><strong>${completed} / ${p.stages.length}</strong></div><p class="feature-note">诊断每 5 秒采集，页面每 3 秒刷新。诊断更新表示监测在响应；步骤完成、节点或 Pod 状态变化才表示部署进展。阶段数量不代表预计耗时比例。</p><div>${archived?badge('启动观测快照'):diagnosticFresh?badge('诊断采集正常','green'):badge(alive?'诊断尚未更新 / 已过期':'后台已停止','amber')}</div>${d.error?`<p class="startup-error">${esc(d.error)}</p>`:''}`);
  const k=d.kubernetes || {}, containers=d.containers || {};
  const nodes=(k.nodes || []).map(n=>`<tr><td class="mono">${esc(n.name)}</td><td>${badge(n.ready?'Ready':'NotReady',n.ready?'green':'amber')}</td><td>${esc(n.reason)}<p class="feature-note">${esc(n.message)}</p></td></tr>`).join('');
  const pods=(k.pods || []).map(n=>`<tr><td class="mono">${esc(n.namespace)}<br>${esc(n.name)}</td><td>${badge(n.phase,n.ready===n.total&&n.total?'green':'amber')}</td><td>${n.ready}/${n.total}</td><td>${n.restarts}</td><td>${esc(n.reason || '—')}<p class="feature-note">${esc(n.node)}</p></td></tr>`).join('');
  const resources=[['Kubernetes 容器','kubernetes',3],['FRR 容器','frr',p.context?.routers??'—'],['星上容器','onboard',p.context?.satellites??'—']].map(([label,key,total])=>`<div><span>${label}</span><strong>${containers[key]?.running ?? '—'} / ${total}</strong><small>运行中 · 已创建 ${containers[key]?.created ?? '—'}</small></div>`).join('');
  const cluster=card('Kubernetes 实际状态',`<p class="mono feature-note">${esc(p.context?.cluster || '等待创建集群')} · 采集于 ${time(d.observed_at)} UTC</p><div class="startup-resource-counts">${resources}</div>${d.docker_error?`<p class="startup-error">Docker 读取失败：${esc(d.docker_error)}</p>`:''}<p class="feature-note">容器运行数量来自 Docker；节点是否可用请看 Ready，Pod 是否可用请看就绪容器数。</p>${k.api_ok?`<h3 class="startup-subtitle">节点 · ${k.nodes.filter(n=>n.ready).length}/${k.nodes.length} Ready</h3><div class="table-wrap startup-kube-table"><table><thead><tr><th>节点</th><th>状态</th><th>条件与原因</th></tr></thead><tbody id="startup-nodes">${nodes}</tbody></table></div><h3 class="startup-subtitle">Pod · ${k.pods.length} 个</h3><div class="table-wrap startup-kube-table"><table><thead><tr><th>命名空间 / Pod</th><th>阶段</th><th>就绪</th><th>重启</th><th>等待原因 / 节点</th></tr></thead><tbody id="startup-pods">${pods}</tbody></table></div>`:`<div class="callout amber">${icon('clock',15)}<span>${esc(k.error || '尚未取得 Kubernetes API 观测，等待集群创建。')}</span></div>`}`);
  const warningBody=(k.warnings || []).length?(k.warnings || []).map(e=>`<div class="startup-warning"><div>${badge(e.reason,'amber')}<span class="mono">${time(e.at)} UTC · ${e.count} 次</span></div><strong>${esc(e.object)}</strong><p>${esc(e.message)}</p></div>`).join(''):`<p class="feature-note">${k.api_ok?'本次采集未发现 Warning 事件。':'等待 Kubernetes API 就绪后采集。'} 警告可能属于启动过渡状态，请结合最新 Pod 状态判断。</p>`;
  const warnings=card('Kubernetes 警告事件',`<p class="feature-note">保留最近历史警告，请结合当前 Pod 就绪状态判断是否仍需处理。</p><div class="startup-warning-list">${warningBody}</div>`);
  const tasks=(p.tasks || []).slice(-40).map(t=>`<div class="startup-task"><div>${badge(labels[t.state],colors[t.state])}<strong>${esc(t.title)}</strong><span class="push mono">${durationLabel(seconds(t.started_at,t.finished_at || (alive?null:r.updated_at)))}</span></div><p class="mono">${esc(t.detail)}</p>${t.error?`<p class="startup-error">${esc(t.error)}</p>`:''}</div>`).join('');
  const logs=(p.events || []).map(e=>time(e.at)+' UTC  '+e.message).join('\n');
  return `<div class="startup-grid"><section class="card"><div class="card-head"><h2>${icon('check',17)}启动阶段</h2><span class="muted small">${completed} / ${p.stages.length} 完成</span></div><ol class="startup-steps">${rows}</ol></section><div class="startup-side">${operationCard}${signals}</div></div><div class="section-gap">${cluster}</div><div class="two-col section-gap">${warnings}${card('执行子步骤 · 最近 40 项',`<div class="startup-task-list">${tasks || '<p class="feature-note">等待 Kubernetes 子步骤。</p>'}</div>`)}</div><div class="section-gap">${card('启动事件日志 · UTC',`<pre id="startup-log" class="code-view startup-log" tabindex="0" aria-label="启动事件日志">${esc(logs)}</pre><p class="feature-note">只在实际进入、完成或失败时记录检查点；刷新页面不会产生虚假的进展记录。支持导出本轮诊断 JSON。</p>`)}</div>`;
}

function liveHeader() {
  const r=data.runtime;
  const stale=!r.fresh;
  return `<div class="page-header"><div><div class="eyebrow">LIVE · ${pages[state.page][1]}</div><h1>${pages[state.page][0]}</h1><p class="page-subtitle">120 星持续实时系统 · ${esc(data.run.run_id)}</p></div><div class="header-actions">${badge(runtimeName(r.phase),r.fresh?'green':'amber')}${runtimeControls(r)}${button('导出当前记录','export','','download')}</div></div><div class="callout ${stale?'amber':''}" id="live-freshness">${icon('live',16)}${stale?'保留最后一次状态，请留意当前运行阶段。':'当前数据来自持续运行服务。'} 模型时间 ${date(frame().at)} · 网络应用 ${time(r.applied_at)} UTC${r.error?' · '+esc(r.error):''}</div>`;
}

function liveOverview() {
  const tx=traffic();
  const states=Object.values(data.onboard).map(v=>v.live).filter(Boolean);
  const connected=states.filter(v=>v.connected).length;
  const primary=(plan()?.paths?.['n3-forward']?.[0]?.nodes||[]).filter(n=>n.startsWith('sat-')).slice(0,4);
  const stats=[
    stat('已部署星上实例',data.run.deployed_counts.onboard_satellites,'/ 120',`<b>${connected}</b> 颗星最近状态为已连接`,'satellite'),
    stat('当前活动链路',frame().active_pairs,'对',`${data.nodes.filter(n=>n.kind==='gateway').length} 座地面网关 · 当前模型接触`,'routing',data.frames.map(f=>f.active_pairs)),
    stat('实际路由更新',data.runtime.route_changes,'次',`已应用 ${data.runtime.sequence-100000} 次实时采样`,'live'),
    stat('观测业务送达率',tx?.transmitted?num(tx.received/tx.transmitted*100,2):'—','%',`${num(tx?.received)} / ${num(tx?.transmitted)} 个已观测报文`,'business')
  ].join('');
  const health=card('持续运行状态',`<div class="keyval"><span>会话时长</span><strong>${durationLabel(uptime())}</strong></div><div class="keyval"><span>拓扑采样目标</span><strong>${data.runtime.step_seconds} 秒</strong></div><div class="keyval"><span>最近应用耗时</span><strong>${num(data.replay.frames.at(-1)?.apply_seconds,2)} 秒</strong></div><div class="keyval"><span>错过的采样时隙</span><strong>${data.runtime.skipped_intervals}</strong></div><div class="keyval"><span>星上自治接管</span><strong>${states.filter(s=>s.fallback_active).length} 颗</strong></div><p class="feature-note">轨道为实时模型计算；业务与路由来自实际软件网络。运行状态不等同于全协议验收通过。</p>`);
  const nodes=card('当前主路径卫星',primary.map(nodeMini).join('')||'<p class="feature-note">暂无已提交路径。</p>',`<a href="#routing" class="card-link">查看路由 →</a>`);
  return `<div class="stats">${stats}</div><div class="overview-grid">${orbitCard()}<div class="side-stack">${health}${nodes}</div></div><div class="overview-bottom">${card('最近实测往返时延',chart(data.pings.map(p=>p[2]),{unit:'ms'})+'<p class="feature-note">持续 UE 探测，保留最近 1800 个有效 RTT 样本；当前尚未收到回复的报文可能影响即时送达率。</p>')}${card('实时任务事件',importantEvents().slice(-4).map(eventRow).join('')||'<p class="feature-note">等待事件。</p>')}</div>`;
}

function liveAutonomy() {
  const states=Object.values(data.onboard).map(v=>v.live).filter(Boolean);
  const active=states.filter(s=>s.fallback_active).length;
  return `<div class="stats">${stat('逐星程序',data.run.deployed_counts.onboard_satellites,'/ 120','实际部署的 Rust 实例','autonomy')}${stat('已连接',states.filter(s=>s.connected).length,'颗','星上持久状态中的 connected','live')}${stat('自治接管',active,'颗','心跳超时后由本星实际路由表选择下一跳','routing')}${stat('状态记录',states.length,'份','随实时采样读取逐星最新状态','file')}</div>${card('120 星当前自治状态',`<div class="fleet-grid">${satelliteNodes().map(n=>{const s=data.onboard[n.id]?.live;return `<button class="fleet-tile ${!s?'empty':s.fallback_active?'amber':''}" data-node="${n.id}">${icon('satellite',16)}<b>${n.id.slice(4)}</b></button>`;}).join('')}</div><p class="feature-note">琥珀色表示自治接管。备用路径从本星内核路由表查询地面出口网关的下一跳；出口不可达时撤回对应旧备用路由。点击卫星查看实际已安装路由。</p>`)}<div class="section-gap">${card('运动中的自治流程','<div class="route-path"><span class="route-node">地面心跳中断</span><span>→</span><span class="route-node">查询本星 IGP 路由</span><span>→</span><span class="route-node">动态更新备用下一跳</span><span>→</span><span class="route-node">地面恢复后撤回备用路由</span></div>')}</div>`;
}

function liveEvidence() {
  return `<div class="run-banner"><div><strong>${esc(data.run.run_id)}</strong><p>本次持续运行 · 滚动日志与当前状态 · ${data.evidence.length} 份文件</p></div>${button('查看历史验收','mode-history','','clock')}</div><div class="two-col">${card('实时事件',importantEvents().slice(-6).reverse().map(eventRow).join(''))}${card('最近网络应用耗时',chart(data.replay.frames.map(f=>f.apply_seconds),{unit:'s'})+'<p class="feature-note">有限窗口保存最近状态，记录错过的采样时隙；超过采样预算时会显示实际时间差。</p>')}</div><section class="card section-gap"><div class="card-head"><h2>本次运行文件</h2>${button('导出清单','export-evidence','','download')}</div><div class="filter-bar"><input id="evidence-search" type="search" value="${esc(state.evidenceQuery)}" placeholder="搜索当前运行文件" aria-label="搜索证据文件"><select id="evidence-type"><option value="all">全部类型</option><option value=".json">JSON</option><option value=".log">日志</option><option value=".conf">FRR 配置</option></select><span id="evidence-count" class="table-summary"></span></div><div class="table-wrap"><table><thead><tr><th>文件</th><th>类型</th><th>大小</th><th>操作</th></tr></thead><tbody id="evidence-table"></tbody></table></div><div class="pagination" id="evidence-pagination"></div></section>`;
}

function liveOperations() {
  const r=data.runtime;
  return `<div class="flex wrap" style="margin-bottom:20px">${button('查看本轮启动诊断','startup-details','','cloud')}${button('导出启动诊断','export-startup','','download')}</div><div class="two-col">${card('后台运行服务',`<div class="keyval"><span>当前阶段</span><strong>${esc(runtimeName(r.phase))}</strong></div><div class="keyval"><span>采样序号</span><strong>${r.sequence}</strong></div><div class="keyval"><span>目标步长</span><strong>${r.step_seconds} 秒</strong></div><div class="keyval"><span>会话时长</span><strong>${durationLabel(uptime())}</strong></div><div class="keyval"><span>后台进程</span><strong>${r.active?'正在运行':'已结束'}</strong></div><p class="feature-note">运行服务独立于浏览器和前端服务，轨道不在第 31 帧结束。停止后会清理本次 FRR、星上、云控及 5G 资源。</p>`)}${card('本次监控入口',Object.entries(data.noc_urls||{}).map(([name,url])=>`<div class="check-row"><span>${esc(name)}</span><a class="push card-link" href="${esc(url)}" target="_blank" rel="noopener">打开本地服务 ↗</a></div>`).join('')+'<p class="feature-note">入口来自本次实际部署；服务停止后端口将不可用。</p>')}</div><div class="section-gap">${card('最新控制器返回状态',`<pre class="code-view">${esc(JSON.stringify(data.controller_status,null,2))}</pre>`)}</div>`;
}

function header() {
  if (state.mode === 'live') return liveHeader();
  const result = data.run.success ? (data.run.focus==='physics'?'物理专项通过':'本轮验收通过') : '本轮未通过';
  const note = data.preview_only ? '本轮缺少归档物理轨道。下方使用已有生成配置预览，验收状态和部署数量以本轮报告为准。' : data.run.deployed_counts?.frr_nodes===0 ? '本轮未成功部署 FRR 网络。轨道展示归档物理模型，运行结果与部署数量以失败报告为准。' : '';
  return `<div class="page-header"><div><div class="eyebrow">${pages[state.page][1]}</div><h1>${pages[state.page][0]}</h1><p class="page-subtitle">${pages[state.page][2]} <span style="color:#c5cdd6">／</span> ${date(data.run.generated_at)}</p></div><div class="header-actions">${state.page==='overview'?badge(result,data.run.success?'green':'amber'):''}${button('刷新记录','refresh','','refresh')}${button('导出报告','export','primary','download')}</div></div>${note?'<div class="callout amber">'+icon('info',15)+note+'</div>':''}`;
}

function orbitCard(big = false) {
  return `<section class="card orbit-card ${big?'orbit-page':''}">
    <div class="card-head"><h2>${icon('orbit',16)} 120 星轨道态势 <span class="upper" style="margin-left:9px">EARTH & CONSTELLATION</span></h2>
      <div class="orbit-toolbar">
        <button class="active" data-action="toggle-orbits">${icon('orbit',11)}轨道</button>
        <button class="active" data-action="toggle-links">${icon('routing',11)}链路</button>
        <button data-action="reset-globe" aria-label="重置视角" title="重置视角与缩放">${icon('refresh',12)}</button>
        <button data-action="fullscreen-globe" aria-label="切换地球全屏" title="全屏查看地球 · Esc 退出">${icon('expand',12)}</button>
      </div>
    </div>
    <div class="orbit-hud">
      <div class="orbit-caption">
        <div class="mono">${esc(data.physics.orbit.source.toUpperCase())} · ${num(data.physics.orbit.altitude_km)} KM</div>
        <strong id="orbit-clock">${time(frame().at)} <span style="font-size:18px;color:#829bac">UTC</span></strong>
        <small>轨道历元 ${data.physics.epoch.slice(0,10)} · <span id="frame-label">${state.mode==='live'?'更新 #'+data.runtime.sequence:'帧 '+(state.frame+1)+' / '+data.frames.length}</span></small>
      </div>
      <button class="sun-readout" id="sun-readout" data-action="locate-sun" aria-label="定位太阳，恢复日地视角">
        <span class="sun-symbol" aria-hidden="true">☀</span><span><strong>太阳</strong><small>随模型时刻更新</small></span>
      </button>
    </div>
    <div class="canvas-wrap">
      <canvas id="orbit-canvas" aria-label="120 颗卫星以白色亮点展示，金色菱形为网关，金色圆环为主路；拖动旋转，滚轮或双指缩放至 6 倍；也可使用下方卫星表选择节点"></canvas>
      <div class="orbit-scale"><button data-action="zoom-in" aria-label="放大" title="放大 · 最高 6 倍">+</button><output id="globe-zoom" aria-label="当前缩放">1.0×</output><button data-action="zoom-out" aria-label="缩小" title="缩小 · 最低 0.5 倍">−</button></div>
    </div>
    <div class="orbit-footer">
      <div class="orbit-legend"><span><i class="legend-mark"></i>卫星</span><span><i class="legend-mark gateway"></i>网关</span><span><i class="legend-mark primary"></i>主路</span><span><i class="legend-line"></i>链路</span></div>
      <div class="orbit-hint">拖动旋转 · 滚轮 / 双指缩放</div>
      <div class="orbit-note"><span>TEME 轨道视图</span><span>太阳大小 / 距离示意压缩</span></div>
    </div>${playback()}</section>`;
}

function playback() {
  if (state.mode === 'live') return `<div class="playback live-timebar"><span><i class="dot"></i> 持续实时 · 1:1 时间</span><span>模型 ${time(frame().at)} UTC</span><span>网络应用 ${time(data.runtime.applied_at)} UTC</span></div>`;
  return `<div class="playback"><div class="playback-top"><button class="play-button" data-action="play" aria-label="${state.playing?'暂停回放':'播放回放'}">${icon(state.playing?'pause':'play',13)}</button><span class="time"><b id="play-time">${String(Math.floor(frame().offset/60)).padStart(2,'0')}:${String(frame().offset%60).padStart(2,'0')}</b> <span>/ 05:00</span></span><input id="frame-slider" type="range" min="0" max="${data.frames.length-1}" value="${state.frame}" aria-label="轨道回放帧"><select id="play-speed" aria-label="回放速度">${[1,10,20].map(s=>`<option value="${s}" ${state.speed===s?'selected':''}>${s}×</option>`).join('')}</select></div><div class="playback-bottom"><span>00:00 · 初始快照</span><span>01:40</span><span>03:20</span><span>05:00 · 自治前快照</span></div></div>`;
}

function overview() {
  if (state.mode === 'live') return liveOverview();
  const tx = traffic(), checks = Object.entries(data.run.checks || {});
  const passed = checks.filter(([,v]) => check(v)).length;
  const routeNodes = (plan()?.paths?.['n3-forward']?.[0]?.nodes || []).filter(id => id.startsWith('sat-'));
  const selectedNodes = (routeNodes.length ? routeNodes : satelliteNodes().map(n => n.id)).slice(0,4);
  const statistics = [
    stat('已部署星上实例', num(data.run.deployed_counts?.onboard_satellites ?? 0), '/ 120', '<b>12</b> 个轨道面 · 每面 10 星', 'satellite'),
    stat('当前活动链路', '<span id="active-links">'+frame().active_pairs+'</span>', '对', '<b>4</b> 座地面网关 · 双向链路', 'routing', data.frames.map(f=>f.active_pairs)),
    stat('物理回放改路', num(data.replay.frames?.filter(f=>f.routes_reprogrammed).length ?? 0), '次', `${data.replay.frames?.length ?? 0} 帧记录 · 全时段 ${data.physics.duration_seconds} 秒`, 'live', data.replay.frames?.map(f=>f.apply_seconds)||[]),
    stat('持续业务送达率', tx ? num(tx.received/tx.transmitted*100,2) : '—', '%', tx ? `<b>${num(tx.received)}</b> / ${num(tx.transmitted)} 个报文` : '本轮尚无持续流量记录', 'business', deliveryBuckets())
  ].join('');
  const healthBars = Array.from({length:24},(_,i)=>'<i style="'+(i/24>passed/(checks.length||1)?'background:#dbe3e9':'')+'"></i>').join('');
  const protocolStatus = data.run.full_protocol_integration ? '本轮通过' : data.run.focus==='physics' ? '本轮未执行' : '尚未通过';
  const health = `<section class="card network-health"><div class="health-heading">本轮验收概况 ${badge(data.run.success?'专项通过':'未通过',data.run.success?'green':'amber')}</div><div class="health-bar">${healthBars}</div><div class="keyval"><span>已通过检查</span><strong>${passed} / ${checks.length}</strong></div><div class="keyval"><span>物理模型</span><strong>${data.preview_only?'配置预览':esc(data.physics.orbit.source.toUpperCase())}</strong></div><div class="keyval"><span>完整协议矩阵</span><strong class="warn">${protocolStatus}</strong></div></section>`;
  const nodeCard = `<section class="card node-card"><div class="card-head"><h2>主路径卫星</h2><a class="card-link" href="#orbit">全部节点 ${icon('arrow',12)}</a></div><div class="node-list" id="path-node-list">${selectedNodes.map(nodeMini).join('')}</div><div class="card-foot"><span>点击卫星查看轨道与邻接</span><span>120 NODES</span></div></section>`;
  const stages = [['user','UE 终端'],['business','gNB 基站'],['satellite','卫星网络'],['ground','地面网关'],['cloud','UPF 核心网']].map(([ic,label])=>`<div class="stage"><span class="stage-icon">${icon(ic,18)}</span><span>${label}</span></div>`).join('');
  const trafficBody = `<div class="stage-chain">${stages}</div><div class="traffic-kpis"><div><small>持续发送</small><strong>${num(tx?.transmitted)} <span>pkts</span></strong></div><div><small>持续接收</small><strong>${num(tx?.received)} <span>pkts</span></strong></div><div><small>实际丢包率</small><strong>${num(tx?.loss_percent,3)} <span>%</span></strong></div></div>`;
  const businessCard = card('5G 端到端业务',trafficBody,`<a class="card-link" href="#business">业务详情 ${icon('arrow',12)}</a>`);
  const events = card('关键任务事件',importantEvents().slice(-3).map(eventRow).join(''),`<a class="card-link" href="#evidence">完整时间线 ${icon('arrow',12)}</a>`);
  return `<div class="stats">${statistics}</div><div class="overview-grid">${orbitCard()}<div class="side-stack">${health}${nodeCard}</div></div><div class="overview-bottom">${businessCard}${events}</div><div class="status-strip"><span>${icon('clock',12)} 运行记录 <b class="mono">${esc(data.run.run_id)}</b></span><span><i class="dot"></i> FRR / 5G / Rust / NOC 同轮证据</span><span>${data.preview_only?'生成配置预览':data.frames.length+' 帧物理记录'} · ${data.evidence.length} 份原始文件</span></div>`;
}

function deliveryBuckets() {
  const t = traffic();
  if (!t) return [];
  const counts = Array(Math.ceil(t.transmitted / 100)).fill(0);
  for (const p of data.pings) {
    const i = Math.floor((p[1] - 1) / 100);
    if (i >= 0 && i < counts.length) counts[i]++;
  }
  return counts.map((count, i) => count / Math.min(100, t.transmitted - i * 100) * 100);
}

function nodeMini(id) {
  const n = data.nodes.find(n => n.id === id);
  return `<button class="node-row" data-node="${id}"><span class="node-symbol">${icon('satellite',14)}</span><div><div class="name mono">${id.toUpperCase()}</div><div class="sub">轨道面 ${String(n?.plane||0).padStart(2,'0')} · 槽位 ${String(n?.slot||0).padStart(2,'0')}</div></div><span class="node-status">${activeNeighbors(id).length} 条邻接</span></button>`;
}

function orbitPage() {
  const options = Array.from({length:12},(_,i)=>`<option value="${i+1}" ${String(state.plane)===String(i+1)?'selected':''}>轨道面 ${i+1}</option>`).join('');
  return `<div class="stack">${orbitCard(true)}<section class="card"><div class="card-head"><div><h2>全星座节点清单</h2><p>独立轨道状态 · 单击任意节点查看完整档案</p></div>${badge(`${satelliteNodes().length} 星 + ${data.nodes.filter(n=>n.kind==='gateway').length} 网关`,'blue')}</div><div class="filter-bar"><input id="node-search" type="search" placeholder="搜索节点 ID 或 IP 地址" aria-label="搜索节点" value="${esc(state.query)}"><label>轨道面 <select id="plane-filter" aria-label="筛选轨道面"><option value="all">全部轨道面</option>${options}</select></label><select id="kind-filter" aria-label="筛选节点类型"><option value="all">全部节点</option><option value="satellite" ${state.kind==='satellite'?'selected':''}>卫星</option><option value="gateway" ${state.kind==='gateway'?'selected':''}>地面网关</option></select><span class="table-summary" id="node-count"></span></div><div class="table-wrap"><table><thead><tr><th>节点</th><th>类型 / 轨道面</th><th>Loopback 地址</th><th>当前活动邻接</th><th>轨道高度</th><th>证据</th><th></th></tr></thead><tbody id="node-table"></tbody></table></div></section></div>`;
}

function filterNodes() {
    const nodes = data.nodes.filter(n => (state.kind === 'all' || n.kind === state.kind) && (state.plane ===
        'all' || String(n.plane) === String(state.plane)) && (n.id + ' ' + n.loopback).toLowerCase()
      .includes(state.query.toLowerCase()));
    const tbody = document.querySelector('#node-table');
    if (!tbody) return;
    patchHTML(tbody, nodes.map(n => {
      const pos = frame().positions[n.id];
      return `<tr data-node="${n.id}" tabindex="0" role="button" aria-label="查看 ${n.id}"><td><span class="flex">${icon(n.kind==='satellite'?'satellite':'ground',15)}<strong class="mono">${n.id}</strong></span></td><td>${n.kind==='satellite'?`轨道面 ${n.plane} / 槽位 ${n.slot}`:'地面网关'}</td><td class="mono">${esc(n.loopback||'—')}</td><td>${activeNeighbors(n.id).length} 条</td><td class="mono">${pos?num(Math.hypot(...pos)-6378.135,1)+' km':'地面站'}</td><td>${badge(data.run.deployed_counts?.frr_nodes===data.nodes.length?'本轮已部署':'配置节点',data.run.deployed_counts?.frr_nodes===data.nodes.length?'green':'amber')}</td><td>${icon('chevron',13)}</td></tr>`;
    }).join('') || '<tr><td colspan="7">没有匹配的节点，试试 sat-0001 或清除筛选。</td></tr>');
    document.querySelector('#node-count').textContent = `显示 ${nodes.length} / ${data.nodes.length} 个节点`;
  }

  function pathCard(path, role) {
    if (!path) return empty('暂无已提交路径', '当前运行可能在初始部署阶段结束。', 'routing');
    return `<div class="flex between"><span class="badge ${role==='主路径'?'green':'amber'}">${role}</span><span class="mono muted small">${path.nodes.length-1} HOPS</span></div><div class="route-path">${path.nodes.map((id,i)=>`${i?'<span class="route-arrow">→</span>':''}<button class="route-node" data-node="${id}">${id}</button>`).join('')}</div><div class="route-meta"><span>模型时延<b>${num(path.latency_us/1000,3)} ms</b></span><span>瓶颈容量<b>${num(path.capacity_bps/1e9,2)} Gbps</b></span><span>模型丢包<b>${num(path.loss_ppm)} ppm</b></span></div>`;
  }

  function routingPage() {
    const p = plan(),
      selected = p?.paths?.[state.intent] || [];
    return `<div class="callout">${icon('info',16)}路由来自本轮实际提交记录。${state.mode==='live'?'主备路径随实时提交更新':'拖动时间轴可比较物理帧中的主备路径'}；模型链路时延与实测 RTT 分别展示。</div><section class="card"><div class="card-head"><h2>${icon('routing',16)}业务意图与提交计划</h2><select id="intent-selector" class="route-selector" aria-label="选择业务意图">${data.intents.map(i=>`<option value="${i.id}" ${state.intent===i.id?'selected':''}>${i.id} · ${i.source} → ${i.destination}</option>`).join('')}</select></div><div class="card-body"><div class="route-meta"><span>计划 ID<b class="mono">${esc(p?.id||'未提交')}</b></span><span>提交拓扑版本<b>${p?.topology_version??'—'}</b></span><span>本帧观测版本<b>${(state.mode==='live'?data.replay.frames.at(-1):data.replay.frames?.[state.frame])?.topology_version??'—'}</b></span><span>策略<b>latency / 主备</b></span></div></div>${playback()}</section><div class="two-col section-gap">${card('主路径',pathCard(selected[0],'主路径'))}${card('备份路径',pathCard(selected[1],'备用路径'))}</div><div class="two-col section-gap">${card('物理帧应用耗时',chart(data.replay.frames?.map(f=>f.apply_seconds)||[],{unit:'s'})+'<div class="chart-caption"><span>采样窗口起点</span><span>实际每帧应用时间 / 秒</span><span>最新采样</span></div>')}${card('提交与验证链',`<div class="checklist">${[['验证计划','拓扑版本、有效期与路径一致性'],['Prepare','读回实际状态，准备设备变更'],['Commit','分批提交 FRR 路由并逐批核验'],['Verify','核对实际路由和路由器 ping'],['持久化 / 回滚','成功后保存状态；失败时尝试恢复']].map(([title,sub],i)=>`<div class="check-row"><span class="step-num">0${i+1}</span><div>${title}<div class="muted small">${sub}</div></div></div>`).join('')}</div>`)}</div><section class="card section-gap"><div class="card-head"><h2>当前意图的逐设备路由</h2>${fileButton(artFile('initial-commit.json'),'查看初始提交原文 ↗')}</div><div class="table-wrap"><table><thead><tr><th>设备</th><th>目的前缀</th><th>下一跳地址</th><th>下一跳节点</th><th>Metric</th><th>角色</th></tr></thead><tbody>${(p?.routes||[]).filter(r=>r.intent_id===state.intent).map(r=>`<tr><td>${fileNode(r.device)}</td><td class="mono">${esc(r.prefix)}</td><td class="mono">${esc(r.next_hop)}</td><td>${fileNode(r.next_hop_node)}</td><td>${r.metric}</td><td>${badge(r.path_role,r.path_role==='primary'?'green':'amber')}</td></tr>`).join('')||'<tr><td colspan="6">暂无实际路由记录</td></tr>'}</tbody></table></div></section>`;
  }

  function fileNode(id) {
    return `<button class="link-button mono" data-node="${esc(id)}">${esc(id)}</button>`;
  }

const workloadCarrierNames = {ospf:'OSPFv2',ospf6:'OSPFv3',native:'逐跳 IP',ldp:'LDP', 'sr-mpls':'SR-MPLS',pcep:'PCEP / SR-MPLS',srv6:'SRv6',evpn:'EVPN / VXLAN'};
const workloadControlNames = {distributed:'分布式',centralized:'集中控制',hybrid:'混合控制'};

function workloadVerified(flow) {
  return data.runtime?.active && flow.fresh && flow.metrics?.goodput_bps > 0 && flow.evidence_fresh &&
    flow.evidence?.forward > 0 && flow.evidence?.reverse > 0 &&
    Object.values(flow.directions||{}).length === 2 && Object.values(flow.directions).every(d=>d.status==='ready');
}

function gatewayName(id) {
  const site=data.physics?.ground_stations?.find(s=>s.id===id);
  return site?.city || site?.name || id;
}

function workloadGeography(flows) {
  const sites=data.physics?.ground_stations||[];
  const ids=[...new Set(flows.flatMap(f=>[f.source,f.destination]))].sort();
  const pairs=new Map();
  for(const f of flows) {
    const key=[f.source,f.destination].sort().join('|');
    if(!pairs.has(key)) pairs.set(key,[]);
    pairs.get(key).push(f);
  }
  const regions=new Set(sites.filter(s=>ids.includes(s.id)).map(s=>s.region).filter(Boolean));
  const region=id=>sites.find(s=>s.id===id)?.region;
  const intercontinental=flows.filter(f=>region(f.source)&&region(f.destination)&&region(f.source)!==region(f.destination)).length;
  const columns=ids.map(id=>`<th title="${esc(gatewayName(id))}">${esc(id.slice(3))}</th>`).join('');
  const rows=ids.map(a=>`<tr><th>${esc(gatewayName(a))}<small>${esc(a)}</small></th>${ids.map(b=>{
    if(a===b)return '<td class="matrix-self">—</td>';
    const selected=pairs.get([a,b].sort().join('|'))||[];
    const verified=selected.filter(workloadVerified).length;
    return `<td><button data-workload-pair="${esc(a+'|'+b)}" class="matrix-cell ${verified?'verified':selected.length?'configured':''}" ${selected.length?'':'disabled'} title="${esc(gatewayName(a)+' ↔ '+gatewayName(b))}：${selected.length} 条业务，${verified} 条实包已验证">${selected.length||'·'}</button></td>`;
  }).join('')}</tr>`).join('');
  return `<section class="card section-gap"><div class="card-head"><div><h2>全球网关业务分布</h2><p>${ids.length} 个网关 · ${regions.size} 个洲 · ${pairs.size} 对网关 · ${intercontinental} 条跨洲业务</p></div></div><div class="card-body"><p>矩阵数字表示两地之间的业务流数（包含回程，不重复计数）。绿色表示已有业务通过实包验证，蓝色表示已编排。点击格子筛选两地业务。</p></div><div class="table-wrap"><table class="gateway-matrix"><thead><tr><th>城市 / 网关</th>${columns}</tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function workloadPanel() {
  const w=data.workloads;
  if(!w?.enabled) return `<div class="callout section-gap">${icon('info',16)}此会话启动时未启用并行 TCP/UDP 业务。新的业务编排配置将在下次启动时加载；当前 5G 会话继续按原配置运行。</div>`;
  const flows=w.flows||[], modes=w.summary.control_modes||{};
  const rate=w.rate_control?.scale??1;
  const ids=[...new Set(flows.flatMap(f=>[f.source,f.destination]))].sort();
  const filtered=flows.filter(f=>(state.workloadGateway==='all'||[f.source,f.destination].includes(state.workloadGateway)) &&
    (state.workloadPeer==='all'||[f.source,f.destination].includes(state.workloadPeer)) &&
    (state.workloadCarrier==='all'||f.carrier===state.workloadCarrier));
  const pages=Math.max(1,Math.ceil(filtered.length/48));
  state.workloadPage=Math.min(state.workloadPage,pages-1);
  const visible=filtered.slice(state.workloadPage*48,(state.workloadPage+1)*48);
  const filters=`<div class="filter-bar"><label>网关 <select id="workload-gateway"><option value="all">全部城市</option>${ids.map(id=>`<option value="${esc(id)}" ${state.workloadGateway===id?'selected':''}>${esc(gatewayName(id))} · ${esc(id)}</option>`).join('')}</select></label><label>承载 <select id="workload-carrier"><option value="all">全部协议</option>${Object.entries(workloadCarrierNames).map(([id,name])=>`<option value="${id}" ${state.workloadCarrier===id?'selected':''}>${name}</option>`).join('')}</select></label>${state.workloadPeer!=='all'?`<span>对端：${esc(gatewayName(state.workloadPeer))}</span>`:''}<button data-action="workload-reset" class="btn">清除筛选</button><span class="table-summary">匹配 ${filtered.length} / ${flows.length} 条</span></div>`;
  const pager=`<div class="pagination"><button class="btn" data-workload-page="${state.workloadPage-1}" ${state.workloadPage===0?'disabled':''}>上一页</button><span>${state.workloadPage+1} / ${pages} 页 · 每页最多 48 条</span><button class="btn" data-workload-page="${state.workloadPage+1}" ${state.workloadPage+1>=pages?'disabled':''}>下一页</button></div>`;
  const rows=visible.map(f=>{
    const m=f.metrics||{}, fresh=f.fresh && data.runtime?.active, ok=workloadVerified(f);
    const status=ok?'实包已验证':!fresh?'等待有效采样':m.goodput_bps>0?'待封装证据':'等待业务通路';
    const paths=Object.entries(f.directions||{}).map(([id,d])=>`<p><b>${id.endsWith('reverse')?'回程':'去程'}</b> · ${d.nodes?esc(d.nodes.join(' → ')):esc(d.route_source||'等待路由')}<br>${d.error?esc(d.error):d.nodes?'控制器计划路径':'协议路由已读回'}</p>`).join('');
    const observed=Object.keys(f.evidence?.nodes||{}).join('、');
    return `<tr><td><strong>${esc(f.name||f.label)}</strong><br><small class="mono">${esc(f.id)}</small><br><small>${esc(gatewayName(f.source))} → ${esc(gatewayName(f.destination))}</small><br><small>${esc(f.source)} → ${esc(f.destination)}</small></td><td>${esc(workloadControlNames[f.control])}<br><small>${esc(workloadCarrierNames[f.carrier])} · ${esc(f.transport.toUpperCase())}</small></td><td class="mono">${num(f.rate_bps/1000)} / ${f.rate_scale==null?'—':num(f.rate_bps*f.rate_scale/1000)}<br><small>kbit/s</small></td><td class="mono">${fresh?num((m.goodput_bps||0)/1000,1):'—'}<br><small>kbit/s</small></td><td>${fresh?num(m.rtt_p95_ms,2):'—'} ms<br><small>目标 ≤ ${num(f.max_rtt_ms)} ms</small></td><td>${fresh?num(m.loss_percent,2):'—'}%<br><small>抖动 ${fresh?num(m.jitter_ms,2):'—'} ms</small></td><td>${badge(status,ok?'green':'amber')}<details><summary>路径与证据</summary>${paths}<p>抓包卫星：${esc(observed||'尚无本轮证据')}</p><p>故障矩阵：本次持续运行未执行</p></details></td></tr>`;
  }).join('');
  return `${workloadGeography(flows)}<section class="card section-gap"><div class="card-head"><div><h2>并行多业务承载</h2><p>${flows.length} 条真实业务流 · 分布式 ${modes.distributed||0} · 集中 ${modes.centralized||0} · 混合 ${modes.hybrid||0}</p></div><label>发包比例 <select id="workload-rate" aria-label="调整业务发包比例" ${data.runtime?.active?'':'disabled'}>${[.1,.25,.5,.75,1].map(v=>`<option value="${v}" ${v===rate?'selected':''}>${v*100}%</option>`).join('')}</select></label></div><div class="card-body"><p>当前已有 ${flows.filter(workloadVerified).length} / ${flows.length} 条业务同时具备新鲜收发计数和双向封装证据。有效速率按应用回包字节计算；TCP 超时也计入业务未完成比例。</p><p class="feature-note">这些是承载测试流；现有 5G UE / PDU 会话数不变。回包会产生反向流量。在线比例在配置上限的 10%–100% 内生效，控制器继续按上限预留；DSCP 与时延目标不代表已保证 QoS。</p>${data.workload_error?`<div class="callout amber">${esc(data.workload_error)}</div>`:''}</div>${filters}<div class="table-wrap"><table id="workload-table"><thead><tr><th>业务 / 端点</th><th>控制 / 承载</th><th>上限 / 生效目标</th><th>实测有效速率</th><th>RTT P95</th><th>未完成 / 抖动</th><th>验证状态</th></tr></thead><tbody>${rows}</tbody></table></div>${pager}<div class="card-body flex">${fileButton('scenarios/constellations/live-workloads.json','业务与速率配置 ↗')}${w.evidence_file?fileButton(w.evidence_file,'本轮业务 PCAP ↗'):''}</div></section>`;
}

function liveProtocols() {
  const flows=data.workloads?.flows||[];
  const descriptions={ospf:'OSPFv2 通告 IPv4 业务端点并独立计算路径。',ospf6:'OSPFv3 通告 IPv6 业务端点，同时维护 SRv6 Locator 可达性。',native:'控制器计算主备路径并逐跳写入 IPv4 静态路由。',ldp:'控制器选择入口下一跳，后续 MPLS 转发跟随 IGP 与 LDP。','sr-mpls':'入口按控制器路径施加 Prefix SID 栈，IS-IS 分发 SID。',pcep:'BGP-LS 校验链路，PCE 响应 PCReq，FRR 安装 BSID 与 SR 标签栈。',srv6:'入口施加 IPv6 段列表，出口解封装到业务地址。',evpn:'控制器编排入口选路，BGP EVPN 学习网关 MAC/IP，VXLAN 承载；底层路径由 IGP 决定。'};
  const cards=Object.entries(workloadCarrierNames).map(([id,title])=>{
    const group=flows.filter(f=>f.carrier===id), ready=group.filter(f=>Object.values(f.directions||{}).length===2&&Object.values(f.directions).every(d=>d.status==='ready')).length, verified=group.filter(workloadVerified).length;
    return `<section class="card protocol-card"><div class="flex between"><span class="proto-icon">${icon('protocols',22)}</span><span class="mono muted small">${id.toUpperCase()}</span></div><h3>${title}</h3><p>${descriptions[id]}</p>${badge(group.length?`${verified} / ${group.length} 条实包验证`:'当前会话未编排业务',group.length&&verified===group.length?'green':'amber')}<p class="small muted">转发配置已读回 / 下发：${ready} / ${group.length}</p><div class="mini-divider"></div><span class="small muted">故障矩阵：本次持续运行未执行</span></section>`;
  }).join('');
  const bgp=data.workloads?.protocols?.bgpls, pce=data.workloads?.protocols?.pcep;
  return `<div class="callout">${icon('info',16)}协议状态、真实业务承载和故障恢复分别验证。OSPF 路由进程与 sf-controller 同时运行；以下实包状态来自当前会话，启动检查仅代表当时的收敛结果。</div><div class="protocol-grid">${cards}</div><div class="two-col section-gap">${card('控制协议观测',`<p>BGP-LS：最近读到 ${bgp?.directed_links??'—'} 条有向链路。</p><p>PCEP：${pce?.running?'PCE 已启动':'当前无工作负载 PCE'}，保留 ${pce?.responses??0} 条正向路径响应记录。</p>${bgp?.error?`<p>${esc(bgp.error)}</p>`:''}<p class="feature-note">PCE 进程存在不等于业务通路已验证；BSID、回包与抓包结果共同决定上方状态。</p>`)}${card('配置与原始证据',`${fileButton('lab/live/workloads.py','并行业务编排 ↗')} ${fileButton('lab/platform/pce.py','PCEP 实现 ↗')} ${fileButton(artFile('protocol-initial-protocol-state.json'),'启动协议状态 ↗')}<p class="feature-note">独立小拓扑回归的故障结果不会写成此 120 星会话已经通过。新增业务也不替代同一 GTP-U 会话跨协议故障验收。</p>`)}</div>${workloadPanel()}`;
}

function businessPage() {
  const tx=traffic();
  const statistics = [stat('持续发送',num(tx?.transmitted),'pkts','整段持续流量统计','business'),stat('持续接收',num(tx?.received),'pkts',state.mode==='live'?'持续 UE 业务观测':'覆盖物理回放与自治恢复','check'),stat('实际丢包率',num(tx?.loss_percent,3),'%',state.mode==='live'?'按已观测 ICMP 序号统计':'本轮门限 < 5%','chart'),stat('PDU 会话',data.pdu.initial?.pdu_establishments??'—','次建立',check(data.run.checks?.same_pdu_session_across_runtime)?'<b>前后身份一致</b>':'本轮尚无同会话证明','link')].join('');
  const flow = [['user','UE','uesimtun0'],['business','gNB','172.22.0.23'],['ground','gw-001','N3 入口'],['satellite','120 星网络','按需选择路径'],['ground','gw-002','N3 出口'],['cloud','UPF','172.22.0.8']].map(([ic,title,sub],i)=>`${i?'<div class="flow-arrow"><small>GTP-U</small></div>':''}<div class="flow-end ${i===3?'active':''}"><span class="flow-icon">${icon(ic,25)}</span><b>${title}</b><small class="mono">${sub}</small></div>`).join('');
  const flowCard = card('5G 基线会话 · N3 用户面',`<div class="flow-wide">${flow}</div><div class="callout" style="margin:0">${icon('info',15)}120 颗卫星共用一套 gNB / UE 会话。报文通过选定的卫星路径转发，并不要求每颗卫星都承载这条流。</div>`);
  const rtt = card('UE 实测往返时延',chart(data.pings.map(p=>p[2]),{unit:'ms',color:'#668fb8'})+`<div class="chart-caption"><span>${data.pings.length} 个有效 RTT 样本</span><span>ICMP 实测 / ms</span></div>`);
  const stageNames = {'cloud-controlled-initial':'初始云控','physical-final':'物理回放结束','onboard-autonomy':'星上自治','ground-recovered':'地面恢复'};
  const stages = (data.run.traffic||[]).filter(t=>t.phase!=='continuous_across_all_faults').map(t=>`<tr><td>${esc(stageNames[t.phase]||t.phase)}</td><td class="mono">${t.transmitted} / ${t.received}</td><td>${badge(t.transmitted===t.received?'通过':'存在丢包',t.transmitted===t.received?'green':'amber')}</td></tr>`).join('');
  const stageCard = card('各阶段业务探测',`<div class="table-wrap"><table><thead><tr><th>阶段</th><th>发送 / 接收</th><th>结果</th></tr></thead><tbody>${stages}</tbody></table></div>`);
  const proofs = Object.entries(data.proofs.native?.forwarding_satellites||{}).map(([id,v])=>`<tr><td>${fileNode(id)}</td><td>${num(v.uplink)}</td><td>${num(v.downlink)}</td></tr>`).join('');
  const proofCard = card('实际转发卫星与报文计数',`<div class="table-wrap"><table><thead><tr><th>卫星</th><th>抓到上行包</th><th>抓到下行包</th></tr></thead><tbody>${proofs}</tbody></table></div><p class="feature-note">计数来自每星抓包，可能含同包在不同接口的观测，不等同于 UE 唯一报文数。</p>`);
  const checks = card('业务连续性证据',checkList(['same_pdu_session_across_runtime','continuous_gtpu_business_across_faults','satellite_bidirectional_gtpu_capture','gtpu_survives_ground_route_withdrawal']));
  return `${state.mode==='live'?workloadPanel():''}<div class="stats section-gap">${statistics}</div>${flowCard}<div class="two-col section-gap">${rtt}${stageCard}</div><div class="two-col section-gap">${checks}${proofCard}</div>`;
}

function autonomyPage() {
  if (state.mode === 'live') return liveAutonomy();
  const states = Object.values(data.onboard),
    mode = state.autonomyStage;
  const active = states.filter(s => s[mode]?.fallback_active).length,
    connected = states.filter(s => s[mode]?.connected).length;
  return `<div class="stats">${stat('逐星运行时',num(data.run.deployed_counts?.onboard_satellites??0),'实例','共享对应 FRR 网络命名空间','autonomy')}${stat('当前查看阶段',mode==='autonomous'?'失联接管':'重连恢复','快照',mode==='autonomous'?'地面控制器副本缩至 0 后':'地面恢复并重新提交路由后','clock')}${stat('备用路由激活',active,'/ 120','所选快照中的 fallback_active','routing')}${stat('连接状态恢复',connected,'/ 120','所选快照中的 connected','live')}</div><section class="card"><div class="card-head"><div><h2>逐星自治状态</h2><p>每个格子对应一颗卫星 · 按本轮 Rust 状态记录展示</p></div><div class="tab-segment"><button data-stage="autonomous" class="${mode==='autonomous'?'active':''}">失联接管</button><button data-stage="final" class="${mode==='final'?'active':''}">最终恢复</button></div></div><div class="card-body"><div class="fleet-grid">${satelliteNodes().map(n=>{const s=data.onboard[n.id]?.[mode];return `<button class="fleet-tile ${!s?'empty':s.fallback_active?'amber':''}" data-node="${n.id}" aria-label="${n.id} ${s?.fallback_active?'备用路由激活':'查看状态'}">${icon('satellite',16)}<b>${n.id.slice(4)}</b></button>`;}).join('')}</div><p class="feature-note">琥珀色：备用路由激活；绿色：记录存在且备用路由已撤回；灰色：本轮缺少状态记录。上图是阶段快照，不是当前实时状态。</p></div></section><div class="two-col section-gap">${card('自治闭环验收',checkList(['onboard_runtime_on_every_configured_satellite','missing_ground_heartbeat_triggers_autonomy','onboard_installs_real_kernel_routes','gtpu_survives_ground_route_withdrawal','ground_reconnect_withdraws_fallback']))}${card('星上程序如何工作',`<div class="checklist">${[['心跳输入','管理通道发送递增 generation，拒绝过期心跳。','onboard/src/main.rs'],['失联接管','超时后安装预先计算的 N3 正反方向备用路由。','onboard/src/routing.rs'],['状态持久化','记录连接、备用路由和 generation，支持进程恢复。','onboard/src/state.rs'],['签名 A/B 更新','属于星上前置专项；本轮没有给 120 颗星逐星升级。','onboard/src/update.rs']].map(([t,s,p])=>`<div class="check-row"><span class="check-symbol">${icon('autonomy',15)}</span><div><strong>${t}</strong><p class="feature-note" style="margin:4px 0">${s}</p>${fileButton(p,p.split('/').at(-1)+' ↗')}</div></div>`).join('')}</div>`)}</div>`;
}

function protocolsPage() {
  if(state.mode==='live') return liveProtocols();
  const profiles = [
    ['OSPFv2 / OSPFv3', 'ospf', 'IPv4 底层路由与 IPv6 SID 可达性。'],
    ['LDP', 'ldp', '为传输地址分配和交换 MPLS 标签。'],
    ['SR-MPLS', 'sr-mpls', '按控制器计划施加 Prefix SID 标签栈。'],
    ['PCEP / BGP-LS', 'pcep', '实时拓扑校验与动态 SR 路径请求、响应。'],
    ['SRv6', 'srv6', '以 IPv6 段列表封装并转发原始 GTP-U。'],
    ['EVPN / VXLAN', 'evpn', '网关虚拟二层转接网络和 MAC / IP 路由交换。']
  ];
  return `<div class="callout amber">${icon('info',16)}${data.run.full_protocol_integration?'本轮报告声明完整协议集成通过，下方仍逐项显示证据。':data.run.focus==='live'?'持续运行使用 native 用户面；以下承载协议配置可查看，但本次常驻运行不自动执行故障验收矩阵。':data.run.focus==='physics'?'本轮为物理专项；完整协议故障矩阵不在本轮验收范围。各项配置和报文证据见下方。':'本轮完整协议集成尚未通过；各承载按实际检查与报文证据分别显示。'}</div><div class="protocol-grid">${profiles.map(([title,id,desc])=>{const passed=check(data.run.checks?.[id+'_same_gtpu_business_and_fault_recovery']);return `<section class="card protocol-card"><div class="flex between"><span class="proto-icon">${icon(id==='pcep'?'routing':'protocols',22)}</span><span class="mono muted small">${id.toUpperCase()}</span></div><h3>${title}</h3><p>${desc}</p>${badge(passed?'同会话故障矩阵通过':'本轮矩阵未验证',passed?'green':'amber')}<div class="mini-divider"></div><div class="flex between"><span class="small muted">初始 → 断链恢复 → 接口恢复</span><button class="icon-btn" data-protocol="${id}" aria-label="查看 ${title} 证据">${icon('arrow',15)}</button></div></section>`;}).join('')}</div><div class="two-col section-gap">${card('本轮实际协议状态',checkList(['all_configured_links_have_live_ospf_neighbors','ospfv3_routes_generated_sid_locators','evpn_all_configured_gateways','satellite_bidirectional_gtpu_capture']))}${card('协议如何连接到业务',`<div class="checklist">${[['协议配置','fabric.py','lab/platform/fabric.py'],['承载切换与故障','protocols.py','lab/platform/protocols.py'],['PCEP 请求 / 响应','pce.py','lab/platform/pce.py'],['同包封装证明','packets.py','lab/platform/packets.py']].map(([t,label,path])=>`<div class="check-row"><span class="check-symbol">${icon('file',14)}</span><span>${t}</span><span class="push">${fileButton(path,label+' ↗')}</span></div>`).join('')}</div><p class="feature-note">不同承载依次运行于同一拓扑和 PDU 会话。BGP-LS 与 PCEP 是控制协议，其证据需要连接到实际业务路径。</p>`)}</div>`;
}

function cloudPage() {
  const c = data.cloud;
  return `<div class="callout">${icon('clock',15)}${state.mode==='live'?'这里展示本次启动的部署记录与启动检查。最新控制器状态和监控入口见“实时控制器”；各检查不代表持续健康探测。':'这里展示本轮云部署及监控的归档证据。资源已清理后的历史记录不代表现在仍有服务在线。'}</div><div class="three-col">${[['Kubernetes / Helm',`${data.run.deployed_counts?.kubernetes_nodes??0} 个节点 · ${c.deployment.replicas??'—'} 个控制器副本`,'控制器使用 FRR adapter，场景通过 Helm ConfigMap 注入。','cloud/platform-controller-deployment.json'],['Lease 单写者',c.lease.holderIdentity||'暂无主实例记录',`租约周期 ${c.lease.leaseDurationSeconds??'—'} 秒 · 记录转换 ${c.lease.leaseTransitions??'—'} 次。`,'cloud/platform-lease.json'],['Cilium / Hubble','网络策略与云内流量观测','物理专项检查 CNI / Relay 就绪；策略故障矩阵由完整入口执行。','cloud/cilium-pods.json']].map(([t,v,sub,path])=>`<section class="card cloud-node"><span class="cube">${icon('cube',28)}</span><h3>${t}</h3><span class="mono small" style="overflow-wrap:anywhere">${esc(v)}</span><p>${sub}</p>${fileButton(artFile(path),'查看部署证据 ↗')}</section>`).join('')}</div><div class="two-col section-gap">${card('地面控制平面',checkList(['helm_deploys_real_frr_adapter','lease_single_ready_writer','cilium_cni_ready','hubble_relay_ready','kubernetes_controls_same_frr_constellation']))}${card('NOC 监控后端',`<div class="checklist">${[['Prometheus','拓扑指标','topology-metric.json','noc_scrapes_same_controller_topology'],['Loki','计划与故障日志','loki-correlated-logs.json','noc_correlates_plan_fault_recovery_logs'],['Tempo','实际 API 追踪','tempo-api-trace.json','noc_receives_actual_controller_trace'],['Grafana','看板与数据源','grafana-datasources.json','noc_grafana_backends_connected'],['Alert rules','地面中断告警','outage-alert.json','noc_observes_ground_outage_alert']].map(([title,desc,path,key])=>`<div class="check-row"><span class="check-symbol ${check(data.run.checks?.[key])?'':'pending'}">${icon(check(data.run.checks?.[key])?'check':'clock',15)}</span><div><strong>${title}</strong><p class="muted small">${desc}</p></div><span class="push">${fileButton(artFile('noc/'+path),'证据 ↗')}</span></div>`).join('')}</div>`)}</div><div class="section-gap">${card('同一次运行的控制与观测',`<div class="flow-wide">${[['routing','Go 控制器','计划 / 拓扑'],['live','OTel Collector','日志 / 追踪'],['cloud','Loki · Tempo','原始事件'],['chart','Prometheus','指标 / 告警'],['overview','Grafana','关联查询']].map(([ic,t,s],i)=>`${i?'<div class="flow-arrow"></div>':''}<div class="flow-end"><div class="flow-icon">${icon(ic,23)}</div><strong>${t}</strong><small>${s}</small></div>`).join('')}</div><p class="feature-note">共享 hostPath 属于单机软件在环环境。Argo CD、HA 滚动升级等未完成项可在完整功能图谱中查看。</p>`)}</div>`;
}
const remaining = ['多厂商网络验证', 'Release 17 NTN 接入', 'O-RAN 控制管理', '云平台交付与 GitOps', '星载系统镜像构建',
  'SONiC / SAI 可编程交换', 'AF_XDP / DPDK 高速数据面', '长时规模与稳定性验收'
];

function capabilitiesPage() {
  return `<div class="stats">${stat('技术覆盖条目',data.capabilities.length,'项','来自 coverage-manifest.json','capabilities')}${stat('技术标签',new Set(data.capabilities.flatMap(c=>c.technologies)).size,'种','协议 / 云平台 / 星载 / 验证','protocols')}${stat('阶段覆盖','0–6','阶段','全工程技术路线','routing')}${stat('仍需完成系统','8','组','按 remaining-systems.md 声明','flag')}</div><section class="card"><div class="card-head"><div><h2>完整技术与功能目录</h2><p>本页包含主线和独立专项。门禁报告的历史通过状态不会被合并成一次统一运行通过。</p></div>${fileButton('docs/coverage-manifest.json','原始清单 ↗')}</div><div class="filter-bar"><input id="cap-search" type="search" placeholder="搜索技术，例如 BFD、P4、NTN" value="${esc(state.capQuery)}" aria-label="搜索全部功能"><span class="table-summary" id="cap-count"></span></div><div class="card-body capability-list" id="cap-list"></div></section><section class="card section-gap"><div class="card-head"><h2>仍需完成的 8 组系统</h2>${fileButton('docs/remaining-systems.md','查看具体范围 ↗')}</div><div class="card-body"><div class="capability-list">${remaining.map((name,i)=>`<div class="capability"><div class="flex between"><h3>0${i+1} / ${name}</h3>${badge('待完成','amber')}</div></div>`).join('')}</div></div></section>`;
}

function filterCapabilities() {
  const entries = data.capabilities.filter(c => (c.id + ' ' + c.technologies.join(' ')).toLowerCase()
    .includes(state.capQuery.toLowerCase()));
  const list = document.querySelector('#cap-list');
  if (!list) return;
  patchHTML(list, entries.map(c =>
    `<article class="capability"><div class="flex between"><h3>${esc(c.technologies.slice(0,2).join(' / '))}</h3>${badge('PHASE '+c.phase,'blue')}</div><p class="mono">${esc(c.id)} · 定义级别 ${esc(c.level)} · 清单声明 ${esc(c.declared_status)}</p><div class="chips">${c.technologies.map(t=>`<span class="chip">${esc(t)}</span>`).join('')}</div>${c.gates.map(g=>`<div class="gate">${badge(g.passed?'独立报告通过':g.present?'门禁未满足':'缺少报告',g.passed?'green':'amber')}${fileButton(g.path,g.path.split('/').at(-1))}</div>`).join('')||'<p>无独立门禁报告；查看实现与硬件边界。</p>'}<div class="gate">${(c.evidence||[]).slice(0,2).map(p=>fileButton(p,p.split('/').at(-1)+' ↗')).join(' · ')}</div>${c.hardware_excluded.length?`<p>硬件验收边界：${esc(c.hardware_excluded.join('、'))}</p>`:''}<button class="card-link link-button" style="margin-top:12px" data-capability="${esc(c.id)}">完整条目与边界 ${icon('arrow',11)}</button></article>`
    ).join('') || empty('没有匹配技术', '尝试其他关键词'));
  document.querySelector('#cap-count').textContent = `${entries.length} / ${data.capabilities.length} 项`;
}

function importantEvents() {
  return (data.run.timeline || []).filter(e => !['physical_frame_applied', 'business_packets_verified',
    'fleet_packets_verified'
  ].includes(e.event));
}

function eventRow(e) {
  const title = eventNames[e.event] || runtimePhases[e.event] || (/^physical-\d+$/.test(e.event) ? '物理帧路由重新提交' : e.event);
  return `<div class="event-row"><div class="event-dot">${icon(e.event.includes('stopped')?'clock':e.event.includes('onboard')?'autonomy':'check',12)}</div><div class="event-copy"><strong>${esc(title)}</strong><p>${esc(e.plan_id?'计划 '+e.plan_id:e.satellites?e.satellites+' 颗卫星':e.event)}</p></div><span class="event-time">${time(e.at)}</span></div>`;
}

function evidencePage() {
  if (state.mode === 'live') return liveEvidence();
  return `<div class="run-banner"><div><strong>${icon('folder',15)} ${esc(data.run.run_id)}</strong><p>${date(data.run.generated_at)} · ${data.run.focus==='physics'?'物理专项':'完整入口'} · ${data.run.physical_model?'逐星物理模型':'逻辑模型'} · ${data.evidence.length} 份归档文件</p></div><div class="flex">${badge(data.run.success?'本轮通过':'本轮未通过',data.run.success?'green':'amber')}<select class="run-select" id="run-selector" aria-label="选择历史运行">${data.runs.map(r=>`<option value="${r.run_id}" ${r.run_id===data.run.run_id?'selected':''}>${r.run_id} · ${r.physical?'物理':'逻辑'} · ${r.success?'通过':'未通过'}</option>`).join('')}</select></div></div><div class="two-col">${card('验收结果与边界',`<div class="keyval"><span>物理专项集成</span><strong>${data.run.physical_runtime_integration===true?'通过':'未通过 / 未声明'}</strong></div><div class="keyval"><span>完整协议集成</span><strong>${data.run.full_protocol_integration?'通过':'未通过 / 未执行'}</strong></div><div class="keyval"><span>FRR 实际部署</span><strong>${data.run.deployed_counts?.frr_nodes??0} 节点</strong></div><div class="keyval"><span>逐星进程</span><strong>${data.run.deployed_counts?.onboard_satellites??0} 实例</strong></div><div class="keyval"><span>本轮运行期间源码一致</span><strong>${data.run.source_unchanged===true?'是':'未确认'}</strong></div><div class="keyval"><span>平台资源清理</span><strong>${check(data.run.checks?.owned_resources_cleaned)?'完成':'未确认'}</strong></div><p class="feature-note">本轮源码一致是报告中的记录，不等同于此刻重新计算全部源文件哈希。外层 5G 清理需另读其日志。</p>`)}${card('关键事件',`<div style="max-height:212px;overflow:auto">${importantEvents().slice().reverse().map(eventRow).join('')}</div>`,button('所有事件','events','','clock'))}</div><section class="card section-gap"><div class="card-head"><div><h2>原始运行证据</h2><p>JSON、每星配置、抓包和日志均来自所选运行目录</p></div>${button('导出文件清单','export-evidence','','download')}</div><div class="filter-bar"><input type="search" id="evidence-search" value="${esc(state.evidenceQuery)}" placeholder="搜索文件，如 physical-2 或 sat-0001" aria-label="搜索证据文件"><select id="evidence-type" aria-label="证据文件类型">${[['all','全部类型'],['.json','JSON'],['.pcap','PCAP 抓包'],['.log','日志'],['.conf','FRR 配置']].map(([v,t])=>`<option value="${v}" ${state.evidenceType===v?'selected':''}>${t}</option>`).join('')}</select><span class="table-summary" id="evidence-count"></span></div><div class="table-wrap"><table><thead><tr><th>文件</th><th>类型</th><th>大小</th><th>操作</th></tr></thead><tbody id="evidence-table"></tbody></table></div><div class="pagination" id="evidence-pagination"></div></section>`;
}

function paginate(items, current, size) {
  const pages = Math.max(1, Math.ceil(items.length / size)),
    page = Math.min(current, pages - 1);
  return {
    rows: items.slice(page * size, (page + 1) * size),
    page,
    pages,
    total: items.length
  };
}

function paginationHTML(kind, p) {
  return `<span>共 ${p.total} 项 · 每页 ${kind==='evidence'?20:15} 项</span><div class="pagination-controls"><button data-pager="${kind}" data-direction="-1" ${p.page===0?'disabled':''}>上一页</button><span>${p.page+1} / ${p.pages}</span><button data-pager="${kind}" data-direction="1" ${p.page+1>=p.pages?'disabled':''}>下一页</button></div>`;
}

function filterEvidence() {
  const items = data.evidence.filter(e => e.name.toLowerCase().includes(state.evidenceQuery.toLowerCase()) &&
    (state.evidenceType === 'all' || e.name.endsWith(state.evidenceType)));
  const p = paginate(items, state.evidencePage, 20);
  state.evidencePage = p.page;
  patchHTML(document.querySelector('#evidence-table'), p.rows.map(e =>
    `<tr><td><span class="file-icon">${icon(e.name.endsWith('.pcap')?'live':'file',14)}</span>${fileButton(e.path,e.name)}</td><td class="muted mono">${esc(e.name.split('.').at(-1).toUpperCase())}</td><td class="muted mono">${bytes(e.bytes)}</td><td><a class="download-link" href="${downloadURL(e.path)}">${icon('download',13)}下载</a></td></tr>`
    ).join('') || '<tr><td colspan="4">没有匹配的证据文件。</td></tr>');
  document.querySelector('#evidence-count').textContent = `匹配 ${items.length} 份文件`;
  patchHTML(document.querySelector('#evidence-pagination'), paginationHTML('evidence', p));
}

function learningPage() {
  const steps = [
    ['输入和物理模型', '两份 JSON → 逐星轨道 → 接触帧', 'tools/physical_constellation.py'],
    ['绑定和实际网络', '节点身份 → IP / 接口 → FRR', 'lab/platform/fabric.py'],
    ['云控与路由提交', '意图 → 主备路径 → 逐节点路由', 'internal/planner/planner.go'],
    ['回放与星上自治', '逐帧改图 → 失联接管 → 地面恢复', 'lab/platform/physics_runtime.py']
  ];
  return `<div class="two-col">${card('从主线开始学习',`<div class="stack" style="gap:10px">${steps.map(([t,s,p],i)=>`<button class="learning-step" data-file="${p}"><span class="step-num">0${i+1}</span><div><strong>${t}</strong><p>${s}</p></div><span class="push">${icon('arrow',13)}</span></button>`).join('')}</div>`)}${card('完整学习资料',`<div class="checklist">${[['120 星学习手册','从配置到清理的完整流程与真实例子','docs/120-satellite-learning-guide.md'],['逐文件归属表','主线、前置验证、可选分支与独立实验','docs/120-satellite-file-index.md'],['运行与改规模','物理专项、完整协议入口和配置修改','docs/120-satellite-quickstart.md'],['仍需完成的系统','8 组未完成能力与准确验收边界','docs/remaining-systems.md']].map(([t,s,p])=>`<button class="node-row" style="padding:12px 0" data-file="${p}"><span class="node-symbol">${icon('learning',17)}</span><div><strong style="font-size:22px">${t}</strong><p class="feature-note" style="margin-top:3px">${s}</p></div><span class="push">${icon('external',13)}</span></button>`).join('')}</div>`)}</div><section class="card section-gap"><div class="card-head"><div><h2>全工程文件导航</h2><p>以学习索引为基准；被导入、被构建、被哈希记录与被业务执行分别说明</p></div>${badge(data.sources.length+' 个条目','blue')}</div><div class="filter-bar"><input id="source-search" type="search" value="${esc(state.sourceQuery)}" placeholder="搜索文件路径或作用，例如 自治" aria-label="搜索工程文件"><select id="source-category" aria-label="文件流程归属"><option value="all">全部流程归属</option>${[...new Set(data.sources.map(s=>s.category))].map(c=>`<option value="${esc(c)}" ${state.sourceCategory===c?'selected':''}>${esc(c)}</option>`).join('')}</select><span class="table-summary" id="source-count"></span></div><div class="table-wrap"><table><thead><tr><th>文件</th><th>流程归属</th><th>作用</th></tr></thead><tbody id="source-table"></tbody></table></div><div class="pagination" id="source-pagination"></div></section>`;
}

function filterSources() {
  const items = data.sources.filter(s => (state.sourceCategory === 'all' || s.category === state
    .sourceCategory) && (s.path + ' ' + s.role).toLowerCase().includes(state.sourceQuery.toLowerCase()));
  const p = paginate(items, state.sourcePage, 15);
  state.sourcePage = p.page;
  patchHTML(document.querySelector('#source-table'), p.rows.map(s =>
    `<tr><td class="file-path">${fileButton(s.path)}</td><td><span class="badge ${/^[AB]\./.test(s.category)?'green':'blue'}">${esc(s.category.split(' ')[0])}</span></td><td class="file-role">${esc(s.role)}</td></tr>`
    ).join('') || '<tr><td colspan="3">没有匹配的工程文件。</td></tr>');
  document.querySelector('#source-count').textContent = `${items.length} 个匹配文件`;
  patchHTML(document.querySelector('#source-pagination'), paginationHTML('source', p));
}
const configFields = [
  ['constellation', 'satellites', '卫星数量', 4, 10000, 1, '独立卫星身份与软件节点'],
  ['constellation', 'planes', '轨道面数', 0, 10000, 1, '0 为自动；否则须整除卫星数'],
  ['constellation', 'gateways', '地面网关', 2, 1000, 1, '每个 ID 必须具有对应物理坐标'],
  ['constellation', 'flows', '网关业务意图', 0, 10000, 1, '0 为自动；不是持续 UE 数量'],
  ['constellation', 'demand_bps', '每条业务需求 / bit/s', 1, 1e12, 1000000, '控制器容量约束输入'],
  ['physical', 'duration_seconds', '回放时长 / 秒', 1, 86400, 1, '配置时间，不代表已重新执行'],
  ['physical', 'step_seconds', '采样间隔 / 秒', .1, 3600, .1, '控制物理采样精度'],
  ['orbit', 'altitude_km', '轨道高度参数 / km', 100, 36000, 10, '当前 Walker 设计轨道参数'],
  ['orbit', 'inclination_deg', '轨道倾角 / °', 0, 180, .1, '当前 Walker 设计轨道参数'],
  ['links', 'minimum_elevation_deg', '地面最小仰角 / °', 0, 90, 1, '星地可见性门限'],
  ['links', 'isl_terminals_per_satellite', '每星 ISL 终端', 1, 20, 1, '限制可同时选择的星间连接'],
  ['links', 'packet_loss_ppm', '配置丢包 / ppm', 0, 1000000, 1, '独立配置参数，不从 RF 误码推导']
];

function configValue(group, key) {
  const c = state.config;
  return group === 'orbit' ? c.physical.orbit[key] : group === 'links' ? c.physical.links[key] : c[group][
  key];
}

function configurationPage() {
  return `<div class="callout">${icon('configuration',16)}当前编辑的是输入副本。参数修改后可下载两份 JSON；需要重新运行物理生成器和验收，才能得到新的轨道、链路和实测结果。</div><section class="card"><div class="card-head"><div><h2>星座与物理参数</h2><p>源输入：leo-120.config.json + physical-defaults.json</p></div>${button('恢复原始配置','reset-config','','refresh')}</div><div class="card-body"><form id="config-form"><div class="form-grid">${configFields.map(([g,k,label,min,max,step,hint])=>`<div class="field"><label for="cfg-${g}-${k}">${label}</label><input id="cfg-${g}-${k}" data-config-group="${g}" data-config-key="${k}" type="number" min="${min}" max="${max}" step="${step}" value="${configValue(g,k)}" required><small>${hint}</small></div>`).join('')}</div><div class="field-error" id="config-error" role="alert"></div><div class="config-actions"><span class="small muted" id="config-summary"></span><div class="flex">${button('下载规模 JSON','download-constellation','','download')}${button('下载物理 JSON','download-physical','primary','download')}</div></div></form></div></section><div class="two-col section-gap">${card('完整规模配置',`<textarea id="constellation-editor" class="code-editor" spellcheck="false" aria-label="完整规模 JSON">${esc(JSON.stringify(state.config.constellation,null,2))}</textarea><div class="config-actions"><span class="small muted">高级编辑包含全部字段</span>${button('应用 JSON','apply-constellation')}</div>`)}${card('完整物理配置',`<textarea id="physical-editor" class="code-editor" spellcheck="false" aria-label="完整物理 JSON">${esc(JSON.stringify(state.config.physical,null,2))}</textarea><div class="config-actions"><span class="small muted">包括站点坐标、链路预算和轨道来源</span>${button('应用 JSON','apply-physical')}</div>`)}</div><div class="section-gap">${card('输入与输出的关系',`<div class="route-path"><span class="route-node">两份输入 JSON</span><span class="route-arrow">→</span><span class="route-node">seed.json</span><span class="route-arrow">→</span><span class="route-node">物理 scenario.json</span><span class="route-arrow">→</span><span class="route-node">部署 / 实验 / 报告</span></div><p class="feature-note">生成的 reports/.../scenario.json 不应作为长期维护输入。改变星数也可能改变覆盖、容量与主备路径可行性。</p>`)}</div>`;
}

function configErrors(c) {
  const errors = [];
  if (!c.constellation || !c.physical?.orbit || !c.physical?.links || !Array.isArray(c.physical
      .ground_stations)) return ['配置结构不完整：需要 constellation、orbit、links、ground_stations。'];
  for (const [g, k, label, min, max, step] of configFields) {
    const v = g === 'orbit' ? c.physical.orbit[k] : g === 'links' ? c.physical.links[k] : c[g][k];
    if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max || (step === 1 && !Number
        .isInteger(v))) errors.push(`${label} 必须是 ${min}–${max} 范围内的${step===1?'整数':'数字'}。`);
  }
  const s = c.constellation;
  if (s.planes && s.satellites % s.planes !== 0) errors.push('轨道面数必须整除卫星总数。');
  if (s.gateway_uplinks < 3 || s.gateway_uplinks > s.satellites) errors.push(
    '逻辑种子的 gateway_uplinks 必须在 3 与星数之间。');
  if (s.demand_bps > s.capacity_bps) errors.push('需求不能超过逻辑种子的 capacity_bps。');
  const ids = new Set(c.physical.ground_stations.map(g => g.id));
  for (let i = 1; i <= Math.min(s.gateways, 1000); i++) {
    const id = 'gw-' + String(i).padStart(3, '0');
    if (!ids.has(id)) {
      errors.push(`缺少 ${id} 的地面坐标。`);
      break;
    }
  }
  if (c.physical.step_seconds > c.physical.duration_seconds) errors.push('采样间隔不应大于回放总时长。');
  return errors;
}

function updateConfigValidation() {
  const errors = configErrors(state.config),
    el = document.querySelector('#config-error');
  if (!el) return;
  el.textContent = errors.join(' ');
  const c = state.config,
    s = c.constellation;
  document.querySelector('#config-summary').textContent = errors.length ? '请先修正参数，再导出配置。' :
    `参数预览：${s.satellites} 星 + ${s.gateways} 网关 · ${Math.ceil(c.physical.duration_seconds/c.physical.step_seconds)+1} 帧（尚未编译）`;
  for (const action of ['download-constellation', 'download-physical']) document.querySelector(
    `[data-action="${action}"]`).disabled = !!errors.length;
}

function livePage() {
  if (state.mode === 'live') return liveOperations();
  return `<section class="card"><div class="card-head"><h2>${icon('live',17)}实时数据接入</h2>${button('读取控制器','poll-live','primary','refresh')}</div><div id="live-content"><div class="live-blank"><div class="live-orbit">${icon('live',37)}</div><h2>${data.controller_configured?'正在读取控制器…':'准备连接实时控制器'}</h2><p>历史回放已连接本地证据。实时页面可额外读取正在运行的<br>控制器就绪状态、拓扑、业务意图与已提交计划。</p>${data.controller_configured?'':'<code>python3 frontend/server.py --controller http://127.0.0.1:8080</code><p style="margin-top:14px;font-size:20px">将地址替换为实际控制器地址；认证可通过 --controller-token-file 指定。</p>'}</div></div></section><div class="two-col section-gap">${card('实时数据范围',`<div class="checklist">${[['/readyz','主实例是否就绪'],['/api/v1/topology','最新拓扑版本和节点／链路'],['/api/v1/intents','当前业务意图'],['/api/v1/status','当前提交状态和 committed_plan']].map(([p,s])=>`<div class="check-row"><span class="check-symbol">${icon('live',13)}</span><span class="mono">${p}</span><span class="push muted">${s}</span></div>`).join('')}</div>`)}${card('实时与回放分别标记',`<p style="font-size:24px;color:#8195a8;line-height:2">实时接口只读取已配置的控制器。其他页面继续使用所选 run_id 的历史物理模型和证据，避免将历史轨道误标成实时轨道。</p><p class="feature-note">控制器连接后每 10 秒自动更新。没有控制器地址或连接失败时，会保留明确的离线状态。</p>`)}</div>`;
}
async function pollLive() {
  const el = document.querySelector('#live-content');
  if (!el) return;
  try {
    const response = await fetch('/api/live');
    const value = await response.json();
    if (state.page !== 'live') return;
    if (!value.configured) {
      toast('尚未配置实时控制器，请按页面说明启动接入。');
      return;
    }
    if (!value.connected) {
      el.innerHTML = empty('控制器暂不可达', '连接超时、认证失败或主实例未就绪。历史回放仍可使用。', 'live');
      return;
    }
    const t = value.results.topology.data,
      s = value.results.status.data,
      intents = value.results.intents.data;
    const raw = (title, value) => `<details class="detail-section"><summary>${title}</summary><pre class="code-view">${esc(JSON.stringify(value,null,2))}</pre></details>`;
    el.innerHTML =
      `<div class="card-body"><div class="callout">${icon('live',15)}实时读取成功 · ${date(value.observed_at)}</div><div class="stats">${stat('拓扑版本',t.version,'','控制器实时返回','routing')}${stat('节点',t.nodes?.length,'','实时拓扑节点','satellite')}${stat('链路',t.links?.length,'方向','实时拓扑方向链路','link')}${stat('提交阶段',esc(s.reconcile?.phase||'—'),'','状态 API 返回','live')}</div><h3>当前提交与执行状态</h3><pre class="code-view" style="margin-top:12px">${esc(JSON.stringify(s,null,2))}</pre>${raw('当前业务意图',intents)}${raw('完整实时拓扑',t)}</div>`;
  } catch {
    if (state.page === 'live') el.innerHTML = empty('读取失败', '本地展示服务暂时不可用，请稍后重试。', 'live');
  }
}

function renderPage(incremental=false) {
  if (!data) return;
  if (data.pending) { renderPending(data,incremental); return; }
  const view = globe ? {yaw:globe.yaw,pitch:globe.pitch,zoom:globe.zoom,links:globe.links,orbits:globe.orbits} : null;
  if (!incremental) {
    globe?.destroy();
    globe = null;
    clearInterval(liveTimer);
  }
  renderShell(incremental);
  const renderers = {
    overview,
    orbit: orbitPage,
    routing: routingPage,
    business: businessPage,
    autonomy: autonomyPage,
    protocols: protocolsPage,
    cloud: cloudPage,
    capabilities: capabilitiesPage,
    evidence: evidencePage,
    learning: learningPage,
    configuration: configurationPage,
    live: livePage
  };
  writeHTML(document.querySelector('#main'),
    `<div class="page-enter">${header()}${renderers[state.page]()}</div>`,incremental,filteredRegions);
  const canvas = document.querySelector('#orbit-canvas');
  if (canvas) {
    if (globe?.canvas === canvas) {
      const changed=globe.data.frames[globe.frame]?.at!==frame().at || globe.selected!==state.node;
      globe.data=data;
      globe.frame=state.frame;
      if (changed) globe.update(state.frame,state.node);
    } else {
      globe?.destroy();
      globe = new Globe(canvas, data, openNode);
      if (view) Object.assign(globe,view);
      globe.update(state.frame, state.node);
    }
    for (const layer of ['orbits','links']) {
      document.querySelector(`[data-action="toggle-${layer}"]`)?.classList.toggle('active',globe[layer]);
    }
  }
  if (state.page === 'orbit') filterNodes();
  if (state.page === 'evidence') filterEvidence();
  if (state.page === 'learning') filterSources();
  if (state.page === 'capabilities') filterCapabilities();
  if (state.page === 'configuration') updateConfigValidation();
  if (!incremental && state.mode !== 'live' && state.page === 'live' && data.controller_configured) {
    pollLive();
    liveTimer = setInterval(pollLive, 10000);
  }
}

function changeFrame(index) {
  state.frame = Math.max(0, Math.min(data.frames.length - 1, index));
  globe?.update(state.frame, state.node);
  const clock = document.querySelector('#orbit-clock');
  if (clock) clock.innerHTML = time(frame().at) + ' <span style="font-size:18px;color:#829bac">UTC</span>';
  const label = document.querySelector('#frame-label');
  if (label) label.textContent = `${state.mode==='live'?'更新 #'+data.runtime.sequence:'帧 '+(state.frame+1)+' / '+data.frames.length}`;
  const slider = document.querySelector('#frame-slider');
  if (slider) slider.value = state.frame;
  const pt = document.querySelector('#play-time');
  if (pt) pt.textContent = String(Math.floor(frame().offset / 60)).padStart(2, '0') + ':' + String(frame()
    .offset % 60).padStart(2, '0');
  const al = document.querySelector('#active-links');
  if (al) al.textContent = frame().active_pairs;
  if (state.page === 'orbit') filterNodes();
  if (state.page === 'routing') renderPage();
  if (state.page === 'overview') {
    const ids = plan()?.paths?.['n3-forward']?.[0]?.nodes.filter(n => n.startsWith('sat-')).slice(0, 4) || [];
    const list = document.querySelector('#path-node-list');
    if (list) list.innerHTML = ids.map(nodeMini).join('');
  }
  if (state.node && document.querySelector('#node-details')) renderNodeBody(state.node);
}

function stopPlay() {
  clearInterval(timer);
  timer = null;
  state.playing = false;
  const btn = document.querySelector('[data-action="play"]');
  if (btn) {
    btn.innerHTML = icon('play', 13);
    btn.setAttribute('aria-label', '播放回放');
  }
}

function startPlay() {
  if (state.frame >= data.frames.length - 1) changeFrame(0);
  clearInterval(timer);
  state.playing = true;
  const btn = document.querySelector('[data-action="play"]');
  if (btn) {
    btn.innerHTML = icon('pause', 13);
    btn.setAttribute('aria-label', '暂停回放');
  }
  timer = setInterval(() => {
    if (state.frame >= data.frames.length - 1) {
      stopPlay();
      return;
    }
    changeFrame(state.frame + 1);
  }, data.physics.step_seconds * 1000 / state.speed);
}

function openDrawer(title, subtitle, body, wide = false, footer = '') {
  fileRequest++;
  lastFocus = document.activeElement;
  document.querySelector('#drawer-root').innerHTML =
    `<div class="drawer-scrim" data-action="close-drawer"></div><section class="drawer ${wide?'wide':''}" role="dialog" aria-modal="true" aria-labelledby="drawer-title"><div class="drawer-head"><div><h2 id="drawer-title">${esc(title)}</h2><p>${esc(subtitle)}</p></div><button class="icon-btn" data-action="close-drawer" aria-label="关闭详情">${icon('close',21)}</button></div><div class="drawer-body">${body}</div>${footer?`<div class="drawer-footer">${footer}</div>`:''}</section>`;
  document.body.style.overflow = 'hidden';
  document.querySelector('.drawer [data-action="close-drawer"]').focus();
}

function closeDrawer() {
  fileRequest++;
  document.querySelector('#drawer-root').innerHTML = '';
  document.body.style.overflow = '';
  lastFocus?.isConnected && lastFocus.focus();
}

function openNode(id) {
  if (!data.nodes.some(n => n.id === id)) {
    toast('当前场景没有这个节点。');
    return;
  }
  state.node = id;
  globe?.update(state.frame, id);
  openDrawer(id.toUpperCase(), (state.mode==='live'?'实时节点 · ':'节点档案 · ') + data.run.run_id, `<div id="node-details"></div>`, false,
    `${button('定位到星座','locate-node','','orbit')}${button('查看 FRR 配置','node-frr','primary','file')}`);
  renderNodeBody(id);
}

function renderNodeBody(id) {
  const node = data.nodes.find(n => n.id === id),
    f = frame(),
    pos = f.positions[id] || f.ground[id],
    neighbors = activeNeighbors(id),
    ob = data.onboard[id],
    station = data.physics.ground_stations.find(g => g.id === id);
  const lat = pos ? Math.asin(pos[2] / Math.hypot(...pos)) * 180 / Math.PI : 0;
  let lon = pos ? (Math.atan2(pos[1], pos[0]) - f.gmst) * 180 / Math.PI : 0;
  lon = ((lon + 540) % 360) - 180;
  const body = document.querySelector('#node-details');
  if (!body) return;
  patchHTML(body, `<div class="flex between">${badge(node.kind==='satellite'?'卫星节点':'地面网关','blue')}${badge(`${neighbors.length} 条活动邻接`,'green')}</div><div class="detail-grid"><div class="detail-cell"><small>Loopback</small><strong class="mono">${esc(node.loopback||'—')}</strong></div><div class="detail-cell"><small>${node.kind==='satellite'?'轨道面 / 槽位':'设计站点'}</small><strong>${node.kind==='satellite'?node.plane+' / '+node.slot:esc(station?.name?.replace(' design site','')||id)}</strong></div><div class="detail-cell"><small>地心经纬度近似</small><strong class="mono">${num(lat,2)}° / ${num(lon,2)}°</strong></div><div class="detail-cell"><small>距参考球面高度</small><strong>${pos?num(Math.hypot(...pos)-6378.135,1):'—'} km</strong></div></div><p class="feature-note">轨道时间 ${date(f.at)} · 地心经纬度由 TEME 位置转换，非精密大地坐标。</p><div class="detail-section"><h3>当前活动邻接</h3>${neighbors.map(peer=>{const link=f.links.find(l=>l.source===id&&l.target===peer);return `<div class="neighbor">${fileNode(peer)}<span class="mono muted">${num(link?.latency_us/1000,2)} ms · ${num(link?.capacity_bps/1e9,2)} Gbps</span></div>`;}).join('')||'<p class="muted small">该帧没有活动邻接。</p>'}</div>${ob?`<div class="detail-section"><h3>自治阶段记录</h3><div class="keyval"><span>失联阶段备用路由</span><strong>${ob.autonomous?ob.autonomous.fallback_active?'已激活':'未激活':'无记录'}</strong></div><div class="keyval"><span>${state.mode==='live'?'当前':'最终'} connected</span><strong>${ob.final?.connected??'无记录'}</strong></div><div class="keyval"><span>${state.mode==='live'?'当前':'最终'} generation</span><strong>${ob.final?.generation??'—'}</strong></div><div class="keyval"><span>${state.mode==='live'?'当前':'最终'} fallback_active</span><strong>${ob.final?.fallback_active??'无记录'}</strong></div></div><div class="detail-section"><h3>${state.mode==='live'?'实际已安装的自治路由':'预配置备用路由'}</h3><pre class="code-view">${esc(JSON.stringify(ob.routes,null,2))}</pre></div>`:''}<div class="detail-section"><h3>${node.kind==='satellite'?'轨道输入':'地面站参数'}</h3><pre class="code-view">${esc(JSON.stringify(node.kind==='satellite'?node.orbit:station,null,2))}</pre></div>`);
}
let fileRequest = 0;
async function openFile(path) {
  if (path.endsWith('.pcap') || path.endsWith('.pcapng')) {
    openDrawer(path.split('/').at(-1), '原始抓包文件',
      `<div class="empty-state">${icon('live',35)}<h3>原始 PCAP 抓包</h3><p>下载后可用 Wireshark 查看每个报文。<br>页面中的报文统计来自 protocol-packet-proofs.json。</p><p style="margin-top:20px"><a class="btn primary" href="${downloadURL(path)}">${icon('download',13)} 下载 PCAP</a></p></div>`
      );
    return;
  }
  openDrawer(path.split('/').at(-1), path, '<div class="empty-state">正在读取原始文件…</div>', true,
    `<a class="btn primary" href="${downloadURL(path)}">${icon('download',13)}下载文件</a>`);
  const id = fileRequest;
  try {
    const r = await fetch('/api/file?path=' + encodeURIComponent(path));
    let text = await r.text();
    if (!r.ok) throw new Error(JSON.parse(text).error || '文件读取失败');
    if (path.endsWith('.json')) {
      try {
        text = JSON.stringify(JSON.parse(text), null, 2);
      } catch {}
    }
    if (id !== fileRequest || !document.querySelector('.drawer')) return;
    document.querySelector('.drawer-body').innerHTML = `<pre class="code-view">${esc(text)}</pre>`;
  } catch (e) {
    if (id === fileRequest && document.querySelector('.drawer-body')) document.querySelector('.drawer-body')
      .innerHTML = empty('无法读取文件', esc(e.message));
  }
}

function showEvents() {
  openDrawer('完整运行时间线', `${data.run.timeline?.length??0} 个事件 · 时间采用 UTC`,
    `<div class="timeline-list">${(data.run.timeline||[]).map(e=>eventRow(e)+`<details style="margin-left:34px;margin-bottom:9px"><summary class="small muted" style="cursor:pointer">事件数据</summary><pre class="code-view" style="margin-top:8px">${esc(JSON.stringify(e,null,2))}</pre></details>`).join('')}</div>`,
    true);
}

function globalSearch(query) {
  const q = query.trim().toLowerCase();
  if (!q) {
    document.querySelector('#global-search').focus();
    return;
  }
  const nodes = data.nodes.filter(n => (n.id + ' ' + n.loopback).toLowerCase().includes(q));
  const features = Object.entries(pages).filter(([, p]) => p.join(' ').toLowerCase().includes(q));
  const files = data.sources.filter(s => (s.path + ' ' + s.role).toLowerCase().includes(q)).slice(0, 30);
  openDrawer('搜索结果', `“${query}” · 节点、功能与工程文件`,
    `<div class="stack">${nodes.length?card('节点',nodes.slice(0,20).map(n=>`<div class="neighbor">${fileNode(n.id)}<span class="muted mono">${n.loopback||''}</span></div>`).join('')):''}${features.length?card('功能页面',features.map(([id,p])=>`<a class="learning-step" href="#${id}" data-dismiss>${icon(id,18)}${p[0]}<span class="push">${icon('arrow',12)}</span></a>`).join('')):''}${files.length?card('工程文件',files.map(s=>`<div class="check-row"><div>${fileButton(s.path)}<p class="feature-note">${esc(s.role)}</p></div></div>`).join('')):''}${!nodes.length&&!features.length&&!files.length?empty('没有匹配结果','试试 sat-0001、路由、Rust 或物理。'):''}</div>`,
    true);
}

async function load(identifier) {
  stopPlay();
  const generation = ++loadGeneration;
  const mode = state.mode;
  const query = mode === 'live' ? '?mode=live' : identifier ? '?run='+encodeURIComponent(identifier) : '';
  const response = await fetch('/api/dashboard'+query);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '读取运行记录失败');
  if (generation !== loadGeneration) return;
  if (value.pending) { renderPending(value); return; }
  data = value;
  state.run = data.run.run_id;
  state.frame = mode === 'live' ? data.frames.length-1 : 0;
  state.config = structuredClone(data.configuration);
  state.node = null;
  state.evidencePage = 0;
  renderPage();
}

async function refreshContinuous() {
  if (state.mode !== 'live' || liveBusy) return;
  liveBusy = true;
  const generation = loadGeneration;
  try {
    const response = await fetch('/api/dashboard?mode=live');
    if (!response.ok) throw new Error('实时服务读取失败');
    const value = await response.json();
    if (state.mode !== 'live' || generation !== loadGeneration) return;
    if (value.pending) { renderPending(value,Boolean(data?.pending)); return; }
    const newRun=!data || data.pending || data.run.run_id !== value.run.run_id;
    if (newRun) {
      data = value;
      state.config = structuredClone(value.configuration);
    } else data = value;
    state.run = value.run.run_id;
    state.frame = value.frames.length-1;
    const scroll = scrollY;
    // Keep advanced configuration text intact while the operator edits it.
    if (state.page !== 'configuration' || newRun) renderPage(!newRun);
    if (state.node && document.querySelector('#node-details')) renderNodeBody(state.node);
    window.scrollTo(0,scroll);
  } catch(error) {
    const banner = document.querySelector('#live-freshness');
    if (banner) { banner.classList.add('amber'); banner.textContent='实时数据连接中断，保留最后一次状态；正在重连。'; }
  } finally { liveBusy=false; }
}

document.addEventListener('click', async e => {
  const el = e.target.closest('button,a,tr[data-node],[data-action]');
  if (!el) return;
  if (el.dataset.workloadPair) {
    [state.workloadGateway,state.workloadPeer]=el.dataset.workloadPair.split('|');
    state.workloadPage=0;
    renderPage();
    document.querySelector('#workload-table')?.scrollIntoView({block:'start'});
    return;
  }
  if (el.dataset.workloadPage !== undefined) {
    state.workloadPage=Math.max(0,Number(el.dataset.workloadPage));
    renderPage();
    return;
  }
  if (el.dataset.action==='workload-reset') {
    state.workloadGateway=state.workloadPeer=state.workloadCarrier='all';
    state.workloadPage=0;
    renderPage();
    return;
  }
  if (el.dataset.dismiss !== undefined) closeDrawer();
  if (el.dataset.node) {
    openNode(el.dataset.node);
    return;
  }
  if (el.dataset.file) {
    openFile(el.dataset.file);
    return;
  }
  if (el.dataset.stage) {
    state.autonomyStage = el.dataset.stage;
    renderPage();
    return;
  }
  if (el.dataset.pager) {
    const key = el.dataset.pager === 'evidence' ? 'evidencePage' : 'sourcePage';
    state[key] += Number(el.dataset.direction);
    el.dataset.pager === 'evidence' ? filterEvidence() : filterSources();
    return;
  }
  if (el.dataset.protocol) {
    const profile = el.dataset.protocol;
    openDrawer(profile.toUpperCase() + ' · 业务与故障证据', '仅列出当前 run_id 的协议记录',
      `<div class="callout ${data.proofs[profile]?'':'amber'}">${data.proofs[profile]?'本轮具有该承载的报文证明。':'本轮未执行该承载完整故障矩阵。native 抓包不能替代本项通过证据。'}</div>${data.proofs[profile]?`<pre class="code-view">${esc(JSON.stringify(data.proofs[profile],null,2))}</pre>`:''}<div class="detail-section">${fileButton('lab/platform/protocols.py','查看承载切换实现 ↗')}</div>`
      );
    return;
  }
  if (el.dataset.capability) {
    const c = data.capabilities.find(x => x.id === el.dataset.capability);
    openDrawer(c.id, '功能清单、实现证据与验收边界',
      `<div class="chips">${c.technologies.map(t=>`<span class="chip">${esc(t)}</span>`).join('')}</div><p style="line-height:1.9;margin:20px 0;color:#718a9f">${esc(c.boundary||'以具体运行报告为准。')}</p><pre class="code-view">${esc(JSON.stringify(c,null,2))}</pre>`,
      true);
    return;
  }
  const action = el.dataset.action;
  if (!action) return;
  if (action === 'mode-live' || action === 'mode-history') {
    state.mode = action === 'mode-live' ? 'live' : 'history';
    localStorage.setItem('sf-mode',state.mode);
    closeDrawer();
    try { await load(); } catch(error) { toast(error.message); }
    return;
  }
  if (action === 'runtime-start' || action === 'runtime-stop') {
    el.disabled = true;
    try {
      const response = await fetch('/api/runtime/'+action.slice(8), {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const value = await response.json();
      if (!response.ok) throw new Error(value.error);
      await load();
      toast(action === 'runtime-start' ? '后台正在启动真实 120 星系统。' : '正在停止并清理本次运行资源。');
    } catch(error) { toast(error.message); el.disabled=false; }
    return;
  }
  if (action === 'menu') {
    document.querySelector('#sidebar').classList.toggle('open');
    return;
  }
  if (action === 'close-drawer') {
    closeDrawer();
    return;
  }
  if (action === 'play') {
    state.playing ? stopPlay() : startPlay();
    return;
  }
  if (action === 'reset-globe' || action === 'locate-sun') {
    globe?.reset();
    return;
  }
  if (action === 'fullscreen-globe') {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (document.querySelector('.orbit-card')?.requestFullscreen) await document.querySelector('.orbit-card').requestFullscreen();
      else location.hash = 'orbit';
    } catch (_) { toast('浏览器暂不支持全屏，可在「星座与轨道」中展开查看。'); }
    return;
  }
  if (action === 'toggle-links' || action === 'toggle-orbits') {
    if (globe) {
      const key = action === 'toggle-links' ? 'links' : 'orbits';
      globe[key] = !globe[key];
      el.classList.toggle('active', globe[key]);
      globe.draw();
    }
    return;
  }
  if (action === 'zoom-in' || action === 'zoom-out') {
    if (globe) {
      globe.setZoom(globe.zoom * (action === 'zoom-in' ? 1.25 : .8));
    }
    return;
  }
  if (action === 'help') {
    openFile('docs/120-satellite-learning-guide.md');
    return;
  }
  if (action === 'events') {
    showEvents();
    return;
  }
  if (action === 'export') {
    saveJSON(data.run, data.run.run_id + '-report.json');
    return;
  }
  if (action === 'export-startup') {
    saveJSON({exported_at:new Date().toISOString(),runtime:data.runtime},(data.runtime?.run_id || 'startup')+'-startup-diagnostics.json');
    return;
  }
  if (action === 'startup-details') {
    openDrawer('本轮启动诊断','启动完成后的记录；节点与 Pod 为启动期间最后观测',startupPanel(data.runtime,true),true);
    return;
  }
  if (action === 'export-evidence') {
    saveJSON({
      run_id: data.run.run_id,
      files: data.evidence
    }, data.run.run_id + '-files.json');
    return;
  }
  if (action === 'refresh') {
    el.disabled = true;
    try {
      await load(state.run);
      toast('已重新读取本轮报告。');
    } catch (error) {
      toast(error.message);
    } finally {
      el.disabled = false;
    }
    return;
  }
  if (action === 'node-frr') {
    openFile(artFile('routers/' + state.node + '/frr.conf'));
    return;
  }
  if (action === 'locate-node') {
    closeDrawer();
    if (state.page !== 'orbit') location.hash = 'orbit';
    else globe?.update(state.frame, state.node);
    return;
  }
  if (action === 'poll-live') {
    el.disabled = true;
    await pollLive();
    el.disabled = false;
    return;
  }
  if (action === 'reset-config') {
    state.config = structuredClone(data.configuration);
    renderPage();
    toast('已恢复当前输入配置。');
    return;
  }
  if (action.startsWith('download-')) {
    const kind = action.replace('download-', ''),
      errors = configErrors(state.config);
    if (errors.length) {
      toast(errors[0]);
      return;
    }
    saveJSON(state.config[kind], kind === 'physical' ? 'physical-custom.json' :
      `leo-${state.config.constellation.satellites}.config.json`);
    return;
  }
  if (action.startsWith('apply-')) {
    const kind = action.replace('apply-', '');
    try {
      const next = JSON.parse(document.querySelector('#' + kind + '-editor').value),
        candidate = structuredClone(state.config);
      candidate[kind] = next;
      const errors = configErrors(candidate);
      if (errors.length) throw new Error(errors[0]);
      state.config = candidate;
      renderPage();
      toast('JSON 已应用到配置副本。');
    } catch (error) {
      document.querySelector('#config-error').textContent = error.message;
      toast('JSON 未应用：' + error.message);
    }
    return;
  }
});
document.addEventListener('input', e => {
  const t = e.target;
  if (t.id === 'frame-slider') {
    changeFrame(Number(t.value));
    return;
  }
  const maps = {
    'node-search': ['query', filterNodes],
    'evidence-search': ['evidenceQuery', filterEvidence],
    'source-search': ['sourceQuery', filterSources],
    'cap-search': ['capQuery', filterCapabilities]
  };
  if (maps[t.id]) {
    const [key, fn] = maps[t.id];
    state[key] = t.value;
    if (key === 'evidenceQuery') state.evidencePage = 0;
    if (key === 'sourceQuery') state.sourcePage = 0;
    fn();
    return;
  }
  if (t.dataset.configGroup) {
    const g = t.dataset.configGroup,
      k = t.dataset.configKey;
    const obj = g === 'orbit' ? state.config.physical.orbit : g === 'links' ? state.config.physical
      .links : state.config[g];
    obj[k] = t.value === '' ? null : Number(t.value);
    document.querySelector('#constellation-editor').value = JSON.stringify(state.config.constellation,
      null, 2);
    document.querySelector('#physical-editor').value = JSON.stringify(state.config.physical, null, 2);
    updateConfigValidation();
  }
});
document.addEventListener('change', async e => {
  const t = e.target;
  if(t.id==='workload-gateway'||t.id==='workload-carrier') {
    state[t.id==='workload-gateway'?'workloadGateway':'workloadCarrier']=t.value;
    state.workloadPeer='all';
    state.workloadPage=0;
    renderPage();
    return;
  }
  if (t.id === 'workload-rate') {
    try {
      t.disabled=true;
      const response=await fetch('/api/runtime/workload-rate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:data.run.run_id,scale:Number(t.value)})});
      const result=await response.json();
      if(!response.ok) throw new Error(result.error||'调速失败');
      data.workloads.rate_control.scale=result.scale;
      toast('调速请求已保存；业务进程将在下一采样周期应用。');
    } catch(error) { toast(error.message); }
    finally { t.disabled=false; }
  }
  const maps = {
    'plane-filter': ['plane', filterNodes],
    'kind-filter': ['kind', filterNodes],
    'evidence-type': ['evidenceType', filterEvidence],
    'source-category': ['sourceCategory', filterSources]
  };
  if (maps[t.id]) {
    const [key, fn] = maps[t.id];
    state[key] = t.value;
    state.sourcePage = state.evidencePage = 0;
    fn();
  }
  if (t.id === 'intent-selector') {
    state.intent = t.value;
    renderPage();
  }
  if (t.id === 'play-speed') {
    state.speed = Number(t.value);
    if (state.playing) startPlay();
  }
  if (t.id === 'run-selector') {
    try {
      t.disabled = true;
      await load(t.value);
      toast('已切换到 ' + data.run.run_id);
    } catch (error) {
      toast(error.message);
      t.disabled = false;
    }
  }
});
document.addEventListener('submit', e => {
  e.preventDefault();
  if (e.target.id === 'global-search-form') globalSearch(document.querySelector('#global-search').value);
});
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    document.querySelector('#global-search')?.focus();
  }
  if (e.key === 'Escape') {
    closeDrawer();
    document.querySelector('#sidebar').classList.remove('open');
  }
  if (e.key === 'Enter' && e.target.matches('tr[data-node]')) openNode(e.target.dataset.node);
  if (e.key === 'Tab' && document.querySelector('.drawer')) {
    const focusable = [...document.querySelectorAll(
        '.drawer button,.drawer a,.drawer input,.drawer select,.drawer textarea,.drawer summary')].filter(
        el => !el.disabled),
      first = focusable[0],
      last = focusable.at(-1);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  }
});
window.addEventListener('hashchange', () => {
  const id = location.hash.slice(1);
  state.page = pages[id] ? id : 'overview';
  stopPlay();
  closeDrawer();
  renderPage();
  window.scrollTo(0, 0);
});
window.addEventListener('pagehide', () => {
  stopPlay();
  clearInterval(liveTimer);
  clearInterval(continuousTimer);
  globe?.destroy();
});
state.page = pages[location.hash.slice(1)] ? location.hash.slice(1) : 'overview';
try {
  await load();
} catch (error) {
  document.querySelector('#main').innerHTML =
    `<div class="loading-screen">${icon('info',36)}<h2>暂时无法读取星座记录</h2><p>${esc(error.message)}</p><a class="btn" href="/">重新读取</a></div>`;
}

continuousTimer = setInterval(refreshContinuous,3000);
