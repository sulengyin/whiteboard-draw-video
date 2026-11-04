#!/usr/bin/env node
/** mp3 → wav（没有 ffmpeg 时用 mpg123-decoder 解 PCM 自己封头）
 *  用法：node mp3_to_wav.js in.mp3 out.wav
 *  依赖：npm i mpg123-decoder
 */
const fs = require('fs');
let MPEGDecoder;
try { ({ MPEGDecoder } = require('mpg123-decoder')); }
catch (e) { console.error('缺 mpg123-decoder：npm i mpg123-decoder'); process.exit(1); }

function wavHeader(nSamples, sampleRate) {
  const b = Buffer.alloc(44);
  const dataLen = nSamples * 2;
  b.write('RIFF', 0); b.writeUInt32LE(36 + dataLen, 4); b.write('WAVE', 8);
  b.write('fmt ', 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20);
  b.writeUInt16LE(1, 22); b.writeUInt32LE(sampleRate, 24);
  b.writeUInt32LE(sampleRate * 2, 28); b.writeUInt16LE(2, 32);
  b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(dataLen, 40);
  return b;
}

(async () => {
  const [, , src, dst] = process.argv;
  if (!src || !dst) throw new Error('用法：mp3_to_wav.js in.mp3 out.wav');
  const mp3 = fs.readFileSync(src);
  const dec = new MPEGDecoder();
  await dec.ready;
  const { channelData, samplesDecoded, sampleRate } = dec.decode(mp3);
  dec.free();
  if (!samplesDecoded) throw new Error('解码为空');
  const pcm = Buffer.alloc(samplesDecoded * 2);
  const ch = channelData[0];
  for (let i = 0; i < samplesDecoded; i++) {
    const v = Math.max(-1, Math.min(1, ch[i] || 0));
    pcm.writeInt16LE(Math.round(v * 32767), i * 2);
  }
  fs.writeFileSync(dst, Buffer.concat([wavHeader(samplesDecoded, sampleRate), pcm]));
  console.log(`ok ${samplesDecoded} @${sampleRate}`);
})().catch(e => { console.error(e.message); process.exit(1); });
