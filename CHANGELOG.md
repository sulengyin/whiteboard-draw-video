# 变更记录

版本号规则：改数据契约或调用方式 → major；新增能力 → minor；修问题、改文档 → patch。

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
