// Local NASA imagery, shared across page changes. No CDN or rendering framework.
let imagery;
function loadImagery() {
  return imagery ||= Promise.all(['earth-day.png', 'earth-night.png'].map(name =>
    new Promise(resolve => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => resolve(null);
      image.src = new URL(`./assets/${name}`, import.meta.url).href;
    })));
}

const VERTEX = `attribute vec2 position;
void main() { gl_Position = vec4(position, 0.0, 1.0); }`;
const FRAGMENT = `precision highp float;
uniform vec2 size, center;
uniform float radius, yaw, pitch, gmst;
uniform vec3 sun;
uniform sampler2D dayMap, nightMap;
const float PI = 3.141592653589793;
void main() {
  vec2 p = (vec2(gl_FragCoord.x, size.y - gl_FragCoord.y) - center) / radius;
  p.y = -p.y;
  float r2 = dot(p, p);
  if (r2 > 1.1025) { gl_FragColor = vec4(0.0); return; }
  vec3 camera = vec3(p, sqrt(max(0.0, 1.0 - r2)));
  float zz = -camera.y * sin(pitch) + camera.z * cos(pitch);
  vec3 normal = vec3(camera.x * cos(yaw) - zz * sin(yaw),
    camera.x * sin(yaw) + zz * cos(yaw),
    camera.y * cos(pitch) + camera.z * sin(pitch));
  float light = dot(normalize(normal), sun);
  if (r2 > 1.0) {
    float halo = pow(max(0.0, 1.0 - (sqrt(r2) - 1.0) / .05), 3.0);
    gl_FragColor = vec4(.16, .48, 1.0, halo * (.12 + .38 * smoothstep(-.25, .6, light)));
    return;
  }
  vec2 uv = vec2(fract((atan(normal.y, normal.x) - gmst) / (2.0 * PI) + .5),
    .5 - asin(clamp(normal.z, -1.0, 1.0)) / PI);
  vec3 surface = texture2D(dayMap, uv).rgb;
  float daylight = smoothstep(-.08, .16, light);
  vec3 color = surface * (.075 + 1.12 * sqrt(max(0.0, light)));
  vec3 night = texture2D(nightMap, uv).rgb;
  float lamps = max(night.r, max(night.g, night.b));
  color += vec3(1.0, .66, .31) * lamps * (1.0 - daylight) * .95;
  float ocean = smoothstep(.02, .12, surface.b - surface.r);
  vec3 view = vec3(-sin(yaw) * cos(pitch), cos(yaw) * cos(pitch), sin(pitch));
  float glint = pow(max(0.0, dot(normal, normalize(sun + view))), 70.0);
  color += vec3(.55, .7, .8) * glint * ocean * daylight * .25;
  float rim = pow(1.0 - camera.z, 3.5) * (.16 + .55 * daylight);
  color += vec3(.13, .42, .85) * rim;
  gl_FragColor = vec4(color, 1.0);
}`;

export class EarthSurface {
  constructor(onChange) {
    this.canvas = document.createElement('canvas');
    this.software = document.createElement('canvas');
    this.onChange = onChange;
    this.ready = false;
    this.destroyed = false;
    this.images = [];
    this.contextLost = e => {
      e.preventDefault();
      this.gl = null;
      this.onChange();
    };
    this.canvas.addEventListener('webglcontextlost', this.contextLost);
    this.initializeGL();
    loadImagery().then(images => {
      if (this.destroyed) return;
      this.images = images;
      this.ready = Boolean(images[0]);
      if (this.gl) this.uploadImages();
      this.onChange();
    });
  }

  initializeGL() {
    const gl = this.canvas.getContext('webgl', {alpha: true, antialias: false, premultipliedAlpha: false});
    this.gl = gl;
    if (!gl) return;
    try {
      const compile = (type, source) => {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
          const message = gl.getShaderInfoLog(shader);
          gl.deleteShader(shader);
          throw new Error(message);
        }
        return shader;
      };
      this.program = gl.createProgram();
      for (const shader of [compile(gl.VERTEX_SHADER, VERTEX), compile(gl.FRAGMENT_SHADER, FRAGMENT)]) {
        gl.attachShader(this.program, shader);
        gl.deleteShader(shader);
      }
      gl.linkProgram(this.program);
      if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error('Earth shader link failed');
      gl.useProgram(this.program);
      this.buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), gl.STATIC_DRAW);
      const position = gl.getAttribLocation(this.program, 'position');
      gl.enableVertexAttribArray(position);
      gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
      this.uniforms = Object.fromEntries(['size','center','radius','yaw','pitch','gmst','sun','dayMap','nightMap']
        .map(name => [name, gl.getUniformLocation(this.program, name)]));
      this.textures = [0, 1].map(unit => {
        const texture = gl.createTexture();
        gl.activeTexture(gl.TEXTURE0 + unit);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
          new Uint8Array(unit ? [0,0,0,255] : [18,49,84,255]));
        return texture;
      });
      gl.uniform1i(this.uniforms.dayMap, 0);
      gl.uniform1i(this.uniforms.nightMap, 1);
    } catch (error) {
      console.warn('Earth renderer: using Canvas fallback.', error);
      this.releaseGL();
    }
  }

  uploadImages() {
    const gl = this.gl;
    this.images.forEach((image, unit) => {
      if (!image) return;
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, this.textures[unit]);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
    });
  }

  draw({width, height, cx, cy, radius, yaw, pitch, gmst, sun}) {
    const gl = this.gl;
    if (!gl) return this.drawSoftware({width, height, cx, cy, radius, yaw, pitch, gmst, sun});
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;
    gl.viewport(0, 0, width, height);
    gl.uniform2f(this.uniforms.size, width, height);
    gl.uniform2f(this.uniforms.center, cx, cy);
    gl.uniform1f(this.uniforms.radius, radius);
    gl.uniform1f(this.uniforms.yaw, yaw);
    gl.uniform1f(this.uniforms.pitch, pitch);
    gl.uniform1f(this.uniforms.gmst, gmst);
    gl.uniform3fv(this.uniforms.sun, sun);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    return this.canvas;
  }

  // Browsers without WebGL still get geographic imagery and correct day/night.
  // Limit only fallback resolution so software rendering remains interactive.
  drawSoftware({width, height, cx, cy, radius, yaw, pitch, gmst, sun}) {
    const ratio = Math.min(1, 640 / width, 480 / height);
    const w = Math.max(1, Math.round(width * ratio)), h = Math.max(1, Math.round(height * ratio));
    this.software.width = w;
    this.software.height = h;
    const context = this.software.getContext('2d');
    if (!this.pixels && this.ready) this.pixels = this.images.map(image => {
      if (!image) return null;
      const map = document.createElement('canvas');
      map.width = image.width; map.height = image.height;
      const ctx = map.getContext('2d', {willReadFrequently: true});
      ctx.drawImage(image, 0, 0);
      return {data: ctx.getImageData(0, 0, map.width, map.height).data, width: map.width, height: map.height};
    });
    const output = context.createImageData(w, h), pixels = output.data;
    const ca = Math.cos(yaw), sa = Math.sin(yaw), cb = Math.cos(pitch), sb = Math.sin(pitch);
    const day = this.pixels?.[0], night = this.pixels?.[1];
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const nx = ((x + .5) / ratio - cx) / radius, ny = (cy - (y + .5) / ratio) / radius;
      const r2 = nx * nx + ny * ny;
      if (r2 > 1) continue;
      const nz = Math.sqrt(1 - r2), zz = -ny * sb + nz * cb;
      const ex = nx * ca - zz * sa, ey = nx * sa + zz * ca, ez = ny * cb + nz * sb;
      const u = ((Math.atan2(ey, ex) - gmst) / (2 * Math.PI) + .5) % 1;
      const v = .5 - Math.asin(Math.max(-1, Math.min(1, ez))) / Math.PI;
      const sample = map => map ? (Math.min(map.height - 1, Math.floor(v * map.height)) * map.width +
        Math.floor((u + 1) % 1 * map.width)) * 4 : 0;
      const di = sample(day), ni = sample(night), out = (y * w + x) * 4;
      const light = ex * sun[0] + ey * sun[1] + ez * sun[2];
      const diffuse = .075 + 1.12 * Math.sqrt(Math.max(0, light));
      const lamps = night ? Math.max(...night.data.subarray(ni, ni + 3)) * Math.max(0, Math.min(1, (.1 - light) / .2)) : 0;
      const rim = Math.pow(1 - nz, 3.5) * (light > 0 ? .71 : .16) * 255;
      for (let channel = 0; channel < 3; channel++) pixels[out + channel] =
        (day ? day.data[di + channel] : [18,49,84][channel]) * diffuse +
        lamps * [1,.66,.31][channel] * .95 + rim * [.13,.42,.85][channel];
      pixels[out + 3] = 255;
    }
    context.putImageData(output, 0, 0);
    return this.software;
  }

  releaseGL() {
    if (!this.gl) return;
    for (const texture of this.textures || []) this.gl.deleteTexture(texture);
    if (this.buffer) this.gl.deleteBuffer(this.buffer);
    if (this.program) this.gl.deleteProgram(this.program);
    this.gl.getExtension('WEBGL_lose_context')?.loseContext();
    this.gl = null;
  }

  destroy() {
    this.destroyed = true;
    this.canvas.removeEventListener('webglcontextlost', this.contextLost);
    this.releaseGL();
  }
}
