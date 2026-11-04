#!/usr/bin/env python3
"""白板视频工程脚手架。

    python3 new_project.py --out ~/videos/ip --title "IP 地址" --images ./pics

做的事：
    · 建 scenes/ paths/ preview/ audio/ cues/ srt/tts/spoken/ out/
    · 拷预览台（index.html / serve.py / voice.html / mp3_to_wav.js）与手素材
    · 从第一张图读尺寸，生成 project.json
    · 扫描图片生成 playlist.json（id = 文件名）

随后：
    node scripts/trace_paths.js --in scenes --out paths
    python3 serve.py 8010
"""
import argparse
import json
import re
import shutil
import struct
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PLAYER = SKILL / "assets" / "player"


def image_size(p: Path):
    """只读文件头拿宽高，不依赖 PIL。"""
    with p.open("rb") as f:
        head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return w, h
        if head[:2] == b"\xff\xd8":
            f.seek(2)
            while True:
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                while b == b"\xff":
                    b = f.read(1)
                if not b:
                    break
                marker = b[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    f.read(3)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                seg = struct.unpack(">H", f.read(2))[0]
                f.seek(seg - 2, 1)
    raise RuntimeError(f"读不出尺寸：{p}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="白板讲解")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--images", default=None, help="图片目录（默认 out/scenes）")
    ap.add_argument("--caption-height", type=int, default=None)
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    a = ap.parse_args()

    root = Path(a.out).expanduser().resolve()
    imgs_src = Path(a.images).expanduser().resolve() if a.images else root / "scenes"
    if not imgs_src.is_dir():
        raise SystemExit(f"图片目录不存在：{imgs_src}")

    for d in ("scenes", "paths", "preview", "audio", "cues", "out",
              "srt/tts/spoken", "assets", "build"):
        (root / d).mkdir(parents=True, exist_ok=True)

    # 模板
    for f in ("index.html", "serve.py", "voice.html", "mp3_to_wav.js"):
        shutil.copy2(PLAYER / f, root / f)
    shutil.copy2(SKILL / "assets" / "hand.png", root / "assets" / "hand.png")

    # 字幕对齐链路（ASR 锚点 → 切块 → SRT → 体检），随工程走，脚本按 build/ 上级找根
    (root / "build" / "asr").mkdir(parents=True, exist_ok=True)
    for f in sorted((SKILL / "assets" / "pipeline").iterdir()):
        if f.is_file() and f.suffix in (".py", ".js"):
            shutil.copy2(f, root / "build" / f.name)

    # 依赖（jpeg-js / pngjs / mpg123-decoder 及传递依赖，约 1.2MB）
    # 工程自带一份，脚本在任意目录都能跑，不用再 npm i；L3 清理时一起删掉。
    nm = SKILL / "node_modules"
    if nm.is_dir() and not (root / "node_modules").exists():
        shutil.copytree(nm, root / "node_modules")

    # 图片（手素材/水印图不算一幕）
    exts = (".jpg", ".jpeg", ".png")
    skip = {"hand", "logo", "cover", "watermark"}
    pics = sorted([p for p in imgs_src.iterdir()
                   if p.suffix.lower() in exts and p.stem.lower() not in skip])
    if not pics:
        raise SystemExit(f"{imgs_src} 里没有 jpg/png")
    if imgs_src != root / "scenes":
        for p in pics:
            shutil.copy2(p, root / "scenes" / p.name)
    pics = sorted([p for p in (root / "scenes").iterdir() if p.suffix.lower() in exts])

    w = a.width or image_size(pics[0])[0]
    h = a.height or image_size(pics[0])[1]

    cfg = json.loads((PLAYER / "project.json").read_text(encoding="utf-8"))
    cfg.update({
        "title": a.title,
        "subtitle": a.subtitle or f"{len(pics)} 幕",
        "slug": a.slug or re.sub(r"[^A-Za-z0-9]+", "-", a.title).strip("-").lower() or "movie",
        "width": w,
        "height": h,
    })
    if a.caption_height is not None:
        cfg["captionHeight"] = a.caption_height
    (root / "project.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    scenes = []
    for i, p in enumerate(pics):
        scenes.append({
            "id": p.stem,
            "title": p.stem,
            "image": f"scenes/{p.name}",
            "startMs": 0,
            "endMs": 0,
        })
    (root / "playlist.json").write_text(
        json.dumps({"title": a.title, "scenes": scenes}, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"工程：{root}")
    print(f"画幅：{w}×{h} + 字幕条 {cfg['captionHeight']}")
    print(f"幕数：{len(scenes)}（{', '.join(s['id'] for s in scenes)}）")
    print("\n下一步（依赖已随工程拷贝，不用 npm i）：")
    print(f"  cd {root}")
    print(f"  node {SKILL / 'scripts' / 'trace_paths.js'} --in scenes --out paths")
    print("  python3 serve.py 8010")
    print(f"  python3 {SKILL / 'scripts' / 'check_project.py'} --root .")


if __name__ == "__main__":
    main()
