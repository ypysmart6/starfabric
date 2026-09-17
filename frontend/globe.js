import {EarthSurface} from './earth-surface.js';
import {solarPosition} from './solar.js';

const TAU = Math.PI * 2;
const EARTH = 6378;
export const MIN_ZOOM = .5;
export const MAX_ZOOM = 6;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

export class Globe {
  constructor(canvas, data, onSelect) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.data = data;
    this.onSelect = onSelect;
    this.frame = 0;
    this.zoom = 1;
    this.selected = null;
    this.hovered = null;
    this.links = true;
    this.orbits = true;
    this.hit = [];
    this.pointers = new Map();
    this.moved = false;
    this.setInitialView();
    this.surface = new EarthSurface(() => this.requestDraw());
    this.observer = new ResizeObserver(() => this.requestDraw());
    this.observer.observe(canvas);
    this.down = e => {
      if (e.button !== 0 && e.pointerType === 'mouse') return;
      this.pointers.set(e.pointerId, [e.clientX, e.clientY]);
      this.drag = [e.clientX, e.clientY];
      this.moved = this.pointers.size > 1;
      this.pinch = this.pinchDistance();
      canvas.setPointerCapture(e.pointerId);
      canvas.style.cursor = 'grabbing';
    };
    this.move = e => {
      if (this.pointers.has(e.pointerId)) {
        const previous = this.pointers.get(e.pointerId);
        this.pointers.set(e.pointerId, [e.clientX, e.clientY]);
        if (this.pointers.size > 1) {
          const distance = this.pinchDistance();
          if (this.pinch > 0) this.zoom = clamp(this.zoom * distance / this.pinch, MIN_ZOOM, MAX_ZOOM);
          this.pinch = distance;
          this.moved = true;
        } else {
          const dx = e.clientX - previous[0], dy = e.clientY - previous[1];
          if (Math.hypot(e.clientX - this.drag[0], e.clientY - this.drag[1]) > 3) this.moved = true;
          // Close-up views turn more gently, keeping the geography under control.
          this.yaw += dx * .006 / Math.sqrt(this.zoom);
          this.pitch = clamp(this.pitch + dy * .006 / Math.sqrt(this.zoom), -1.5, 1.5);
        }
        this.requestDraw();
      } else {
        const point = this.pick(e);
        if (this.hovered !== (point?.id || null)) {
          this.hovered = point?.id || null;
          this.requestDraw();
        }
        canvas.style.cursor = point ? 'pointer' : 'grab';
        canvas.title = point ? `${point.id} · 点击查看节点` : '拖动旋转 · 滚轮或双指缩放 · 最高 6 倍';
      }
    };
    this.up = e => {
      if (!this.pointers.has(e.pointerId)) return;
      if (e.type === 'pointerup' && this.pointers.size === 1 && !this.moved) {
        const point = this.pick(e);
        if (point) this.onSelect(point.id);
      }
      this.pointers.delete(e.pointerId);
      this.pinch = this.pinchDistance();
      this.drag = this.pointers.values().next().value || null;
      if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
      canvas.style.cursor = 'grab';
    };
    this.wheel = e => {
      e.preventDefault();
      const pixels = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? canvas.clientHeight : 1);
      this.setZoom(this.zoom * Math.exp(-clamp(pixels, -500, 500) * .0015));
    };
    this.leave = () => {
      if (this.hovered) { this.hovered = null; this.requestDraw(); }
    };
    this.events = [['pointerdown', this.down], ['pointermove', this.move], ['pointerup', this.up],
      ['pointercancel', this.up], ['lostpointercapture', this.up], ['pointerleave', this.leave], ['wheel', this.wheel]];
    for (const [type, fn] of this.events) canvas.addEventListener(type, fn, {passive: false});
    this.draw();
  }

  pinchDistance() {
    const points = [...this.pointers.values()];
    return points.length > 1 ? Math.hypot(points[0][0] - points[1][0], points[0][1] - points[1][1]) : 0;
  }
  setInitialView() {
    const sun = solarPosition(this.data.frames[this.frame]?.at);
    this.yaw = sun ? sun.rightAscension - .9 : -.55;
    this.pitch = .28;
  }
  setZoom(value) {
    this.zoom = clamp(value, MIN_ZOOM, MAX_ZOOM);
    this.requestDraw();
  }
  requestDraw() {
    if (this.destroyed || this.animation) return;
    this.animation = requestAnimationFrame(() => {
      this.animation = null;
      this.draw();
    });
  }
  pick(e) {
    const rect = this.canvas.getBoundingClientRect();
    return [...this.hit].reverse().find(p => Math.hypot(p.x - (e.clientX - rect.left), p.y - (e.clientY - rect.top)) < 10);
  }
  destroy() {
    this.destroyed = true;
    this.observer.disconnect();
    cancelAnimationFrame(this.animation);
    this.surface.destroy();
    for (const [type, fn] of this.events) this.canvas.removeEventListener(type, fn);
  }
  update(frame, selected) {
    this.frame = frame;
    this.selected = selected;
    this.draw();
  }
  reset() {
    this.setInitialView();
    this.zoom = 1;
    this.draw();
  }
  project(v) {
    const [x, y, z] = v, a = this.yaw, b = this.pitch;
    const xx = x * Math.cos(a) + y * Math.sin(a), zz = -x * Math.sin(a) + y * Math.cos(a);
    return {x: this.cx + xx * this.scale,
      y: this.cy - (z * Math.cos(b) - zz * Math.sin(b)) * this.scale,
      z: z * Math.sin(b) + zz * Math.cos(b)};
  }
  visible(point) {
    const radial2 = (point.x - this.cx) ** 2 + (point.y - this.cy) ** 2;
    return radial2 >= this.earth ** 2 || point.z * this.scale >= Math.sqrt(this.earth ** 2 - radial2) - 1;
  }
  path(points, color, width = 1, behind = false) {
    const c = this.ctx;
    c.strokeStyle = color;
    c.lineWidth = width;
    c.beginPath();
    let drawing = false;
    for (const point of points) {
      if (!behind && !this.visible(point)) { drawing = false; continue; }
      if (drawing) c.lineTo(point.x, point.y);
      else c.moveTo(point.x, point.y);
      drawing = true;
    }
    c.stroke();
  }
  link(a, b, color, width) {
    // Sample the segment to clip links at the actual globe surface, including
    // far-side satellites that remain visible outside the Earth's silhouette.
    this.path(Array.from({length: 25}, (_, i) => {
      const t = i / 24;
      return {x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t};
    }), color, width);
  }
  drawSun(sun, w, h) {
    const c = this.ctx;
    if (!sun) return {status: '模型时刻缺失', position: null};
    // Compress only display distance/size. Lighting uses the unscaled direction.
    const projected = this.project(sun.direction.map(v => v * EARTH * 2.6));
    const overlap = Math.hypot(projected.x - this.cx, projected.y - this.cy) < this.earth + 24;
    const hidden = projected.z < 0 && overlap;
    const towardViewer = projected.z >= 0 && overlap;
    const offscreen = projected.x < 38 || projected.x > w - 38 || projected.y < 42 || projected.y > h - 55;
    let x = projected.x, y = projected.y;
    if (hidden || towardViewer || offscreen) {
      // A border marker indicates the direction; never draw the solar disk
      // through an occluding Earth, even when fully zoomed in.
      let dx = x - this.cx, dy = y - this.cy;
      if (Math.hypot(dx, dy) < 1) { dx = 1; dy = 0; }
      const tx = dx > 0 ? (w - 42 - this.cx) / dx : (32 - this.cx) / dx;
      const ty = dy > 0 ? (h - 55 - this.cy) / dy : (42 - this.cy) / dy;
      const t = Math.min(tx, ty);
      x = this.cx + dx * t; y = this.cy + dy * t;
      const angle = Math.atan2(dy, dx);
      c.save(); c.translate(x, y); c.rotate(angle);
      c.strokeStyle = '#e9b779'; c.lineWidth = 1.3;
      c.beginPath(); c.moveTo(-5, -5); c.lineTo(1, 0); c.lineTo(-5, 5); c.stroke();
      c.restore();
      c.fillStyle = '#f3c787'; c.font = '20px system-ui,sans-serif';
      c.textAlign = x > this.cx ? 'right' : 'left';
      c.fillText(hidden ? '太阳 · 地球背后' : towardViewer ? '太阳 · 视线前方' : '太阳方向', x + (x > this.cx ? -13 : 13), y + 4);
      c.textAlign = 'left';
      return {status: hidden ? '地球遮挡 · 点击定位' : towardViewer ? '视线前方 · 点击定位' : '画外方向 · 点击定位', position: {x,y,hidden,offscreen:true}};
    }
    const size = clamp(15 * Math.sqrt(this.zoom), 11, 25);
    const glow = c.createRadialGradient(x, y, size * .4, x, y, size * 4.5);
    glow.addColorStop(0, 'rgba(255,214,144,.42)');
    glow.addColorStop(.28, 'rgba(255,168,63,.17)');
    glow.addColorStop(.65, 'rgba(242,124,40,.04)');
    glow.addColorStop(1, 'rgba(255,146,46,0)');
    c.fillStyle = glow;
    c.beginPath(); c.arc(x, y, size * 4.5, 0, TAU); c.fill();
    const disk = c.createRadialGradient(x - size * .2, y - size * .2, 0, x, y, size);
    disk.addColorStop(0, '#fffef0'); disk.addColorStop(.72, '#ffe5a2'); disk.addColorStop(1, '#f6aa51');
    c.fillStyle = disk;
    c.beginPath(); c.arc(x, y, size, 0, TAU); c.fill();
    c.textAlign = 'center'; c.fillStyle = '#eccc96'; c.font = '20px system-ui,sans-serif';
    c.fillText('太阳', x, y + size + 27); c.textAlign = 'left';
    return {status: '随模型时刻更新 · 点击定位', position: {x,y,hidden:false,offscreen:false}};
  }
  syncHUD(sun, sunView) {
    const wrap = this.canvas.closest('.orbit-card') || this.canvas.parentElement;
    const zoom = wrap.querySelector('#globe-zoom');
    if (zoom) zoom.textContent = `${this.zoom.toFixed(1)}×`;
    const readout = wrap.querySelector('#sun-readout');
    if (readout) {
      readout.querySelector('strong').textContent = sun ? `太阳 · ${sun.distanceAU.toFixed(3)} AU` : '太阳位置不可用';
      readout.querySelector('small').textContent = sunView.status;
      readout.title = sun ? '按当前模型时刻近似计算太阳方向，1 AU 约为 1.496 亿公里。点击恢复日地视角。' : '模型时间不可用';
    }
    this.canvas.dataset.zoom = this.zoom.toFixed(3);
    this.canvas.dataset.surface = this.surface.ready ? 'textured' : 'loading';
    this.canvas.dataset.renderer = this.surface.gl ? 'webgl' : 'canvas';
    this.canvas.dataset.sunAt = this.data.frames[this.frame].at;
    this.sunView = sunView;
  }
  draw() {
    if (this.destroyed) return;
    const c = this.ctx, box = this.canvas.getBoundingClientRect();
    const frame = this.data.frames[this.frame];
    if (!box.width || !box.height || !frame) return;
    const dpr = Math.min(devicePixelRatio || 1, 2), w = box.width, h = box.height;
    if (this.canvas.width !== Math.round(w * dpr)) this.canvas.width = Math.round(w * dpr);
    if (this.canvas.height !== Math.round(h * dpr)) this.canvas.height = Math.round(h * dpr);
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.textAlign = 'left'; c.shadowBlur = 0;
    this.cx = w * .46; this.cy = h * .53;
    this.scale = Math.min(w * .36, h * .50) * this.zoom / 9000;
    this.earth = EARTH * this.scale;
    const earth = this.earth, sun = solarPosition(frame.at);
    const bg = c.createRadialGradient(w * .4, h * .45, 0, w * .5, h * .5, Math.max(w, h) * .75);
    bg.addColorStop(0, '#0d1c31'); bg.addColorStop(.5, '#060e1d'); bg.addColorStop(1, '#030812');
    c.fillStyle = bg; c.fillRect(0, 0, w, h);
    for (let i = 0; i < 190; i++) {
      const x = ((i * 179.37 + 91) % 997) / 997 * w, y = ((i * 311.61 + 23) % 991) / 991 * h;
      c.fillStyle = `rgba(195,216,245,${.12 + (i % 7) * .055})`;
      c.beginPath(); c.arc(x, y, i % 17 === 0 ? 1 : .5, 0, TAU); c.fill();
    }
    const sunView = this.drawSun(sun, w, h);
    const orbitalR = EARTH + (this.data.physics.orbit.altitude_km || 1200);
    const orbitLines = [];
    if (this.orbits) {
      const planes = [...new Map(this.data.nodes.filter(n => n.kind === 'satellite').map(n => [n.plane, n])).values()];
      for (const node of planes) {
        const a = (node.orbit.raan_deg ?? (node.plane - 1) * 360 / planes.length) * Math.PI / 180;
        const i = (node.orbit.inclination_deg ?? 53) * Math.PI / 180;
        const points = Array.from({length: 241}, (_, k) => {
          const t = k * TAU / 240;
          return this.project([orbitalR * (Math.cos(a) * Math.cos(t) - Math.sin(a) * Math.sin(t) * Math.cos(i)),
            orbitalR * (Math.sin(a) * Math.cos(t) + Math.cos(a) * Math.sin(t) * Math.cos(i)),
            orbitalR * Math.sin(t) * Math.sin(i)]);
        });
        orbitLines.push(points);
        this.path(points, 'rgba(117,166,204,.11)', .7, true);
      }
    }
    const surface = this.surface.draw({width: this.canvas.width, height: this.canvas.height,
      cx: this.cx * dpr, cy: this.cy * dpr, radius: earth * dpr, yaw: this.yaw, pitch: this.pitch,
      gmst: frame.gmst, sun: sun?.direction || [0,0,0]});
    c.drawImage(surface, 0, 0, w, h);
    for (const points of orbitLines) this.path(points, 'rgba(142,193,223,.27)', .8);
    const projected = Object.fromEntries(Object.entries({...frame.positions, ...frame.ground})
      .map(([id, v]) => [id, {id, ...this.project(v)}]));
    const primary = this.data.plans[this.frame]?.paths?.['n3-forward']?.[0]?.nodes || [];
    const primaryEdges = new Set(primary.slice(1).map((n, i) => [primary[i], n].sort().join('|'))), seen = new Set();
    if (this.links || this.selected) for (const link of frame.links) {
      const key = [link.source, link.target].sort().join('|');
      if (seen.has(key) || !link.operational_up) continue;
      seen.add(key);
      const a = projected[link.source], b = projected[link.target];
      if (!a || !b) continue;
      const selected = link.source === this.selected || link.target === this.selected;
      if (!this.links && !selected) continue;
      this.link(a, b, selected ? 'rgba(129,214,255,.9)' : primaryEdges.has(key) ? '#ffc77d' : 'rgba(167,185,210,.18)',
        selected || primaryEdges.has(key) ? 1.6 : .6);
    }
    this.hit = Object.values(projected).filter(p => this.visible(p)).sort((a, b) => a.z - b.z);
    for (const p of this.hit) {
      const isGround = p.id.startsWith('gw-'), isPrimary = primary.includes(p.id), selected = p.id === this.selected;
      const highlighted = selected || p.id === this.hovered;
      if (p.x < -20 || p.x > w + 20 || p.y < -20 || p.y > h + 20) continue;
      // Screen-space markers remain legible at every zoom: bright white core,
      // opaque dark keyline over bright terrain, and shape-coded ground stations.
      const radius = highlighted ? 4.4 : isGround ? 5 : 3.2;
      c.fillStyle = isGround ? '#ffcd87' : '#ffffff';
      c.strokeStyle = '#050b16'; c.lineWidth = 2.8;
      c.beginPath();
      if (isGround) {
        c.moveTo(p.x, p.y - radius - 1); c.lineTo(p.x + radius + 1, p.y);
        c.lineTo(p.x, p.y + radius + 1); c.lineTo(p.x - radius - 1, p.y); c.closePath();
      } else c.arc(p.x, p.y, radius, 0, TAU);
      c.stroke(); c.fill();
      if (highlighted || (isPrimary && !isGround)) {
        c.beginPath(); c.arc(p.x, p.y, highlighted ? 10 : 7, 0, TAU);
        c.strokeStyle = '#050b16'; c.lineWidth = 4; c.stroke();
        c.strokeStyle = highlighted ? '#81d6ff' : '#ffc77d'; c.lineWidth = 1.6; c.stroke();
      }
      if (highlighted || isGround) {
        c.font = '20px ui-monospace,monospace';
        const site = isGround ? this.data.physics.ground_stations.find(s=>s.id===p.id) : null;
        const label = site?.city ? `${site.city} ${p.id.slice(3)}` : p.id.toUpperCase(), labelWidth = c.measureText(label).width;
        const lx = clamp(p.x + 16, 8, w - labelWidth - 16), ly = clamp(p.y, 20, h - 20);
        c.fillStyle = 'rgba(3,9,19,.87)'; c.fillRect(lx - 6, ly - 16, labelWidth + 12, 30);
        c.fillStyle = highlighted ? '#fff' : '#ffcd87'; c.fillText(label, lx, ly + 6);
      }
    }
    // Keep directional markers readable above the globe in a close-up view.
    if (sunView.position?.offscreen) this.drawSun(sun, w, h);
    this.syncHUD(sun, sunView);
  }
}
