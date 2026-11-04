# 白板手绘视频 / Whiteboard Draw Video

把文案 + 手绘插画做成「一支笔沿着图案轮廓慢慢画出来」的分幕讲解视频：真人感配音、与旁白逐字一致的字幕、浏览器内录导出 mp4。

这是一个 WorkBuddy Skill，本仓库就是技能目录本体。

## 安装

```bash
git clone git@github.com:sulengyin/whiteboard-draw-video.git ~/.workbuddy/skills/whiteboard-draw-video
```

已经装过、只是更新：

```bash
cd ~/.workbuddy/skills/whiteboard-draw-video && git pull
```

重启 WorkBuddy 后生效，输入 `/whiteboard-draw-video` 触发，或直接说「用白板手绘视频做一份 XXX 讲解片」。

> 依赖（jpeg-js / pngjs / mpg123-decoder）已随仓库提交，不需要 `npm i`。

## 使用

```bash
# 1. 填素材模板 → 建工程
python3 scripts/parse_brief.py --brief 素材.md --out ~/videos/xxx

# 2. 抠墨 → 轮廓 → 笔迹路径（含 IoU 自检）
node scripts/trace_paths.js --in ~/videos/xxx/scenes --out ~/videos/xxx/paths

# 3. 起预览台（校验、配音、内录导出）
python3 ~/videos/xxx/serve.py 8010

# 4. 出片前体检
python3 scripts/check_project.py --root ~/videos/xxx

# 5. 收尾清理（交付后 L1，定稿 L3 归档）
python3 scripts/clean_project.py --root ~/videos/xxx --level 1
```

素材模板见 [`assets/素材模板.md`](assets/素材模板.md)。

## 目录

```
SKILL.md                 触发词、硬规则、7 步流程、验收表
references/
  params.md              参数基线与调参顺序
  pitfalls.md            22 条返工原因（现象 → 根因 → 修法）
  pipeline.md            数据契约、配音接口、字幕对齐算法
scripts/
  parse_brief.py         素材模板 → 工程骨架 + 发音稿
  new_project.py         建工程（自带预览台、对齐脚本、依赖）
  trace_paths.js         抠墨 → 连通域 → 轮廓环 → 排序 → paths.json
  render_frames.js       离线截帧验收
  check_project.py       出片前 13 项体检
  clean_project.py       按「重建代价」分级清理
  _deps.js               依赖回落：cwd → scripts → 技能根
assets/
  hand.png               手绘笔素材
  player/                参数化预览台（index.html / serve.py / voice.html）
  pipeline/              字幕对齐链路 + Edge TTS 兜底
 素材模板.md            给用户填的表
 分镜表模板.md          拆幕用的五列表（幕/段落/时长/画面主线/绘制顺序）
node_modules/            三个运行时依赖，已提交
```

## 产物位置

视频写到工程的 `out/`，不复制到 Download（`project.json` 里 `copyToDownload` 默认 `false`）。清理走 `clean_project.py`，不会在磁盘上堆积几百 MB 中间文件。

## 许可

私有仓库，保留所有权利。
