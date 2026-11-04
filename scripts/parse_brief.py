#!/usr/bin/env python3
"""把填好的《素材模板.md》解析成工程可用的数据。

    python3 parse_brief.py --brief 素材.md --out ~/videos/xxx

产出：
    <out>/brief.json            项目信息 + 每幕旁白
    <out>/srt/tts/NN.txt        字幕原稿（逐字）
    <out>/srt/tts/spoken/NN.txt 发音稿（套用了「发音修正」）

幕号 → 两位数字 id（01、02…），与 new_project.py 生成的一致；
scene id 优先取图片文件名的主干，保证能对上 scenes/ 里的图。
"""
import argparse
import json
import re
import sys
from pathlib import Path

FIELD_RE = re.compile(r"^(图片|标题|旁白|发音修正)\s*[:：]\s*(.*)$")
SCENE_RE = re.compile(r"^###\s*第\s*(\d+)\s*幕")
INFO_RE = re.compile(r"^[-*+]\s*([^:：]+)[:：]\s*(.*)$")
FIX_RE = re.compile(r"^(.*?)\s*(?:→|->|=>)\s*(.+)$")

DEFAULTS = {
    "title": "",
    "subtitle": "",
    "slug": "",
    "orientation": "",
    "captions": "要",
    "voice": "",
    "paper": "",
    "quality": "",
}

# 模板里的中文键 → 内部字段
INFO_KEYS = {
    "标题": "title", "副标题": "subtitle", "输出文件名": "slug",
    "画幅": "orientation", "字幕": "captions", "音色": "voice",
    "纸色": "paper", "导出画质": "quality",
}


def is_note(line: str) -> bool:
    """模板里的说明行，整行被中文括号包住，直接丢。"""
    s = line.strip()
    return bool(s) and s.startswith("（") and s.endswith("）")


def parse(text: str):
    info = dict(DEFAULTS)
    scenes = []
    cur = None
    field = None
    buf = []

    def flush_field():
        nonlocal buf, field
        if cur is not None and field:
            raw = "\n".join(buf)
            body = "\n".join(l for l in raw.split("\n") if not is_note(l)).strip()
            cur[field] = body
        buf = []

    for line in text.splitlines():
        m_scene = SCENE_RE.match(line.strip())
        m_field = FIELD_RE.match(line.strip())

        if m_scene:
            flush_field()
            cur = {"n": int(m_scene.group(1)), "image": "", "title": "",
                   "oral": "", "spoken": "", "fixes": []}
            scenes.append(cur)
            field = None
            continue

        if line.startswith("## ") or line.startswith("---"):
            flush_field()
            field = None
            if not line.startswith("---"):
                cur = None
            continue

        if cur is None:
            m_info = INFO_RE.match(line.strip())
            if m_info:
                key = m_info.group(1).strip()
                val = m_info.group(2).strip()
                key = INFO_KEYS.get(key, key)
                if key in info:
                    info[key] = val
            continue

        if m_field:
            flush_field()
            field = {"图片": "image", "标题": "title",
                     "旁白": "oral", "发音修正": "fixes"}[m_field.group(1)]
            rest = m_field.group(2).strip()
            if rest and not is_note(rest):
                buf = [rest]
            continue

        if field:
            buf.append(line)

    flush_field()

    # 发音修正 → 逐条
    for s in scenes:
        fixes = []
        for line in s["fixes"].split("\n") if isinstance(s["fixes"], str) else []:
            m = FIX_RE.match(line.strip())
            if m:
                fixes.append([m.group(1).strip(), m.group(2).strip()])
        s["fixes"] = fixes

    # 幕号连续性
    for i, s in enumerate(scenes, 1):
        if s["n"] != i:
            sys.exit(f"第 {i} 段写的是「第 {s['n']} 幕」，幕号必须从 1 连续")
    return info, scenes


def apply_fixes(oral: str, fixes):
    out = oral
    for src, dst in fixes:
        out = out.replace(src, dst)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True, help="填好的素材模板.md")
    ap.add_argument("--out", required=True, help="工程根目录")
    ap.add_argument("--images", default=None, help="图片目录（默认找 brief 同级的 pics/ 或 scenes/）")
    a = ap.parse_args()

    brief_path = Path(a.brief).expanduser().resolve()
    root = Path(a.out).expanduser().resolve()
    info, scenes = parse(brief_path.read_text(encoding="utf-8"))

    if not scenes:
        sys.exit("没解析到任何一幕，检查是否写成「### 第 1 幕」")

    # 图片定位
    if a.images:
        img_dir = Path(a.images).expanduser().resolve()
    else:
        img_dir = next((p for p in (brief_path.parent / "pics",
                                    brief_path.parent / "scenes",
                                    brief_path.parent)
                        if p.is_dir()), brief_path.parent)

    missing = []
    for s in scenes:
        if not s["image"]:
            missing.append(f"第 {s['n']} 幕：没填图片")
            continue
        hit = None
        for cand in (img_dir / s["image"], root / "scenes" / s["image"]):
            if cand.exists():
                hit = cand
                break
        if hit is None:
            missing.append(f"第 {s['n']} 幕：找不到 {s['image']}（在 {img_dir}）")
            continue
        s["id"] = hit.stem
        s["path"] = str(hit)

    # 内容检查
    for s in scenes:
        if not s["oral"].strip():
            missing.append(f"第 {s['n']} 幕：旁白是空的")
    if missing:
        print("有问题的字段：")
        for m in missing:
            print("  -", m)
        sys.exit(1)

    (root / "srt" / "tts" / "spoken").mkdir(parents=True, exist_ok=True)
    for s in scenes:
        n = f"{s['n']:02d}"
        s["title"] = s["title"] or s["id"]
        s["spoken"] = apply_fixes(s["oral"], s["fixes"])
        # 字幕稿折成一整段：换行留给 TTS 发音稿做停顿，进字幕会变成空条目
        flat = re.sub(r"[ \t]*\n+[ \t]*", " ", s["oral"]).strip()
        (root / "srt" / "tts" / f"{n}.txt").write_text(flat + "\n", encoding="utf-8")
        (root / "srt" / "tts" / "spoken" / f"{n}.txt").write_text(
            s["spoken"].strip() + "\n", encoding="utf-8")

    payload = {
        "title": info["title"] or "白板讲解",
        "subtitle": info["subtitle"],
        "slug": info["slug"],
        "orientation": info["orientation"],
        "captions": info["captions"] != "不要",
        "voice": info["voice"] or "睿泽 / 0.92 / -4",
        "paper": info["paper"],
        "quality": info["quality"],
        "scenes": [{k: s[k] for k in ("n", "id", "image", "title", "oral", "spoken", "fixes")}
                   for s in scenes],
    }
    (root / "brief.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"标题：{payload['title']}")
    print(f"幕数：{len(scenes)}")
    total = 0
    for s in scenes:
        n = len(s["oral"])
        total += n
        flag = f" · {len(s['fixes'])} 处发音修正" if s["fixes"] else ""
        print(f"  {s['n']:>2}. {s['id']:<6} {n:>4} 字{flag}")
    print(f"         合计 {total} 字，粗估 {total / 4.2 / 60:.1f} 分钟")
    print(f"\n写出：{root}/brief.json 与 srt/tts/*.txt")


if __name__ == "__main__":
    main()
