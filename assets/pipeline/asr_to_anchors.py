#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 AudioTranscribe 的听写结果转成 align2 用的锚点。

输入：build/asr/scene-XX.txt，每行形如
    [0:4.000,0:7.100,0]  它最直观的变化是长度128位。
输出：build/asr_anchors.py

为什么要锚点：TTS 遇到长句（尤其带数字、字母）会自己断句，纯靠停顿长度选切点会串位。
ASR 的每段起点就是"这句话真正开口的时刻"，拿它把稿子切成块，误差就不会跨块累积。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "asr"


def parse_sec(s: str) -> float:
    if ":" in s:
        m, sec = s.split(":")
        return int(m) * 60 + float(sec)
    return float(s)


def norm(s: str) -> str:
    out = []
    for ch in s:
        c = ch.lower()
        if "\u4e00" <= c <= "\u9fff" or "a" <= c <= "z":
            out.append(c)
    return "".join(out)


def main() -> None:
    anchors = {}
    for f in sorted(SRC.glob("*.txt")):
        sid = f.stem
        # 兼容老的 scene-01.txt 与新的 01.txt
        key = sid.split("-", 1)[1] if sid.startswith("scene-") else sid
        items = []
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\[([\d:.]+),([\d:.]+),\d+\]\s*(.*)", line.strip())
            if not m:
                continue
            t = parse_sec(m.group(1))
            txt = m.group(3).strip()
            head = norm(txt)[:8]
            if len(head) < 3:
                continue
            items.append((round(t, 2), head))
        anchors[key] = items
        print(f"幕 {key}: {len(items)} 个锚点")

    body = "\n".join(f"    {k!r}: {v!r}," for k, v in anchors.items())
    out = ('#!/usr/bin/env python3\n'
           '"""ASR 复核得到的锚点：每段开头几个字 + 那句话的开口时间（秒）。\n\n'
           '由 build/asr_to_anchors.py 从 build/asr/ 的听写结果生成，时间会由 align2 统一补 0.2s。\n'
           '换配音后必须重跑 AudioTranscribe 并更新本文件。\n"""\n\n'
           f"ANCHORS = {{\n{body}\n}}\n")
    (ROOT / "asr_anchors.py").write_text(out, encoding="utf-8")
    print("→ build/asr_anchors.py")


if __name__ == "__main__":
    main()
