#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按配音实际时长给 playlist.json 排幕级时间轴（SRT 合并时要用）。

每幕尾部留 400ms 完整画面，幕间再空 200ms，避免两幕切换像被硬切。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAP_TAIL = 400
GAP_HEAD = 200


def main() -> None:
    pl = json.loads((ROOT / "playlist.json").read_text(encoding="utf-8"))
    mf = json.loads((ROOT / "audio" / "manifest.json").read_text(encoding="utf-8"))
    t = 0
    for sc in pl["scenes"]:
        dur = mf[sc["id"]]["durMs"]
        sc["startMs"] = t
        sc["endMs"] = t + dur + GAP_TAIL
        t = sc["endMs"] + GAP_HEAD
    (ROOT / "playlist.json").write_text(json.dumps(pl, ensure_ascii=False, indent=1), encoding="utf-8")
    total = (pl["scenes"][-1]["endMs"]) / 1000
    print(f"全片 {total/60:.1f} 分钟（{total:.1f}s），{len(pl['scenes'])} 幕")
    for sc in pl["scenes"]:
        print(f"  {sc['id']}  {(sc['endMs']-sc['startMs'])/1000:5.1f}s  {sc['title']}")


if __name__ == "__main__":
    main()
