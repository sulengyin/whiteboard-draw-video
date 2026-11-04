---
name: whiteboard-draw-video
display_name_zh: 白板手绘视频
display_name_en: Whiteboard Draw Video
description: 把文案与插画做成「一支笔沿图案轮廓慢慢画出来」的分幕白板讲解视频，含真人感配音、与旁白逐字一致的字幕、浏览器内录导出成片。当用户要给文档/课件/长文配手绘讲解片，或提到「白板动画」「笔把图画出来」「边画边讲」「讲解视频」时使用。
agent_created: true
---

# 白板手绘讲解视频

## 这个技能交付什么

一条可预览、可导出、可复查的生产线：

```
文案分幕 → 配图 → 抠墨 + 轮廓追踪 → 预览台 → 配音 → 字幕对齐 → 内录成片
```

最终产物是一个浏览器工程（`index.html` + `serve.py`）和一支 mp4。不是设计稿，不是静态图，也不是"图上盖一层遮罩慢慢擦出来"。

素材收口：`assets/素材模板.md`（项目信息 + 逐幕的图片 / 标题 / 旁白 / 发音修正）。用户填这份表，比让他散着发文档和图片省一轮返工。

## 不可妥协的画面规则

违反任意一条都要返工，用户会直接看出来。

1. **笔到哪里，画面才到哪里**。未画到处必须是干净纸色，不能先露出整张图或方框。做法：把纸底抠成 alpha=0，再与笔刷掩膜相乘。
2. **笔沿图形自身的轮廓走**，不是整页蛇形扫、不是骨架、不是等高线内缩。笔尖与显影边缘误差 < 2px。
3. **每个封闭图形只描最外一圈**；描到闭环的瞬间整块填充（`Path2D` + `evenodd`，含内孔），不要逐点渗色，也不要等幕末跳出。
4. **顺序：整体从上到下，行内从左到右**。先按 `cy` 聚成行（容差约 0.7 倍行高），行内按 `cx` 升序；抬笔跳转的时间成本打 0.45 折，保持笔速观感恒定。
5. **整幅图永远完整**：等比缩到画布的 96% 留纸边，只叠加 ≤3.5% 的呼吸推近；任何时刻不裁掉下沿或文字。
6. **0 秒必须是一张完整画面**，不能是白纸；切幕、暂停、拖进度都不许闪白。
7. **三栏在 100% 缩放下同时可见**：左幕列表 / 中画布 / 底按钮条。用 `flex + 100dvh + flex:0 0 auto`，不要用 grid 硬撑。
8. **字幕就是旁白原文**，逐字一致，不许写摘要；时间必须从音频取（能量包络 + ASR 锚点），不许按字数估算。
9. **导出要能定位**：录完回传本地写 `out/`，页面显示绝对路径且允许换行（不截断）。默认不再往 Download 复制第二份。
10. **fragmented MP4 的时长要回填**，否则播放器显示 0:00、进度条拖不动。
11. **交付后必须清理**：跑一次 `clean_project.py --level 1`，工程不能长期攒在磁盘上（一支片 250 MB+，其中九成可重算）。

## 标准流程

### 0. 收素材 → 建工程

素材收集一律用 `assets/素材模板.md`（空表，直接发给用户填）。拿到填好的 md 后：

```bash
python3 <skill>/scripts/parse_brief.py --brief 素材.md --out ~/videos/xxx
python3 <skill>/scripts/new_project.py --out ~/videos/xxx --title "标题" --images ./pics
cd ~/videos/xxx
# 依赖已随工程自带（jpeg-js / pngjs / mpg123-decoder，约 1.2MB），不用 npm i
```

`parse_brief.py` 产出 `brief.json` 与 `srt/tts/NN.txt`（逐字原稿）、`srt/tts/spoken/NN.txt`（套用过「发音修正」），并检查幕号连续、图片存在、旁白非空——有问题会直接列出哪一幕哪一项。

用户只给了 docx + 图片、没填模板时，自己按模板结构拆幕，再走同一条链路；不要跳步直接写 txt，容易丢字。

拆幕要落成 `assets/分镜表模板.md` 那张五列表（幕 / 对应段落 / 时长 / 画面主线 / 元素绘制顺序）。它同时是画图需求单和笔的顺序来源，没有它后面很难对齐——尤其是「元素绘制顺序」，写完就不用再解释一遍想画什么。

`new_project.py` 生成 `project.json`（画幅/笔宽/导出参数）、`playlist.json`（幕 id + 图片）、预览台与手素材。
画幅从第一张图自动读；竖版图建议底部留 220px 字幕条（`captionHeight`）。

### 1. 生成笔迹路径

```bash
node <skill>/scripts/trace_paths.js --in scenes --out paths
```

每张图输出 `paths/<id>.json`，并打印「吻合率」。**吻合率必须 ≥99%**，掉到 95% 以下说明轮廓追踪出错（通常是对角粘连），按提示调 `--th/--min/--paper`。
同时输出 `paths/preview/<id>-path.png`：蓝 → 红 = 先 → 后，用来肉眼确认顺序。

### 2. 离线截帧自检

```bash
node <skill>/scripts/render_frames.js --dir . --at 0.3,0.6,0.95
```

看三件事：笔尖是否贴着显影边缘、图形内部有没有空洞、顺序是不是从上到下从左到右。
没有浏览器截图能力时，这是唯一可靠的自检手段。

### 3. 起预览台

```bash
python3 serve.py 8010     # 必须用它，普通 http.server 没有 /upload
```

打开 `http://127.0.0.1:8010/index.html`。先点「截一帧看版式」，再导 30 秒样片给用户确认，确认后才录全片。

### 4. 配音

`srt/tts/<id>.txt` 存原稿（出字幕用），`srt/tts/spoken/<id>.txt` 存发音稿（喂 TTS）：

- 数字、IP、端口、版本号写成汉字读法（`192.168.3.158` → `一九二点一六八点三点一五八`），避免 TTS 读错。
- 打开 `voice.html`：短信登录 → 搜音色（默认「睿泽」）→ 语速 0.92 / 语调 -4 → 逐幕生成。
  页面把音频地址交给 `/fetch_audio`，服务端落盘并统一成 **16k / 16bit / 单声道**（对齐脚本按单声道读，立体声会错位）。
- 换音色、改语速、改文案，都必须重做第 5 步的锚点。
- 没有 DubbingX 账号时的兜底：`npm i msedge-tts mpg123-decoder`，然后
  `NODE_TLS_REJECT_UNAUTHORIZED=0 node build/tts_edge.js --voice zh-CN-YunyangNeural`。
  机器人感比 DubbingX 明显，只作退路。

### 5. 字幕对齐

顺序不能颠倒：

```bash
# 逐幕做 ASR 听写，结果存 build/asr/<id>.txt
python3 build/asr_to_anchors.py     # ASR 分段起点 → 锚点（+0.2s 补提前量）
python3 build/retime2.py            # 按真实音频排幕级 startMs/endMs
python3 build/align2.py             # 锚点切块 + 块内 DP 选停顿 → cues/<id>.json
python3 build/make_srt.py           # → srt/<slug>.srt
python3 build/audit_sync.py         # 体检
```

这套脚本由 `new_project.py` 从 `assets/pipeline/` 拷进 `build/`，不要手改工程里的副本（改了下次建工程会被覆盖）。
幕 id 全部取自 `playlist.json`，所以 `01`、`scene-01` 都能跑；文本从 `srt/tts/<id>.txt` 读，音频从 `audio/<id>.wav` 读。
换配音、改发音稿、改旁白，都必须从 `asr_to_anchors.py` 重跑一遍，不能只跑 `align2`。

`align2.py` 的代价同时约束「位置偏差」和「语速偏差」；只用时长约束会整体漂移。
详细链路与脚本职责见 `references/pipeline.md`。

### 6. 出片

```bash
python3 <skill>/scripts/check_project.py --root .
```

全过后再录。录制要求：

- 按真实时间内录，期间不能切走标签页。
- 导出画布边长取偶数（H.264 会丢奇数行）。
- 结束提示里必须给绝对路径（`out/`），并附「再下载一份」。

### 7. 收尾（避免工程长期占地方）

一支 5 分钟的片子，工程会长到 250–300 MB，真正该留的只有 `out/*.mp4`。删之前先分清三类：

| 类别 | 内容 | 重建代价 |
|---|---|---|
| 可重算 | `preview/`、`paths/preview/`、`paths/*.json` | 几秒到几十秒 |
| 需重做 | `audio/*.wav`、`cues/`、`srt/*.srt` | 重新登录配音页 + 重跑对齐链路 |
| 唯一产物 | `out/*.mp4` | 不可重建，必须先归档 |

```bash
python3 <skill>/scripts/clean_project.py --root .                      # 只看占用明细
python3 <skill>/scripts/clean_project.py --root . --level 1 --dry-run  # 看会删什么
python3 <skill>/scripts/clean_project.py --root . --level 1            # 每次交付后跑
python3 <skill>/scripts/clean_project.py --root . --level 3 --archive ~/Movies/白板视频   # 定稿后
```

- **L1 是交付后的默认动作**，能省 60% 以上（预览帧 + 旧版成片 + 旧 TTS 备份 + 早期 ffmpeg 残留），清完仍可直接重录。
- **L2** 在定稿不再改时用；之后要改配音就得重走第 4–5 步。
- **L3** 把最新成片移到 `--archive` 再删整个工程，只剩视频本身。
- `serve.py` 默认**不**往 Download 再复制一份（否则每支片子白占一倍）；要复制的把 `project.json` 的 `copyToDownload` 设成 `true`。
- 成片体积由码率决定（默认 2600 kbps，5 分钟约 80 MB），压到 2000 以下字幕边缘会糊，不建议。

## 验收清单

| # | 检查 | 通过标准 |
|---|---|---|
| 1 | 轮廓反填 | IoU ≥ 99% |
| 2 | 笔迹覆盖 | ≥ 97% 墨迹像素 |
| 3 | 顺序 | 跨行次数 ≤ 图形数；行内 cx 单调 |
| 4 | 0 秒静帧 | 非纯色，图完整 |
| 5 | 整图完整 | 任意帧四角可采到纸色 |
| 6 | 三栏布局 | 100% 缩放截图里底部按钮完整可见 |
| 7 | 字幕文本 | 拼接后与原稿逐字一致 |
| 8 | 字幕时间 | 偏差中位 ≤50ms、90 分位 ≤300ms |
| 9 | 避头尾 | 行首无 `，。、；：？！）】》`；无孤字行 |
| 10 | 导出 | 两处文件 md5 一致；时长非 0:00 |

`check_project.py` 覆盖 1、3、7、9 与文件完整性；2、4、5、6、8、10 需要截帧或人工看一眼。

## 参数与踩坑

- 参数基线、调参顺序：`references/params.md`
- 22 条返工原因与对应修法：`references/pitfalls.md`
- 完整命令链路、脚本职责、对齐算法：`references/pipeline.md`

## 修改这个技能自身

改完脚本或文档、准备提交时：

```bash
python3 scripts/preflight.py                          # 敏感信息 / 本机路径 / 大文件 / 语法
python3 tests/smoke.py --images ~/videos/xxx/scenes   # 动过 trace_paths 或参数才需要
```

`preflight` 返回 1 就不要提交。分支、提交格式、版本号、协作边界见 `README.md` 的「维护规范」，`CHANGELOG.md` 每个 tag 补一段。

## 不要做的事

- 不要用「静态图 + 淡入 / 遮罩刷出」冒充手绘。
- 不要用纯骨架、onion peel、骨架 + 大笔刷——都会出现笔在色块里乱窜、等高线圈或空洞。
- 不要按字数比例分配字幕时间。
- 不要用 `text-overflow:ellipsis` 显示文件路径。
- 不要在鸿蒙上折腾 ffmpeg 静态二进制（签名后 segfault），直接走浏览器内录。
