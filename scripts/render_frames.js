#!/usr/bin/env node
/**
 * 离线截帧：不开浏览器，直接按路径 JSON 合成"描到 X%"的画面。
 * 用来在没有浏览器截图能力时验收「笔尖是否贴着显影边缘、有没有空洞、顺序对不对」。
 *
 * 用法：
 *   node render_frames.js --img ./scenes/01.jpg --path ./paths/01.json --at 0.3,0.6,0.95 --out ./preview
 *   node render_frames.js --dir . --at 0.5            # 自动配 scenes/ 与 paths/
 *
 * 依赖：jpeg-js、pngjs
 */
const fs = require('fs');
const path = require('path');

const { loadDep } = require('./_deps');
let jpeg = null, PNG = null;
jpeg = loadDep('jpeg-js');
const pngMod = loadDep('pngjs');
PNG = pngMod && pngMod.PNG;

function args(argv) {
  const a = { img: null, path: null, dir: null, at: '0.4', out: 'preview', fit: 0.96, half: true, caption: 0 };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i], v = argv[i + 1];
    if (k === '--img') { a.img = v; i++; }
    else if (k === '--path') { a.path = v; i++; }
    else if (k === '--dir') { a.dir = v; i++; }
    else if (k === '--at') { a.at = v; i++; }
    else if (k === '--out') { a.out = v; i++; }
    else if (k === '--fit') { a.fit = Number(v); i++; }
    else if (k === '--caption') { a.caption = Number(v); i++; }
    else if (k === '--full') { a.half = false; }
    else if (k === '--help' || k === '-h') { console.log('node render_frames.js --img a.jpg --path a.json --at 0.3,0.6 --out preview'); process.exit(0); }
  }
  return a;
}
const A = args(process.argv);
const AT = A.at.split(',').map(Number).filter(n => n >= 0 && n <= 1);
fs.mkdirSync(A.out, { recursive: true });

function decode(file) {
  const buf = fs.readFileSync(file);
  if (path.extname(file).toLowerCase() === '.png') {
    if (!PNG) throw new Error('缺 pngjs');
    const p = PNG.sync.read(buf);
    return { width: p.width, height: p.height, data: p.data };
  }
  if (!jpeg) throw new Error('缺 jpeg-js');
  return jpeg.decode(buf, { maxMemoryUsageInMB: 2048 });
}

function renderOne(imgFile, pjFile, prog) {
  const img = decode(imgFile);
  const pj = JSON.parse(fs.readFileSync(pjFile, 'utf8'));
  const W = img.width, H = img.height;
  const SC = A.half ? 2 : 1;                 // 默认半尺寸出图，快
  const ow = Math.round(W / SC), oh = Math.round(H / SC);
  const PEN = (pj.penWidth || 11) / SC;
  const R = Math.max(0.8, PEN / 2);

  const d = img.data;
  const corner = (x, y) => { const o = (y * W + x) * 4; return [d[o], d[o + 1], d[o + 2]]; };
  const cs = [corner(3, 3), corner(W - 4, 3), corner(3, H - 4), corner(W - 4, H - 4)];
  const paper = pj.paper || [0, 1, 2].map(k => cs.reduce((s, c) => s + c[k], 0) / cs.length);

  const mask = new Float32Array(ow * oh);

  function dot(cx, cy) {
    const x0 = Math.max(0, Math.floor(cx - R - 1)), x1 = Math.min(ow - 1, Math.ceil(cx + R + 1));
    const y0 = Math.max(0, Math.floor(cy - R - 1)), y1 = Math.min(oh - 1, Math.ceil(cy + R + 1));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const dist = Math.hypot(x + 0.5 - cx, y + 0.5 - cy);
        const a = dist <= R - 0.5 ? 1 : dist >= R + 0.5 ? 0 : (R + 0.5 - dist);
        const i = y * ow + x;
        if (a > mask[i]) mask[i] = a;
      }
    }
  }

  function stroke(reg, from, to) {
    const q = reg.ring, n = q.length / 2;
    const at = i => [q[(i % n) * 2] / SC, q[(i % n) * 2 + 1] / SC];
    let prev = at(from);
    dot(prev[0], prev[1]);
    for (let i = from + 1; i <= to; i++) {
      const cur = at(i);
      const steps = Math.ceil(Math.hypot(cur[0] - prev[0], cur[1] - prev[1]) / Math.max(0.6, R * 0.5)) || 1;
      for (let s = 1; s <= steps; s++) {
        dot(prev[0] + (cur[0] - prev[0]) * s / steps, prev[1] + (cur[1] - prev[1]) * s / steps);
      }
      prev = cur;
    }
  }

  function fill(reg) {
    for (let y = 0; y < oh; y++) {
      const sy = y * SC + SC / 2;
      const xs = [];
      for (const r of reg.rings) {
        const n = r.length / 2;
        for (let i = 0; i < n; i++) {
          const j = (i + 1) % n;
          const y0 = r[i * 2 + 1], y1 = r[j * 2 + 1];
          if (y0 === y1) continue;
          if (sy >= Math.min(y0, y1) && sy < Math.max(y0, y1)) {
            const x0 = r[i * 2], x1 = r[j * 2];
            xs.push(x0 + (sy - y0) * (x1 - x0) / (y1 - y0));
          }
        }
      }
      xs.sort((a, b) => a - b);
      for (let k = 0; k + 1 < xs.length; k += 2) {
        const a = Math.max(0, Math.round(xs[k] / SC)), b = Math.min(ow - 1, Math.round(xs[k + 1] / SC) - 1);
        for (let x = a; x <= b; x++) mask[y * ow + x] = 1;
      }
    }
  }

  for (const reg of pj.regions) {
    const n = reg.ring.length / 2;
    if (prog < reg.t0) break;
    if (prog >= reg.t1) { stroke(reg, 0, n); fill(reg); continue; }
    const f = (prog - reg.t0) / Math.max(1e-6, reg.t1 - reg.t0);
    stroke(reg, 0, Math.min(n, Math.floor(f * n)));
    break;
  }

  // 按 FIT 缩到画布中央，留纸边（与页面 drawPhoto 的构图一致）
  const cw = ow, ch = oh + A.caption;
  const k = A.fit;
  const dw = Math.round(ow * k), dh = Math.round(oh * k);
  const dx = Math.round((cw - dw) / 2), dy = Math.round((ch - A.caption - dh) / 2);

  const png = new PNG({ width: cw, height: ch });
  for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) {
    const t = (y * cw + x) * 4;
    png.data[t] = paper[0]; png.data[t + 1] = paper[1]; png.data[t + 2] = paper[2]; png.data[t + 3] = 255;
  }
  for (let y = 0; y < dh; y++) {
    const sy = Math.min(oh - 1, Math.floor(y / k));
    for (let x = 0; x < dw; x++) {
      const sx = Math.min(ow - 1, Math.floor(x / k));
      const s = (sy * SC * W + sx * SC) * 4;
      const r = d[s], g = d[s + 1], b = d[s + 2];
      const dist = Math.max(Math.abs(r - paper[0]), Math.abs(g - paper[1]), Math.abs(b - paper[2]));
      const inkA = Math.max(0, Math.min(1, (dist - 9) / 15));
      const a = inkA * mask[sy * ow + sx];
      const t = ((y + dy) * cw + (x + dx)) * 4;
      png.data[t] = paper[0] + (r - paper[0]) * a;
      png.data[t + 1] = paper[1] + (g - paper[1]) * a;
      png.data[t + 2] = paper[2] + (b - paper[2]) * a;
      png.data[t + 3] = 255;
    }
  }

  const stem = path.basename(pjFile, '.json');
  const dst = path.join(A.out, `frame-${stem}-${Math.round(prog * 100)}.png`);
  fs.writeFileSync(dst, PNG.sync.write(png));

  // 覆盖率：mask 里的 1 占墨迹像素的比例（判断有没有漏画）
  let ink = 0, covered = 0;
  for (let y = 0; y < oh; y++) for (let x = 0; x < ow; x++) {
    const s = ((y * SC) * W + x * SC) * 4;
    const dist = Math.max(Math.abs(d[s] - paper[0]), Math.abs(d[s + 1] - paper[1]), Math.abs(d[s + 2] - paper[2]));
    if (dist >= 24) { ink++; if (mask[y * ow + x] > 0.2) covered++; }
  }
  const cov = ink ? covered / ink : 1;
  console.log(`${dst}  覆盖 ${(cov * 100).toFixed(1)}%`);
  return dst;
}

if (A.dir) {
  const dir = path.resolve(A.dir);
  const scenes = path.join(dir, 'scenes'), pathsDir = path.join(dir, 'paths');
  const imgs = fs.readdirSync(fs.existsSync(scenes) ? scenes : dir)
    .filter(f => /\.(jpe?g|png)$/i.test(f)).sort();
  for (const f of imgs) {
    const stem = path.basename(f, path.extname(f));
    const imgFile = fs.existsSync(scenes) ? path.join(scenes, f) : path.join(dir, f);
    const pj = path.join(pathsDir, `${stem}.json`);
    if (!fs.existsSync(pj)) { console.warn(`跳过 ${f}（没有 ${pj}）`); continue; }
    for (const p of AT) renderOne(imgFile, pj, p);
  }
} else {
  if (!A.img || !A.path) { console.error('需要 --img 和 --path，或用 --dir'); process.exit(1); }
  for (const p of AT) renderOne(path.resolve(A.img), path.resolve(A.path), p);
}
