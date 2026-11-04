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
CHANGELOG.md             每个 tag 一段
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
  preflight.py           提交前自检（敏感信息 / 本机路径 / 大文件 / 语法）
  _deps.js               依赖回落：cwd → scripts → 技能根
tests/
  smoke.py               路径吻合率回归测试
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

## 维护规范

### 分支

只保留 `main`，任何时刻都是可用状态。小改动直接提交；重构路径算法、换渲染方案这类大改开 `feat/xxx` 短分支，自查后 `git merge --squash` 合回，分支随即删除。不设长期 `dev`。

### 提交

Conventional Commits，标题英文前缀 + 中文描述：

```
feat(paths): 支持深色纸张自动反色
fix(player): 切幕时音频未重置导致下一幕无声
docs: 补分镜表模板
chore(deps): 升级 pngjs 到 7.0
```

`type` 只用 `feat` / `fix` / `docs` / `refactor` / `chore` / `test`；`scope` 用目录名（`paths` `player` `pipeline` `skill`）；标题 ≤ 72 字符，正文只写「为什么」。破坏性变更写成 `feat(paths)!:` 并在正文加 `BREAKING CHANGE: ...`。

### 版本

tag 打在 `main` 上，`vX.Y.Z`：

| 变化 | 版本 |
|---|---|
| `paths.json` / `project.json` 字段变化、调用方式变化 | major |
| 新增脚本、新参数、新能力 | minor |
| 修 bug、调参、文档 | patch |

每次 tag 在 `CHANGELOG.md` 补一段，`git push --follow-tags`。

### 提交前

```bash
python3 scripts/preflight.py                      # 敏感信息 / 本机路径 / 大文件 / 语法
python3 tests/smoke.py --images ~/videos/xxx/scenes   # 动过路径算法才需要
```

`preflight` 返回 1 就不要提交。`smoke` 拿真实插画跑一遍吻合率，阈值默认 99%。

### 敏感信息

不入库：token、短信验证码、手机号、cookie、真实姓名与邮箱、任何一次制作的文案/插画/配音/字幕/成片。

DubbingX 凭证只存在浏览器 `localStorage`，代码里只出现字段名。用户提供的素材属于用户，示例里一律用示意图代替。

误提交的处理：不 amend、不 rebase 已推送的历史。`git rm --cached <file>` 提一次删除，同时**去源头作废那个凭证**——推上去就按已泄露算。

### 协作

私有仓库，使用者给 clone 只读权限，不接受直接 push。外部改动走 fork + PR，合并条件：`preflight.py` 无告警、`smoke.py` 通过、涉及参数调整时附前后对比截图。

## 许可

私有仓库，保留所有权利。
