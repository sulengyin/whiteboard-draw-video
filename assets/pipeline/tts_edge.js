#!/usr/bin/env node
/**
 * Edge TTS 配音：读 srt/tts/spoken/<id>.txt，输出 audio/<id>.wav（24k/16bit/单声道）
 *
 *   npm i msedge-tts mpg123-decoder
 *   NODE_TLS_REJECT_UNAUTHORIZED=0 node build/tts_edge.js [--voice zh-CN-YunyangNeural] [--rate +0%] [--only 01,02]
 *
 * 说明：本机没有 pip / ffmpeg，所以用 msedge-tts 拿 mp3，再用 mpg123-decoder
 * 解成 PCM 自己封 WAV —— align2.py 读包络需要 PCM。
 */
const fs = require('fs');
const path = require('path');
const { MsEdgeTTS, OUTPUT_FORMAT } = require('msedge-tts');
const { MPEGDecoder } = require('mpg123-decoder');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'srt', 'tts', 'spoken');
const OUT = path.join(ROOT, 'audio');

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf('--' + k); return i >= 0 ? argv[i + 1] : d; };
const VOICE = arg('voice', 'zh-CN-YunyangNeural');
const RATE = arg('rate', '+0%');
const PITCH = arg('pitch', '+0Hz');
const ONLY = arg('only', '');

function wavHeader(nSamples, sampleRate = 24000, channels = 1) {
  const b = Buffer.alloc(44);
  const dataLen = nSamples * 2 * channels;
  b.write('RIFF', 0); b.writeUInt32LE(36 + dataLen, 4); b.write('WAVE', 8);
  b.write('fmt ', 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20);
  b.writeUInt16LE(channels, 22); b.writeUInt32LE(sampleRate, 24);
  b.writeUInt32LE(sampleRate * 2 * channels, 28); b.writeUInt16LE(2 * channels, 32);
  b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(dataLen, 40);
  return b;
}

async function synth(text, tts) {
  const { audioStream } = tts.toStream(text, { rate: RATE, pitch: PITCH, volume: '+0%' });
  const chunks = [];
  for await (const c of audioStream) chunks.push(c);
  const mp3 = Buffer.concat(chunks);
  if (!mp3.length) throw new Error('合成返回空数据');
  const dec = new MPEGDecoder();
  await dec.ready;
  const { channelData, samplesDecoded, sampleRate } = dec.decode(mp3);
  dec.free();
  const pcm = Buffer.alloc(samplesDecoded * 2);
  const ch = channelData[0];
  for (let i = 0; i < samplesDecoded; i++) {
    const v = Math.max(-1, Math.min(1, ch[i] || 0));
    pcm.writeInt16LE(Math.round(v * 32767), i * 2);
  }
  return { wav: Buffer.concat([wavHeader(samplesDecoded, sampleRate), pcm]), durMs: samplesDecoded / sampleRate * 1000 };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const files = fs.readdirSync(SRC).filter(f => /\.txt$/i.test(f)).sort()
    .filter(f => !ONLY || ONLY.split(',').includes(f.replace(/\.txt$/i, '')));
  if (!files.length) throw new Error('没有找到配音稿：' + SRC);

  const tts = new MsEdgeTTS();
  await tts.setMetadata(VOICE, OUTPUT_FORMAT.AUDIO_24KHZ_48KBITRATE_MONO_MP3);

  const manifestPath = path.join(OUT, 'manifest.json');
  const manifest = fs.existsSync(manifestPath) ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')) : {};
  for (const f of files) {
    const text = fs.readFileSync(path.join(SRC, f), 'utf8').trim();
    let got = null;
    for (let attempt = 1; attempt <= 3 && !got; attempt++) {
      try { got = await synth(text, tts); }
      catch (e) { if (attempt === 3) throw e; await new Promise(r => setTimeout(r, 1500)); }
    }
    const stem = f.replace(/\.txt$/, '');
    fs.writeFileSync(path.join(OUT, stem + '.wav'), got.wav);
    manifest[stem] = { file: stem + '.wav', durMs: Math.round(got.durMs), chars: text.length, voice: VOICE };
    console.log(`  ${stem}  ${(got.durMs / 1000).toFixed(2)}s  ${text.length} 字`);
  }
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf8');
  const total = Object.values(manifest).reduce((a, v) => a + v.durMs, 0) / 1000;
  console.log(`合计 ${total.toFixed(1)}s → ${OUT}`);
})().catch(e => { console.error('失败：', e.message); process.exit(1); });
