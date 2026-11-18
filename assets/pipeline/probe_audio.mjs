#!/usr/bin/env node
/**
 * probe_audio.mjs —— 成片音频体检，一条命令给出「是不是文件的问题」。
 *
 * 用法：
 *   node build/probe_audio.mjs out/xxx.mp4
 *   node build/probe_audio.mjs out/xxx.mp4 --ref audio/01.wav   # 额外比对起点
 *
 * 输出四组判据（任一组报 FAIL 就按提示处理，不要靠耳朵猜）：
 *   1. 编码        Opus 在 MP4 里是 Chrome 的私货，很多播放器解出杂音 → 转 AAC
 *   2. 时长一致    容器总时长 vs 包内真实时长，差 >2% 说明 time table 坏了 → retime_mp4.py
 *   3. 电平        峰值 ≤0dBFS 且削波样本少，否则先 prep_audio.py 重洗配音
 *   4. 连续性      包边界跳变 / 掉块，说明包被叠放或截断
 *
 * 退出码：0 全过；1 有 FAIL（可直接用于 CI / 交付前闸门）。
 */
import fs from 'fs';
import path from 'path';
import { createFile } from 'mp4box';
import { OpusDecoder } from 'opus-decoder';

const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith('--'));
if (!file) {
  console.error('用法：node probe_audio.mjs <file.mp4> [--ref audio/01.wav]');
  process.exit(2);
}
const refIdx = args.indexOf('--ref');
const refPath = refIdx >= 0 ? args[refIdx + 1] : null;

/* ---------- 1) 拆包 ---------- */
const mp4 = createFile();
const samples = [];
let track = null;
mp4.onError = (e) => {
  console.error('解封装失败：', e);
  process.exit(2);
};
mp4.onReady = (info) => {
  track = info.tracks.find((t) => t.type === 'audio' || (t.audio && !t.video));
  const v = info.tracks.find((t) => t.video);
  if (!track) {
    console.error('这个文件没有音轨');
    process.exit(2);
  }
  console.log(`容器：${info.tracks.length} 轨` +
    `（视频 ${v ? `${v.codec} ${v.video.width}x${v.video.height}` : '无'}）`);
  mp4.setExtractionOptions(track.id, null, { nbSamples: 1e6 });
  mp4.start();
};
mp4.onSamples = (id, u, s) => {
  for (const x of s) samples.push(x);
};

const buf = fs.readFileSync(file);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
ab.fileStart = 0;
mp4.appendBuffer(ab);
mp4.flush();

await new Promise((r) => setTimeout(r, 800));

const codec = (track.codec || '').toLowerCase();
const ts = track.timescale || 48000;
const containerSec = track.duration / ts;
const fragDur = samples.reduce((a, s) => a + s.duration, 0) / ts;

const fails = [];
const ok = (label, msg) => console.log(`  PASS  ${label}：${msg}`);
const bad = (label, msg) => {
  console.log(`  FAIL  ${label}：${msg}`);
  fails.push(label);
};

console.log(`\n音轨：${track.codec}  ${track.audio.sample_rate}Hz  ${track.audio.channel_count}ch` +
  `  ${samples.length} 包`);

/* ---------- 2) 编码 ---------- */
const isOpus = codec.includes('opus');
if (isOpus) {
  bad('编码', 'MP4 里装的是 Opus。Windows 媒体播放器 / 手机自带 / 微信解它常出一层沙沙底噪，' +
    '文件本身没问题。交付前跑 node build/to_aac.mjs');
} else {
  ok('编码', `${track.codec}（MP4 原生格式）`);
}

/* ---------- 3) 时长一致性 ---------- */
const drift = containerSec ? Math.abs(containerSec - fragDur) / fragDur : 1;
if (containerSec < fragDur * 0.5) {
  console.log(`  容器只声明了 ${containerSec.toFixed(2)}s，包内实际 ${fragDur.toFixed(2)}s`);
} else {
  console.log(`  容器声称 ${containerSec.toFixed(2)}s，包内累计 ${fragDur.toFixed(2)}s`);
}
if (drift > 0.02) {
  bad('时长一致', `差 ${(drift * 100).toFixed(1)}%。time table 坏了，播放器会把相邻包叠起来 → 沙沙声。` +
    '跑 python3 build/retime_mp4.py');
} else {
  ok('时长一致', `差 ${(drift * 100).toFixed(1)}%`);
}

/* ---------- 4) 解码（仅 Opus 可解） ---------- */
let peak = null;
let clips = 0;
let mono = null;
let sr = 48000;
if (isOpus) {
  const dec = new OpusDecoder({ sampleRate: 48000, channels: 2 });
  await dec.ready;
  const chunks = [];
  let undecodable = 0;
  for (const p of samples) {
    try {
      const r = dec.decodeFrame(new Uint8Array(p.data));
      if (!r || !r.samplesDecoded) {
        undecodable++;
        continue;
      }
      sr = r.sampleRate;
      chunks.push(r.channelData[0]);
    } catch {
      undecodable++;
    }
  }
  const total = chunks.reduce((a, c) => a + c.length, 0);
  mono = new Float32Array(total);
  let off = 0;
  for (const c of chunks) {
    mono.set(c, off);
    off += c.length;
  }
  peak = 0;
  for (const v of mono) {
    const a = Math.abs(v);
    if (a > peak) peak = a;
    if (a > 0.999) clips++;
  }
  if (undecodable) bad('可解码', `${undecodable} / ${samples.length} 包解不开`);
  else ok('可解码', `${samples.length} 包全部成功，${(total / sr).toFixed(2)}s`);
}

/* ---------- 5) 电平 ---------- */
if (peak !== null) {
  const dbfs = 20 * Math.log10(peak + 1e-12);
  console.log(`  峰值 ${peak.toFixed(3)}（${dbfs.toFixed(1)} dBFS），>0.999 的样本 ${clips} 个`);
  if (clips > 0 || peak > 0.999) {
    bad('电平', '削波了。先重洗配音：python3 build/prep_audio.py .，再重跑字幕与导出');
  } else if (dbfs > -0.5) {
    bad('电平', '峰值贴到 0dBFS，编码后必然过冲。重洗配音到 -3dBFS');
  } else {
    ok('电平', `${dbfs.toFixed(1)} dBFS，余量够`);
  }
}

/* ---------- 6) 连续性 ---------- */
if (mono) {
  const win = Math.round(sr * 0.01);
  const rms = [];
  for (let i = 0; i + win <= mono.length; i += win) {
    let s = 0;
    for (let k = 0; k < win; k++) s += mono[i + k] ** 2;
    rms.push(Math.sqrt(s / win));
  }
  let holes = 0;
  for (let k = 2; k < rms.length - 2; k++) {
    const nb = Math.min(rms[k - 1], rms[k + 1], rms[k - 2], rms[k + 2]);
    if (nb > 0.05 && rms[k] < nb * 0.15) holes++;
  }
  if (holes > 5) bad('连续性', `语音中出现 ${holes} 处 10ms 掉块（包被叠放或截断）`);
  else ok('连续性', `掉块 ${holes} 处`);
}

/* ---------- 7) 与源配音对起点（可选） ---------- */
if (refPath && mono) {
  const wav = fs.readFileSync(refPath);
  let p = 12;
  let dataOff = 0;
  let dataLen = 0;
  while (p + 8 <= wav.length) {
    const id = wav.toString('ascii', p, p + 4);
    const sz = wav.readUInt32LE(p + 4);
    if (id === 'data') {
      dataOff = p + 8;
      dataLen = sz;
      break;
    }
    p += 8 + sz + (sz & 1);
  }
  const refSr = wav.readUInt32LE(24);
  const ch = wav.readUInt16LE(22);
  const frames = Math.floor(dataLen / (2 * ch));
  const step = Math.round(refSr * 0.01);
  const envR = [];
  for (let i = 0; i + step <= frames; i += step) {
    let s = 0;
    for (let k = 0; k < step; k++) {
      const v = wav.readInt16LE(dataOff + ((i + k) * ch) * 2) / 32768;
      s += v * v;
    }
    envR.push(Math.sqrt(s / step));
  }
  const win = Math.round(sr * 0.01);
  const envM = [];
  for (let i = 0; i + win <= mono.length; i += win) {
    let s = 0;
    for (let k = 0; k < win; k++) s += mono[i + k] ** 2;
    envM.push(Math.sqrt(s / win));
  }
  // ref 通常只是一幕（比成片短），所以拿短的那个去长的上面滑
  const [short, long] = envR.length <= envM.length ? [envR, envM] : [envM, envR];
  const swapped = envR.length > envM.length;
  let best = -2;
  let bestOff = 0;
  for (let off = 0; off + short.length <= long.length; off++) {
    let num = 0;
    let da = 0;
    let db = 0;
    for (let k = 0; k < short.length; k++) {
      num += long[off + k] * short[k];
      da += long[off + k] ** 2;
      db += short[k] ** 2;
    }
    const sc = num / (Math.sqrt(da * db) + 1e-12);
    if (sc > best) {
      best = sc;
      bestOff = off;
    }
  }
  if (best === -2) {
    bad('对位', `参考音频太短（${(envR.length / 100).toFixed(2)}s），无法比对`);
  } else if (best > 0.85) {
    const where = swapped ? '参考音频更长的位置' : '起点';
    ok('对位', `与 ${path.basename(refPath)} 相关度 ${best.toFixed(3)}，${where} ${(bestOff / 100).toFixed(2)}s`);
  } else {
    bad('对位', `相关度只有 ${best.toFixed(3)}，这条音轨不是这段配音，或时间轴被改过`);
  }
}

/* ---------- 结论 ---------- */
console.log('');
if (fails.length === 0) {
  console.log('结论：音频侧全部通过。若仍听到杂音，是播放器解码问题 → 交付 AAC 版。');
  process.exit(0);
}
console.log(`结论：${fails.length} 项不通过 —— ${fails.join('、')}`);
process.exit(1);
