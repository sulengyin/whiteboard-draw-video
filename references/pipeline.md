# 数据契约与完整链路

## 目录约定

```
项目/
├── project.json          画幅 / 笔宽 / 导出参数
├── playlist.json         scenes[{id,title,image,startMs,endMs}]
├── index.html            预览台（读上面两个文件）
├── voice.html            配音页（DubbingX）
├── serve.py              预览 + /upload + /fetch_audio
├── mp3_to_wav.js         DubbingX 给 mp3 时转 wav
├── scenes/               插画（文件名 = 幕 id）
├── paths/<id>.json       笔迹路径
├── preview/              离线截帧、顺序预览图
├── audio/<id>.wav        配音（16k / 16bit / mono）
├── cues/<id>.json        幕内字幕（页面与 SRT 共用这一份）
├── srt/tts/<id>.txt      旁白原稿（出字幕）
├── srt/tts/spoken/<id>.txt  发音稿（喂 TTS）
├── srt/*.srt             成片字幕
├── build/                对齐与体检脚本（new_project 从 assets/pipeline 拷入）
└── out/                  导出落盘
```

`id` 用 `01`、`scene-01` 都行，只要三处一致：图片名、`paths/<id>.json`、`audio/<id>.wav`。

## 数据格式

### paths/<id>.json

```json
{
  "w": 1024, "h": 1536, "len": 320000, "paper": [246, 236, 216],
  "regions": [
    {
      "cx": 120, "cy": 340,
      "t0": 0.0, "t1": 0.12,
      "ring":  [x1, y1, x2, y2, ...],
      "outer": [[x,y,t,r], ...],
      "rings": [[...], [...]],
      "len": 1840
    }
  ]
}
```

- `ring` 是最外圈，笔走这条；`rings` 含内孔，用于 `evenodd` 填充。
- `t0/t1` 归一化到整幕，`t1` 时刻整块显影。
- 相邻 region 的 `t0` 可以不等于上一个 `t1`（差值就是抬笔飞行时间）。

### cues/<id>.json

```json
{
  "scene": 1,
  "durationMs": 36180,
  "cues": [
    { "startMs": 210, "endMs": 3950, "text": "…", "lines": ["…"], "seg": "01" }
  ]
}
```

`lines` 是排好版的结果，页面直接画，不再二次换行 —— 保证预览与烧进成片的字幕一致。

## 配音

### 发音稿改写

只改 `spoken/`，字幕永远用原文：

| 原文 | 发音稿 |
|---|---|
| `192.168.3.158` | `一九二点一六八点三点一五八` |
| `2001:0db8::0001` | `二零零一冒号零 d b 八冒号冒号零零零一` |
| `IPv4` | `IPV4`（避免读成 "ip 威 four"） |
| `24` | `二十四` |
| `%` | `百分之` |

### DubbingX 接口（voice.html 已封装）

```
POST /sendSmsCode            {phone}
POST /smsLogin               {phone,code}         → data.token
POST /voice/searchVoiceTimbreListV2  {keyword,...} → list
POST /voice/voiceTtsTask/batchAddVoiceTtsTask     → taskId
POST /voice/voiceTtsTask/getVoiceTtsTaskInfo/{id} → status / convertUrl
```

`base = https://api.dubbingx.com`，请求头 `token` + `X-Locale: zh_CN`。
免费音色「睿泽」起步参数：语速 0.92、语调 -4、情绪 `常规-日常说话-3`。

服务端 `/fetch_audio` 负责：下载 → 非 wav 走 `mp3_to_wav.js` → `normalize_wav` 统一 16k/16bit/mono → 备份旧音频 → 更新 `audio/manifest.json`。

## 字幕对齐

核心问题：TTS 会在长句中间自己断句（尤其带数字、端口时），单靠"字数 ÷ 语速"必然累积漂移。

### 三步

1. **能量包络**：25ms 窗 / 10ms 跳，算 RMS；阈值 = 峰值 −32dB，带滞回。静音 ≥120ms 切出语音块，块起点 −60ms 作为候选锚点。
2. **ASR 锚点**：逐幕听写，把每段开口时间 +0.2s（补 ASR 提前量）写成锚点；先按锚点把稿子切块，误差就不会跨块累积。
3. **块内 DP**：把每句起点吸到停顿上，代价 =

   ```
   cost = w1 * (候选时刻 − 期望时刻)² + w2 * (本句语速 − 全幕平均语速)²
   ```

   只有时长项会整体漂移，只有位置项会一句吞两句；两项都要。

### 换音频后的固定顺序

```
ASR 听写 → asr_to_anchors.py → retime2.py → align2.py → make_srt.py → audit_sync.py
```

`retime2.py` 会顺手调一次 `make_srt`，但那时 cue 还是旧的，必须再跑一遍 `align2 && make_srt`。
不重做锚点的退路是清空锚点、退回纯停顿对齐，个别长句会差半句。

### 断行

- 一行能放下（≤25 全角）就不拆。
- 必须拆时在标点处对半开，不要按固定字数硬切。
- 避头尾：行首不能是 `，。、；：？！）】》`，行尾不能是 `（【《「『`。
- 合并 <8 字的孤行，避免"某个窗 / 口。"这种劈词。

## 导出

```
canvas.captureStream(fps)
  + AudioContext.createMediaElementSource(audio) → MediaStreamDestination
  → MediaRecorder(mp4;avc1+aac → 退 webm)
  → Blob → POST /upload
```

`/upload` 做两件事：写 `out/`（同名加序号）→ 修 fMP4 时长。
`project.json` 的 `copyToDownload` 为 `true` 时才额外复制到 `~/Download`（默认关，避免双倍占用）。
页面提示必须给 `out/` 的绝对路径。

导出前检查：

- `ac.resume()` 已 await，`au.currentTime` 在走（否则录出静音）。
- 画布边长偶数。
- 录制期间不切标签页。

## 环境限制（鸿蒙）

- 无 ffmpeg / numpy / PIL；不能做离线逐帧渲染，只能浏览器实时内录。
- 静态 ffmpeg 二进制签名后 segfault，别再试。
- Python 缺 CA 根证书，HTTPS 需跳过校验（脚本已内置）。
- `audioop` 在 3.13 被移除；若报错改用 `wave` + 手写降采样，或装 `audioop-lts`。
