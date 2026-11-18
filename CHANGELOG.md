# 变更记录

版本号规则：改数据契约或调用方式 → major；新增能力 → minor；修问题、改文档 → patch。

## [1.2.7] - 2026-11-18

### 修复

- `clean_project.py --level 3` 改成「先复制 → 校验 md5 → 再删工程」。原来是 `move` 后 `rmtree`，
  一旦删工程被权限拦住，就变成「成片已挪走、工程还在、out/ 为空」，重跑会被判定
  "out/ 里没有成片"而彻底卡死（本轮真实踩到）。
- 归档判重从"尺寸相同"升级为"尺寸 + md5 相同"：同名同尺寸但内容不同时覆盖归档，
  不再误判为已归档。
- 删工程失败时给出明确提示（成片在哪、重跑不会重复复制），不再抛裸异常。

## [1.2.6] - 2026-11-18

### 新增

- **硬规则 15「用户一确认成品就只留成品」**：用户确认可用 + 明确说本次制作结束，
  这两个条件都满足后不再追问，直接跑定稿清理，磁盘上只留一个成片文件。
- SKILL.md 第 7 步新增「定稿清理：用户一说"可以了"就直接做」，写清触发条件、
  两条不该做的判断（"留着以后改"不算理由、同一支片子只留一份）。
- 验收清单第 12 项：定稿后磁盘上只剩一个成片。

### 改动

- `clean_project.py`：
  - 成片排序改为「`-aac` 优先，其次 mtime」。原来只按 mtime，重录一次就会让 Opus 录制版
    变成"最新"，进而把 AAC 交付版当旧版删掉，最后留下一个有杂音的文件。
  - `--level 3` 的 `--archive` 可省略，默认 `~/Movies/白板视频`；执行前打印实际目标路径。
  - L3 会提示选中的是不是交付版（非 AAC 会标出来）。
- `references/pitfalls.md` 新增第 23 条（清理删错版本）。

## [1.2.5] - 2026-11-18

### 新增

- `assets/pipeline/probe_audio.mjs`：成片音频体检，退出码即结论。一次给出五组判据 ——
  编码（Opus / AAC）、容器时长与包内累计是否一致、解码成功率、峰值与削波样本数、
  语音中的 10ms 掉块数；加 `--ref audio/01.wav` 还能把音轨与源配音做包络互相关，
  输出相关度与起点秒数。可直接当交付闸门（非 0 不交）。
- SKILL.md 第 13、14 条硬规则：交付版必须是 AAC；声音问题先取证再改。
- SKILL.md 出片一节新增「声音不对时按这个顺序查」四步表（编码 → 时长 → 电平 → 连续性）。
- `references/pitfalls.md` 新增「成片声音」5 条（S1–S5），把本轮四个不同病因拆开写。

### 修正

- `references/pipeline.md`：更正 MediaRecorder 的编码事实 —— Chrome 不会写 AAC，
  `isTypeSupported('video/mp4;codecs=...,mp4a.40.2')` 返回 true 但录出来仍是 Opus；
  `/upload` 现在是三件事（落盘 → 重排时间轴 → 回填 mvhd）；补「交付两道处理」脚本职责表。
- 依赖体积注释 1.2MB → 13MB（v1.2.4 引入 AAC 链路后已过时）。

### 复盘：同一个"电流声"其实是四个病

| 现象 | 真因 | 有效动作 |
|---|---|---|
| 满篇爆点、波形顶格 | TTS 0dBFS + 编码过冲 | 重洗配音到 -3dBFS |
| 越往后越糊、像两层声音 | Opus 包 duration 记成到货间隔，整轨短 5% | `retime_mp4.py` |
| 音画不同步、部分播放器只放一帧 | `tkhd.duration`=0、视频轨 mdhd 只有 0.04s | 同上（已覆盖） |
| 全程沙沙底噪，Chrome 里听不出 | 播放器解 Opus-in-MP4 | `to_aac.mjs` 换编码 |

教训：前三次都是"听起来像"就直接改，各错一次。正确的第一步是把成片音轨解成 wav 单独播，
以及跑 `probe_audio` 拿 FAIL 项——本轮最后正是靠"wav 干净 / mp4 脏"一步定死是解码器问题。

## [1.2.4] - 2026-11-18

### 新增

- `assets/pipeline/to_aac.mjs`：把成片从「MP4 + Opus」转成「MP4 + AAC」。视频轨原样拷贝，
  只重编音轨（AAC-LC 192k / 48k），2.8 秒转完 50 秒的片子。
  链路全 wasm，不依赖系统 ffmpeg：mp4box 拆包 → opus-decoder 解码 → mediabunny 复制视频轨
  并重新封装 → `@mediabunny/aac-encoder`（ffmpeg 的 AAC 编码器）编码。
- 依赖随技能自带（mediabunny / @mediabunny/aac-encoder / mp4box / opus-decoder，约 12MB，
  已剔除 src、bundles、sourcemap）。
- 交付规则：不认 Opus-in-MP4 的播放器（Windows 媒体播放器、手机自带、微信）一律交 `-aac.mp4`。

### 背景

- 排查结论：成片音轨逐段与源配音互相关 0.98~0.99、峰值 -1 dB、无削波无掉块、8k 以上 -85 dB，
  内容没问题；容器时长也已修好。剩下的"沙沙"是播放器解 Opus 时产生的，只能换编码。
- 鸿蒙上 ffmpeg 二进制（4.1.4 / 7.0.2 静态 arm64，签名后）一律 SIGSEGV，AAC 只能走 wasm。

## [1.2.3] - 2026-11-18

### 修复

- `retime_mp4.py` 之前只改音轨，容器里两个更硬的错误没动：两条轨的 `tkhd.duration`
  都是 **0**，视频轨 `mdhd.duration` 只有 **1192 ticks / 0.04 秒**（实际 1238 帧、49.6 秒）。
  播放器拿视频时长去对音轨时只能硬拉伸，听感就是持续的"沙沙"声。
  现在按 trun 累加出每轨真实时长，回写 mdhd / tkhd / mvhd；视频 sample 表仍不动。
- 新增 `--check`，只体检不写盘。

### 新增

- 页面加「导出 WebM」：MP4 里 Chrome 只会塞 Opus，遇到不认 Opus-in-MP4 的播放器
  可以导 WebM（VP9+Opus 原生复用）交叉验证。

## [1.2.2] - 2026-11-18

### 修复

- 电流声的真正原因：Chrome 的 MP4 复用器把 Opus 轨的 sample duration 记成了
  "两个包到货的墙钟间隔"（21ms / 37.5ms 乱跳），整条音轨 47.8s，而包内实际是
  837×60ms = 50.22s。按时间戳播放的播放器把相邻包叠在一起，就是满篇电流声；
  Chrome 自己顺序解码，所以本机预览听不出来。
- 新增 `assets/pipeline/retime_mp4.py`：按 Opus TOC 算真实帧长，重写每片 trun 的
  duration、tfdt、mdhd/mvhd，视频轨不动；幂等，可重复跑。
- `assets/player/serve.py`：`/upload` 收到 mp4 后先重排音轨再回填总时长。
- SKILL.md 出片一节补「音轨时长表要重排」。

### 数据

- 重排前：837 包 / 容器 2296161 ticks（47.8s）/ 实解码 50.22s。
- 重排后：837 包全部 2880 ticks，tfdt 严格单调，总长 50.22s。

## [1.2.1] - 2026-11-17

### 修复

- 导出成片有"电流声 / 滋滋声"。定位到配音电平：TTS 输出峰值顶在 0dBFS，
  源文件本身已有削波样本，Opus 重建波形再冲 0.65dB（实测峰值 1.073），
  播放器只能硬削 → 满篇爆音。
- 新增 `assets/pipeline/prep_audio.py`：Kaiser 窗多相重采样到 48k（8k 以上 -86dB）、
  35Hz 高通去直流、峰值统一 -3dBFS、首尾 8ms 淡入淡出，并回写 `audio/manifest.json` 时长。
- `assets/player/index.html`：AudioContext 显式 48k（配音已是 48k，不再二次重采样）；
  导出链加 `DynamicsCompressor` 限幅（-3dB / 20:1）兜底；Opus 码率 128k → 160k。
- SKILL.md 增加第 12 条硬规则「配音必须留 3dB 余量」，配音一节补洗音频步骤。

### 数据

- 洗前：`nat-full.mp4` 解码峰值 1.073、>0.999 的样本 233 个；
  洗后源文件峰值 -3.0dB，留足编码过冲空间。

## [1.2.0] - 2026-11-17

### 变更

- `assets/pipeline/align2.py`：字幕改为**按逗号级小句打包**，不再按字数等分。
  以前的切法会把「…都用」和「192.168.1.10，」切开，后者只剩 1.3 秒，像闪了一下。
  现在短句合并到 22 宽、短于 8 宽的条并到邻居，长度自然落在 10–22 字。
- `assets/pipeline/textwrap_cjk.py`：折行不劈数字/字母串（`192.168.1.10`、`DHCP`），
  避免出现 `.10，` 这种以标点开头的行。
- `references/params.md`：字幕排版一节补「分条单位」「数字不可切」和三条阈值。

### 数据

- 四幕实测：36/83/65/65 字的稿子，切出 13 条，标点断行全合规，
  起点偏差中位 0ms、90 分位 548ms。

## [1.1.1] - 2026-11-17

### 修复

- `assets/player/index.html`：`play()` 不再要求字幕已对齐。原文 `if (!S.cues) return;`
  会让「**配音已生成、字幕还没对齐**」这个中间状态完全播不动——点播放没反应、没声音、画面不动。
  现改为只要求图已加载。
- `assets/player/index.html`：绘制时长改为 `recomputeDrawMs()`，在路径加载后与音频
  `loadedmetadata` 各算一次。此前在音频元数据到达前用 `totalMs()` 会算出 0 秒，
  导致进度恒为 1、整张图一次性直出，看着像静态画面。
- `assets/player/index.html`：门上的总时长在 `playlist.endMs` 未回填时，
  改问 `/audio_status` 累加各幕配音时长。

### 文档

- `references/pitfalls.md` 新增「预览台」小节（P1 播不动、P2 一上来就整图）。

## [1.1.0] - 2026-11-04

### 新增

- `assets/分镜表模板.md`：拆幕用的五列表（幕 / 对应段落 / 时长 / 画面主线 / 元素绘制顺序），
  含填写规则、切幕原则与自检清单。SKILL 第 0 步已引用。
- `scripts/preflight.py`：提交前自检，扫手机号、密钥、令牌、私钥、本机绝对路径、>1 MB 文件与 Python 语法。
- `tests/smoke.py`：拿真实插画跑路径生成，断言吻合率不低于阈值，用于改算法后回归。

### 变更

- `assets/player/serve.py`：下载目录不再写死某台机器的绝对路径，
  改为 `WB_DOWNLOAD_DIR` 环境变量 + `~/Download`、`~/Downloads` 探测。
- `.gitignore`：补音频、环境文件、密钥类规则；显式保护 `node_modules/` 不被通配波及。

### 文档

- README 增加「维护规范」：分支、提交、版本、敏感信息、协作边界。

## [1.0.1] - 2026-11-04

### 修复

- `new_project.py` 末尾仍提示 `npm i`，与「依赖随工程自带」不符；改为打印真实脚本绝对路径。

## [1.0.0] - 2026-11-04

### 首个版本

- `scripts/`：素材解析、建工程、笔迹路径（含 IoU 自检）、离线截帧、出片体检、分级清理。
- `assets/player/`：参数化预览台，三栏布局、配音页、内录导出、fMP4 时长回填。
- `assets/pipeline/`：ASR 锚点 → 时间轴 → 对齐 → SRT → 同步体检，含 Edge TTS 兜底。
- `references/`：参数基线、22 条返工踩坑、数据契约。
- 依赖 jpeg-js / pngjs / mpg123-decoder 随仓库提交，免安装。
