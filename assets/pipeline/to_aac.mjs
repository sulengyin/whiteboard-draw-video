#!/usr/bin/env node
/**
 * 把 MediaRecorder 录出来的「MP4 + Opus」转成「MP4 + AAC」。
 *
 * 为什么要转：Chrome 在 MP4 里只会写 Opus。部分播放器（Windows 媒体播放器、
 * 手机自带播放器、微信）解 Opus 时要么直接没声，要么叠包 / 重采样出一层
 * "沙沙"的底噪。AAC 是 MP4 的原生音频，哪儿都认。
 *
 * 为什么不用 ffmpeg：鸿蒙上静态 ffmpeg 二进制签名后仍然 SIGSEGV，跑不起来。
 * 这里走纯 wasm：
 *   - mp4box       拆 MP4、取 Opus 包
 *   - opus-decoder libopus 解码成 PCM
 *   - mediabunny   复制 H.264 轨 + 重新封装
 *   - @mediabunny/aac-encoder  ffmpeg 的 AAC 编码器（wasm）
 *
 * 用法：
 *   node build/to_aac.mjs out/xxx.mp4 [out/xxx-aac.mp4]
 *
 * 幂等：输入里没有 Opus 轨就直接退出，不做任何事。
 */
import fs from 'fs';
import path from 'path';
import { createFile } from 'mp4box';
import { OpusDecoder } from 'opus-decoder';
import {
  Input, ALL_FORMATS, BlobSource, Output, BufferTarget, Mp4OutputFormat,
  EncodedPacketSink, EncodedVideoPacketSource, AudioSampleSource, AudioSample
} from 'mediabunny';
import { registerAacEncoder } from '@mediabunny/aac-encoder';

// 把 ffmpeg 的 AAC 编码器（wasm）挂到 mediabunny 上；Node 没有 WebCodecs，
// 不注册这一步就会报 "AudioEncoder is not available"。
registerAacEncoder();

const AAC_BITRATE = 192000;
const SAMPLE_CHUNK = 1024;

/** 拆出 Opus 音轨的所有包 */
function demuxOpus(file) {
  return new Promise((resolve) => {
    const buf = fs.readFileSync(file);
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    ab.fileStart = 0;
    const mp4 = createFile();
    const packets = [];
    let info = null;

    mp4.onError = () => resolve({ packets, info });
    mp4.onSamples = (_id, _u, samples) => {
      for (const s of samples) packets.push(Buffer.from(s.data));
    };
    mp4.onReady = (meta) => {
      const track = meta.tracks.find(t => (t.codec || '').startsWith('Opus'));
      if (!track) { resolve({ packets: [], info: meta }); return; }
      info = track;
      mp4.setExtractionOptions(track.id, null, { nbSamples: 1e6 });
      mp4.start();
      // 解封装是同步的，给一个 tick 收尾
      setTimeout(() => resolve({ packets, info }), 300);
    };
    mp4.appendBuffer(ab);
    mp4.flush();
  });
}

/** Opus 包 → 交织的 Float32（每声道一段平面数据） */
async function decodeOpus(packets) {
  const dec = new OpusDecoder({ sampleRate: 48000, channels: 2 });
  await dec.ready;
  const planes = [[], []];
  let rate = 48000;
  for (const p of packets) {
    let r = null;
    try { r = dec.decodeFrame(p); } catch (e) { continue; }
    if (!r || !r.samplesDecoded) continue;
    rate = r.sampleRate;
    planes[0].push(r.channelData[0]);
    planes[1].push(r.channelData.length > 1 ? r.channelData[1] : r.channelData[0]);
  }
  const cat = (arr) => {
    const n = arr.reduce((s, c) => s + c.length, 0);
    const out = new Float32Array(n);
    let k = 0;
    for (const c of arr) { out.set(c, k); k += c.length; }
    return out;
  };
  return { left: cat(planes[0]), right: cat(planes[1]), rate };
}

async function main() {
  const src = process.argv[2];
  if (!src) {
    console.error('用法：node to_aac.mjs in.mp4 [out.mp4]');
    process.exit(2);
  }
  const dst = process.argv[3] ||
    path.join(path.dirname(src), path.basename(src, path.extname(src)) + '-aac.mp4');

  const { packets } = await demuxOpus(src);
  if (!packets.length) {
    console.log('没有 Opus 音轨，跳过');
    return;
  }

  const { left, right, rate } = await decodeOpus(packets);
  const frames = left.length;
  console.log(`Opus ${packets.length} 包 → ${frames} 采样 / ${rate}Hz ≈ ${(frames / rate).toFixed(2)}s`);

  const buf = fs.readFileSync(src);
  const input = new Input({ source: new BlobSource(new Blob([buf])), formats: ALL_FORMATS });
  const vtrack = await input.getPrimaryVideoTrack();
  if (!vtrack) throw new Error('没找到视频轨');

  const output = new Output({
    format: new Mp4OutputFormat({ fastStart: 'in-memory' }),
    target: new BufferTarget()
  });
  const vsrc = new EncodedVideoPacketSource(vtrack.codec);
  output.addVideoTrack(vsrc, { frameRate: 25 });
  const asrc = new AudioSampleSource({ codec: 'aac', bitrate: AAC_BITRATE });
  output.addAudioTrack(asrc);
  await output.start();

  const decCfg = await vtrack.getDecoderConfig();
  let n = 0;
  for await (const pkt of new EncodedPacketSink(vtrack).packets()) {
    await vsrc.add(pkt, n === 0 ? { decoderConfig: decCfg } : undefined);
    n++;
  }

  for (let i = 0; i < frames; i += SAMPLE_CHUNK) {
    const count = Math.min(SAMPLE_CHUNK, frames - i);
    const planar = new Float32Array(count * 2);
    planar.set(left.subarray(i, i + count), 0);
    planar.set(right.subarray(i, i + count), count);
    const s = new AudioSample({
      data: planar, format: 'f32-planar', numberOfChannels: 2,
      sampleRate: rate, timestamp: i / rate
    });
    await asrc.add(s);
    s.close();
  }

  await output.finalize();
  fs.writeFileSync(dst, Buffer.from(output.target.buffer));
  const mb = (fs.statSync(dst).size / 1048576).toFixed(1);
  console.log(`✓ ${n} 帧视频 + AAC 音轨 → ${dst}（${mb} MB）`);
}

main().catch(e => { console.error('转换失败：', e); process.exit(1); });
