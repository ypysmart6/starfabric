// Draft selection is kept outside the polling dashboard DOM.
let catalog, draft;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function launchOptions() {
  if (catalog) return catalog;
  const response = await fetch('/api/runtime/options');
  if (!response.ok) throw new Error('无法读取启动选项');
  catalog = await response.json();
  try {
    const saved = JSON.parse(localStorage.getItem('sf-launch-draft'));
    draft = saved?.schema_version === 1 ? {...catalog.defaults, ...saved} : structuredClone(catalog.defaults);
  } catch { draft = structuredClone(catalog.defaults); }
  return catalog;
}

export function launchForm(runtime = {}) {
  if (!catalog) return '<p>正在读取启动选项…</p>';
  const active = runtime.active;
  const c = active && runtime.launch_config ? runtime.launch_config : draft;
  const select = (key, values) => `<select name="${key}">${Object.entries(values).map(([id,label]) => `<option value="${esc(id)}" ${c[key]===id?'selected':''}>${esc(label)}</option>`).join('')}</select>`;
  const numbers = [['satellites','卫星数量',4,253],['gateways','网关数量',2,24],['planes','轨道面数',1,253],['copies_per_profile','每类业务的双向对数',1,64],['step_seconds','拓扑采样间隔（秒）',5,60]];
  return `<section class="card launch-card"><div class="card-head"><div><h2>${active?'本轮启动配置':'启动前配置'}</h2><p>选择协议、路径算法和星座规模，再启动实时平台</p></div></div><div class="card-body"><form id="launch-form"><fieldset ${active?'disabled':''}>
    <div class="field"><label>星座规模方案</label><select name="preset"><option value="">自定义 / 当前选择</option>${catalog.presets.map((p,i)=>`<option value="${i}">${esc(p.label)}</option>`).join('')}</select></div>
    <div class="form-grid"><div class="field"><label>底层路由协议（双栈）</label>${select('igp',{isis:'IS-IS · IPv4 + IPv6',ospf:'OSPFv2 + OSPFv3'})}</div><div class="field"><label>集中式业务路径算法</label>${select('algorithm',catalog.algorithms)}</div>${numbers.map(([key,label,min,max])=>`<div class="field"><label>${label}</label><input name="${key}" type="number" min="${min}" max="${max}" step="1" required value="${esc(c[key])}"></div>`).join('')}</div>
    <div class="field"><label>业务承载（可多选，分配到各业务类别）</label><div class="launch-carriers">${Object.entries(catalog.carriers).map(([id,label])=>`<label><input type="checkbox" name="carrier" value="${id}" ${c.carriers?.includes(id)?'checked':''}>${esc(label)}</label>`).join('')}</div></div>
    <p class="feature-note">8 类持续业务 × 每类对数 = 双向业务总对数。选择 EVPN + SRv6 时，也启用网关二层互通和 5G N3 承载；UE / gNB 由 5G 编排管理。</p>
    <div id="launch-error" class="field-error" role="alert"></div>
    <div class="config-actions"><span>${active?'停止本轮后才能更换配置。':'启动后将按这份配置创建真实节点、路由协议和业务。'}</span><button type="button" class="btn primary" data-action="launch-submit">按所选配置启动</button></div></fieldset></form>
    <ul class="feature-note">${catalog.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div></section>`;
}

export function captureLaunch(form) {
  const fd = new FormData(form);
  draft = {schema_version:1, igp:fd.get('igp'), algorithm:fd.get('algorithm'), carriers:fd.getAll('carrier')};
  for (const name of ['satellites','gateways','planes','copies_per_profile','step_seconds']) draft[name] = Number(fd.get(name));
  localStorage.setItem('sf-launch-draft', JSON.stringify(draft));
  return structuredClone(draft);
}

document.addEventListener('change', event => {
  const form = event.target.closest('#launch-form');
  if (!form || form.querySelector('fieldset').disabled) return;
  if (event.target.name === 'preset' && event.target.value !== '') {
    const p = catalog.presets[Number(event.target.value)];
    for (const key of ['satellites','gateways','planes']) form.elements[key].value = p[key];
  }
  const c = captureLaunch(form);
  const conflict = c.igp === 'ospf' && c.carriers.some(v => ['sr-mpls','pcep'].includes(v));
  form.querySelector('#launch-error').textContent = conflict ? 'SR-MPLS / PCEP 需要 IS-IS；请调整底层协议或取消这两项承载。' : '';
});

export async function submitLaunch(form) {
  if (!form.reportValidity()) throw new Error('请检查配置字段');
  const configuration = captureLaunch(form);
  const body = JSON.stringify({configuration});
  for (const action of ['validate', 'start']) {
    const response = await fetch('/api/runtime/'+action, {method:'POST',headers:{'Content-Type':'application/json'},body});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || '启动请求失败');
  }
}
