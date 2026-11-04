#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 cues/（已按配音停顿对齐的字幕）生成 SRT。

字幕时间直接来自音频能量对齐的结果，不再按字数估算，
所以每一屏的出现时机与配音开口时刻一致。

用法：python3 build/make_srt.py
产物：srt/ip-explainer.srt、srt/ip-explainer.raw.srt
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenes as S
import textwrap_cjk as T

ROOT = Path(__file__).resolve().parent
PROJ = ROOT.parent
OUT = PROJ / "srt"


def ts(ms: int) -> str:
    h, ms = divmod(int(ms), 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build() -> None:
    allc = json.loads((PROJ / "cues" / "all.json").read_text(encoding="utf-8"))
    playlist = json.loads((PROJ / "playlist.json").read_text(encoding="utf-8"))
    base = {sc["id"]: sc["startMs"] for sc in playlist["scenes"]}

    cues, violations = [], []
    for sc in allc["scenes"]:
        key = sc.get("id") or f"{sc['scene']:02d}"
        off = base.get(key, 0)
        for c in sc["cues"]:
            lines = c["lines"]
            for p in T.check(lines):
                violations.append(f"{key} {c['startMs']}ms {p}：{' / '.join(lines)}")
            cues.append((off + c["startMs"], off + c["endMs"], lines))

    cues.sort(key=lambda x: x[0])
    OUT.mkdir(parents=True, exist_ok=True)

    body, raw = [], []
    for i, (s, e, lines) in enumerate(cues, 1):
        body.append(f"{i}\n{ts(s)} --> {ts(e)}\n" + "\n".join(lines) + "\n")
        raw.append(f"{i}\n{ts(s)} --> {ts(e)}\n" + "".join(lines) + "\n")

    (OUT / f"{S.slug(PROJ)}.srt").write_text("\n".join(body), encoding="utf-8")
    (OUT / f"{S.slug(PROJ)}.raw.srt").write_text("\n".join(raw), encoding="utf-8")

    print(f"字幕 {len(cues)} 条 → {OUT / (S.slug(PROJ) + '.srt')}")
    print("标点断行检查：", "全部合规" if not violations else f"{len(violations)} 处问题")
    for v in violations[:10]:
        print("  ", v)


if __name__ == "__main__":
    build()
