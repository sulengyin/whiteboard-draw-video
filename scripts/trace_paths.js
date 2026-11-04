#!/usr/bin/env node
/**
 * 笔迹路径生成器：把一张插画拆成"一个个封闭图形"，让笔沿每个图形的最外圈描一遍，
 * 描到闭环时整块填出来。
 *
 * 用法：
 *   node trace_paths.js --in ./scenes --out ./paths
 *   node trace_paths.js --in ./scenes/01.jpg --out ./paths
 *   node trace_paths.js --in ./scenes --out ./paths --only 01,02
 *   node trace_paths.js --in ./scenes --out ./paths --paper 246,236,216 --th 0.4
 *
 * 输出（每张图一份）：
 *   <out>/<stem>.json
 *   { w, h, len, regions:[{ cx, cy, t0, t1, ring:[x,y,...], rings:[[...],...], len }] }
 *   ring  —— 最外圈，笔走这条（像素格点坐标）
 *   rings —— 外圈 + 内孔，用 even-odd 整块填充
 *   t0/t1 —— 描这一圈的开始/结束（归一化 0..1），t1 时整块出现
 *
 * 依赖：jpeg-js、pngjs（npm i jpeg-js pngjs）
 */
const fs = require('fs');
const path = require('path');

const { loadDep } = require('./_deps');
let jpeg = null, PNG = null;
jpeg = loadDep('jpeg-js');
const pngMod = loadDep('pngjs');
PNG = pngMod && pngMod.PNG;

/* ---------------- 参数 ---------------- */

function parseArgs(argv) {
  const a = { in: '.', out: 'paths', only: null, paper: null, th: 0.4,
              min: null, hole: null, rowTol: null, rdp: null, quiet: false };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i], v = argv[i + 1];
    if (k === '--in') { a.in = v; i++; }
    else if (k === '--out') { a.out = v; i++; }
    else if (k === '--only') { a.only = v.split(',').map(s => s.trim()); i++; }
    else if (k === '--paper') { a.paper = v.split(',').map(Number); i++; }
    else if (k === '--th') { a.th = Number(v); i++; }
    else if (k === '--min') { a.min = Number(v); i++; }
    else if (k === '--hole') { a.hole = Number(v); i++; }
    else if (k === '--row-tol') { a.rowTol = Number(v); i++; }
    else if (k === '--rdp') { a.rdp = Number(v); i++; }
    else if (k === '--quiet') { a.quiet = true; }
    else if (k === '--help' || k === '-h') { console.log(fs.readFileSync(__filename, 'utf8').split('*/')[0]); process.exit(0); }
  }
  return a;
}

const A = parseArgs(process.argv);
const OUT = path.resolve(A.out);
fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(path.join(OUT, 'preview'), { recursive: true });

const exts = ['.jpg', '.jpeg', '.png'];
function collect(p) {
  const st = fs.statSync(p);
  if (st.isFile()) return [p];
  return fs.readdirSync(p)
    .filter(f => exts.includes(path.extname(f).toLowerCase()))
    .sort()
    .map(f => path.join(p, f));
}

let files = collect(path.resolve(A.in));
if (A.only) files = files.filter(f => A.only.includes(path.basename(f, path.extname(f)).replace(/^scene-/, '')));

if (!files.length) { console.error('没有找到图片（支持 .jpg/.jpeg/.png）'); process.exit(1); }

/* ---------------- 图像 ---------------- */

let W = 0, H = 0;          // 全局尺寸，下面所有函数共用
let MIN_PX = 80, HOLE_PX = 40, RDP_EPS = 0.8, ROW_TOL = 76;

function decode(file) {
  const ext = path.extname(file).toLowerCase();
  const buf = fs.readFileSync(file);
  if (ext === '.png') {
    if (!PNG) throw new Error('缺 pngjs：npm i pngjs');
    const p = PNG.sync.read(buf);
    return { width: p.width, height: p.height, data: p.data };
  }
  if (!jpeg) throw new Error('缺 jpeg-js：npm i jpeg-js');
  return jpeg.decode(buf, { maxMemoryUsageInMB: 2048 });
}

function inkAlpha(img) {
  const d = img.data;
  const corner = (x, y) => { const o = (y * W + x) * 4; return [d[o], d[o + 1], d[o + 2]]; };
  let paper = A.paper;
  if (!paper) {
    const cs = [corner(3, 3), corner(W - 4, 3), corner(3, H - 4), corner(W - 4, H - 4)];
    paper = [0, 1, 2].map(k => cs.reduce((s, c) => s + c[k], 0) / cs.length);
  }
  const a = new Float32Array(W * H);
  for (let i = 0, p = 0; p < W * H; i += 4, p++) {
    const dist = Math.max(Math.abs(d[i] - paper[0]), Math.abs(d[i + 1] - paper[1]), Math.abs(d[i + 2] - paper[2]));
    // 9..24 之间软过渡：低于 9 是纸，高于 24 是墨
    a[p] = Math.max(0, Math.min(1, (dist - 9) / 15));
  }
  return { alpha: a, paper };
}

function binarize(alpha, th) {
  const m = new Uint8Array(W * H);
  for (let p = 0; p < W * H; p++) if (alpha[p] >= th) m[p] = 1;
  return m;
}

/* ---------------- 形态学 ---------------- */

function morph(m, r, mode) {
  const tmp = new Uint8Array(W * H), out = new Uint8Array(W * H);
  const pick = mode === 'dilate' ? Math.max : Math.min;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let v = mode === 'dilate' ? 0 : 1;
      for (let k = -r; k <= r; k++) {
        const xx = x + k; if (xx < 0 || xx >= W) continue;
        v = pick(v, m[y * W + xx]);
      }
      tmp[y * W + x] = v;
    }
  }
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let v = mode === 'dilate' ? 0 : 1;
      for (let k = -r; k <= r; k++) {
        const yy = y + k; if (yy < 0 || yy >= H) continue;
        v = pick(v, tmp[yy * W + x]);
      }
      out[y * W + x] = v;
    }
  }
  return out;
}

const close = (m, r) => morph(morph(m, r, 'dilate'), r, 'erode');

function dropSmall(m, minPx) {
  const lab = new Int32Array(W * H).fill(-1);
  const q = new Int32Array(W * H);
  const out = new Uint8Array(W * H);
  let id = 0;
  for (let p = 0; p < W * H; p++) {
    if (!m[p] || lab[p] >= 0) continue;
    let head = 0, tail = 0; q[tail++] = p; lab[p] = id;
    while (head < tail) {
      const i = q[head++], x = i % W, y = (i / W) | 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const nx = x + dx, ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
        const j = ny * W + nx;
        if (m[j] && lab[j] < 0) { lab[j] = id; q[tail++] = j; }
      }
    }
    if (tail >= minPx) for (let k = 0; k < tail; k++) out[q[k]] = 1;
    id++;
  }
  return out;
}

function fillHoles(m, maxHole) {
  const seen = new Uint8Array(W * H);
  const q = new Int32Array(W * H);
  const out = Uint8Array.from(m);
  for (let p = 0; p < W * H; p++) {
    if (m[p] || seen[p]) continue;
    let head = 0, tail = 0; q[tail++] = p; seen[p] = 1;
    let edge = false;
    while (head < tail) {
      const i = q[head++], x = i % W, y = (i / W) | 0;
      if (x === 0 || y === 0 || x === W - 1 || y === H - 1) edge = true;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const nx = x + dx, ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
        const j = ny * W + nx;
        if (!m[j] && !seen[j]) { seen[j] = 1; q[tail++] = j; }
      }
    }
    if (!edge && tail <= maxHole) for (let k = 0; k < tail; k++) out[q[k]] = 1;
  }
  return out;
}

/* ---------------- 连通图形 ---------------- */

function labelRegions(m) {
  const lab = new Int32Array(W * H).fill(-1);
  const q = new Int32Array(W * H);
  const comps = [];
  for (let p = 0; p < W * H; p++) {
    if (!m[p] || lab[p] >= 0) continue;
    const id = comps.length;
    let head = 0, tail = 0; q[tail++] = p; lab[p] = id;
    let x0 = W, y0 = H, x1 = 0, y1 = 0;
    while (head < tail) {
      const i = q[head++], x = i % W, y = (i / W) | 0;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const nx = x + dx, ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
        const j = ny * W + nx;
        if (m[j] && lab[j] < 0) { lab[j] = id; q[tail++] = j; }
      }
    }
    comps.push({ id, size: tail, x0, y0, x1, y1 });
  }
  return { lab, comps };
}

/* ---------------- 轮廓环（沿像素边缘，前景在右） ---------------- */

const DIRS = [[1, 0], [0, 1], [-1, 0], [0, -1]];   // 右 下 左 上

function traceRings(lab, comp, labAt) {
  const V = W + 1;
  const same = (x, y, id) => x >= 0 && y >= 0 && x < W && y < H && labAt(x, y) === id;
  const edges = new Map();
  const addEdge = (fx, fy, tx, ty, dir) => {
    const from = fy * V + fx;
    let arr = edges.get(from);
    if (!arr) { arr = []; edges.set(from, arr); }
    arr.push({ to: ty * V + tx, dir, used: false });
  };

  for (let y = comp.y0; y <= comp.y1; y++) {
    for (let x = comp.x0; x <= comp.x1; x++) {
      if (labAt(x, y) !== comp.id) continue;
      if (!same(x, y - 1, comp.id)) addEdge(x, y, x + 1, y, 0);          // 上边 →
      if (!same(x + 1, y, comp.id)) addEdge(x + 1, y, x + 1, y + 1, 1);  // 右边 ↓
      if (!same(x, y + 1, comp.id)) addEdge(x + 1, y + 1, x, y + 1, 2);  // 下边 ←
      if (!same(x - 1, y, comp.id)) addEdge(x, y + 1, x, y, 3);          // 左边 ↑
    }
  }

  const rings = [];
  for (const [from, arr] of edges) {
    for (const e0 of arr) {
      if (e0.used) continue;
      const ring = [];
      let cur = from, e = e0;
      for (let guard = 0; guard < 400000; guard++) {
        e.used = true;
        ring.push(cur % V, (cur / V) | 0);
        cur = e.to;
        if (cur === from) break;
        const outs = edges.get(cur);
        if (!outs) break;
        const back = (e.dir + 2) & 3;
        const order = [(back + 3) & 3, back, (back + 1) & 3, (back + 2) & 3];
        let pick = null;
        for (const d of order) {
          pick = outs.find(o => !o.used && o.dir === d);
          if (pick) break;
        }
        if (!pick) pick = outs.find(o => !o.used);
        if (!pick) break;
        e = pick;
      }
      if (ring.length >= 8) rings.push(ring);
    }
  }
  return rings;
}

function ringArea(r) {
  let a = 0;
  for (let i = 0; i < r.length; i += 2) {
    const j = (i + 2) % r.length;
    a += r[i] * r[j + 1] - r[j] * r[i + 1];
  }
  return a / 2;
}

/* ---------------- 抽稀 ---------------- */

function rdpOpen(pts, eps) {
  if (pts.length < 3) return pts;
  const keep = new Uint8Array(pts.length);
  keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop();
    const [x0, y0] = pts[a], [x1, y1] = pts[b];
    const dx = x1 - x0, dy = y1 - y0;
    const len = Math.hypot(dx, dy) || 1;
    let best = -1, bi = -1;
    for (let i = a + 1; i < b; i++) {
      const d = Math.abs(dy * (pts[i][0] - x0) - dx * (pts[i][1] - y0)) / len;
      if (d > best) { best = d; bi = i; }
    }
    if (best > eps) { keep[bi] = 1; stack.push([a, bi], [bi, b]); }
  }
  return pts.filter((_, i) => keep[i]);
}

function simplifyRing(ring) {
  const n = ring.length / 2;
  const pts = [];
  for (let i = 0; i < n; i++) pts.push([ring[i * 2], ring[i * 2 + 1]]);
  if (pts.length < 8) return ring;

  let far = 1, best = -1;
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]);
    if (d > best) { best = d; far = i; }
  }
  if (best < 2) return ring;
  const a = rdpOpen(pts.slice(0, far + 1), RDP_EPS);
  const b = rdpOpen(pts.slice(far), RDP_EPS);
  const merged = a.concat(b.slice(1, -1));
  if (merged.length < 4) return ring;
  const out = [];
  for (const [x, y] of merged) out.push(x, y);
  return out;
}

function ringLength(r) {
  let L = 0;
  for (let i = 0; i < r.length; i += 2) {
    const j = (i + 2) % r.length;
    L += Math.hypot(r[j] - r[i], r[j + 1] - r[i + 1]);
  }
  return L;
}

/* ---------------- 自检：多边形填充 vs 墨迹 ---------------- */

function fillPolygon(mask, rings, w = W, h = H) {
  for (let y = 0; y < h; y++) {
    const sy = y + 0.5;
    const xs = [];
    for (const r of rings) {
      const n = r.length / 2;
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        const y0 = r[i * 2 + 1], y1 = r[j * 2 + 1];
        const x0 = r[i * 2], x1 = r[j * 2];
        if (y0 === y1) continue;
        if ((sy >= Math.min(y0, y1)) && (sy < Math.max(y0, y1))) {
          xs.push(x0 + (sy - y0) * (x1 - x0) / (y1 - y0));
        }
      }
    }
    xs.sort((a, b) => a - b);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const a = Math.max(0, Math.round(xs[k])), b = Math.min(w - 1, Math.round(xs[k + 1]) - 1);
      for (let x = a; x <= b; x++) mask[y * w + x] = 1;
    }
  }
}

/* ---------------- 主流程 ---------------- */

const report = [];

for (const file of files) {
  const stem = path.basename(file, path.extname(file));
  const img = decode(file);
  W = img.width; H = img.height;

  // 参数按画布面积等比缩放（基准 1024×1536）
  const k = Math.sqrt((W * H) / (1024 * 1536));
  const minPx = A.min != null ? A.min : Math.max(12, Math.round(80 * k));
  const holePx = A.hole != null ? A.hole : Math.max(6, Math.round(40 * k));
  ROW_TOL = A.rowTol != null ? A.rowTol : Math.max(20, Math.round(76 * k));
  RDP_EPS = A.rdp != null ? A.rdp : Math.max(0.5, 0.8 * k);

  const { alpha, paper } = inkAlpha(img);
  let m = binarize(alpha, A.th);
  m = close(m, Math.max(1, Math.round(2 * k)));
  m = dropSmall(m, minPx);
  m = fillHoles(m, holePx);

  const { lab, comps } = labelRegions(m);
  const labAt = (x, y) => (x < 0 || y < 0 || x >= W || y >= H) ? -1 : lab[y * W + x];

  const regions = [];
  for (const c of comps) {
    const rings = traceRings(lab, c, labAt).map(simplifyRing).filter(r => r.length >= 8);
    if (!rings.length) continue;
    let main = rings[0], mainA = Math.abs(ringArea(rings[0]));
    for (const r of rings) {
      const a = Math.abs(ringArea(r));
      if (a > mainA) { mainA = a; main = r; }
    }
    if (mainA < minPx * 0.5) continue;
    regions.push({
      cx: (c.x0 + c.x1) / 2, cy: (c.y0 + c.y1) / 2,
      ring: main, rings, len: ringLength(main)
    });
  }

  /* 排序：先按 cy 聚成行，行内按 cx 从左到右 */
  regions.sort((a, b) => a.cy - b.cy);
  const rows = [];
  for (const r of regions) {
    const row = rows[rows.length - 1];
    if (row && r.cy - row.y0 <= ROW_TOL) row.items.push(r);
    else rows.push({ y0: r.cy, items: [r] });
  }
  regions.length = 0;
  for (const row of rows) {
    row.items.sort((a, b) => a.cx - b.cx);
    for (const r of row.items) regions.push(r);
  }

  /* 时间：按轮廓长度分，抬笔距离打 0.45 折 */
  let cost = 0, prev = null;
  for (const r of regions) {
    if (prev) cost += Math.hypot(r.cx - prev.cx, r.cy - prev.cy) * 0.45;
    r.t0 = cost;
    cost += Math.max(60, r.len);
    r.t1 = cost;
    prev = r;
  }
  const total = cost || 1;

  /* 自检 */
  const filled = new Uint8Array(W * H);
  fillPolygon(filled, regions.flatMap(r => r.rings));
  let inter = 0, uni = 0;
  for (let p = 0; p < W * H; p++) {
    if (filled[p] && m[p]) inter++;
    if (filled[p] || m[p]) uni++;
  }
  const iou = uni ? inter / uni : 0;

  const out = {
    w: W, h: H, len: Math.round(total), paper,
    regions: regions.map(r => ({
      cx: Math.round(r.cx), cy: Math.round(r.cy),
      t0: +(r.t0 / total).toFixed(5), t1: +(r.t1 / total).toFixed(5),
      ring: r.ring, rings: r.rings, len: Math.round(r.len)
    }))
  };
  fs.writeFileSync(path.join(OUT, `${stem}.json`), JSON.stringify(out));

  /* 顺序预览：蓝 → 红 = 先 → 后 */
  const sw = W >> 1, sh = H >> 1;
  const png = new PNG({ width: sw, height: sh });
  const src = img.data;
  for (let y = 0; y < sh; y++) for (let x = 0; x < sw; x++) {
    const s = ((y * 2) * W + x * 2) * 4, t = (y * sw + x) * 4;
    png.data[t] = Math.round(src[s] * .35 + 165);
    png.data[t + 1] = Math.round(src[s + 1] * .35 + 165);
    png.data[t + 2] = Math.round(src[s + 2] * .35 + 165);
    png.data[t + 3] = 255;
  }
  const put = (x, y, c) => {
    x |= 0; y |= 0;
    if (x < 0 || y < 0 || x >= sw || y >= sh) return;
    const t = (y * sw + x) * 4;
    png.data[t] = c[0]; png.data[t + 1] = c[1]; png.data[t + 2] = c[2];
  };
  for (const r of regions) {
    const q = r.t1 / total;
    const col = [Math.round(20 + 215 * q), Math.round(90 - 40 * q), Math.round(235 - 205 * q)];
    for (let i = 0; i < r.ring.length; i += 2) {
      const j = (i + 2) % r.ring.length;
      const steps = Math.ceil(Math.hypot(r.ring[j] - r.ring[i], r.ring[j + 1] - r.ring[i + 1]) / 0.5) + 1;
      for (let s = 0; s <= steps; s++) {
        put((r.ring[i] + (r.ring[j] - r.ring[i]) * s / steps) / 2,
            (r.ring[i + 1] + (r.ring[j + 1] - r.ring[i + 1]) * s / steps) / 2, col);
      }
    }
  }
  fs.writeFileSync(path.join(OUT, 'preview', `${stem}-path.png`), PNG.sync.write(png));

  const pts = regions.reduce((s, r) => s + r.ring.length / 2, 0);
  report.push({ file: path.basename(file), w: W, h: H, regions: regions.length, points: pts,
                lengthPx: Math.round(total), iou: +(iou * 100).toFixed(2) });
  if (!A.quiet) {
    const flag = iou >= 0.99 ? 'OK ' : iou >= 0.95 ? 'warn' : 'FAIL';
    console.log(`[${flag}] ${stem}  ${W}×${H}  ${regions.length} 图形 / ${pts} 点 / ${Math.round(total)}px / 吻合 ${(iou * 100).toFixed(1)}%`);
  }
}

fs.writeFileSync(path.join(OUT, '_report.json'), JSON.stringify({ paper: A.paper, files: report }, null, 1));

const bad = report.filter(r => r.iou < 95);
if (bad.length) {
  console.error(`\n有 ${bad.length} 张吻合率低于 95%：${bad.map(b => b.file + ' ' + b.iou + '%').join(', ')}`);
  console.error('多半是对角粘连或图片不是浅色纸底 —— 试 --th 0.3 / --min 120 / --paper r,g,b');
  process.exit(2);
}
