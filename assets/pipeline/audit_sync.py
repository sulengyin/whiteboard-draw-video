#!/usr/bin/env python3
"""体检：字幕条目的起止点是不是真的卡在配音的停顿上。

只看三件事：
1. 每条字幕的起点离「开口点」（语音块的头）有多远 —— 越近越好，理想 <120ms；
2. 每条字幕覆盖的区间里，有声帧占比 —— 太低说明这条字幕大部分时间在「空放」；
3. 有没有「没被任何字幕盖住的有声片段」—— 有就说明漏了一句。

    python3 build/audit_sync.py            # 全部八幕
    python3 build/audit_sync.py 3           # 只看第三幕
"""

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenes as S  # noqa: E402
from align import envelope, voiced_blocks  # noqa: E402

PROJ = Path(__file__).resolve().parent.parent


def audit(sid):
    cues = json.loads(S.cues_path(PROJ, sid).read_text(encoding="utf-8"))["cues"]
    env, hop = envelope(S.audio_path(PROJ, sid))
    total = len(env) * hop
    blocks = voiced_blocks(env, hop)

    # 每个 10ms 帧是否落在有声块里
    voiced = [False] * len(env)
    for a, b in blocks:
        for i in range(max(0, a // hop), min(len(env), b // hop + 1)):
            voiced[i] = True

    onsets = [a for a, _b in blocks]
    rows = []
    for c in cues:
        s, e = c["startMs"], c["endMs"]
        near = min(onsets, key=lambda o: abs(o - s)) if onsets else s
        off = s - near
        # 起点前有多少静音（mid-word 起跳的会接近 0）
        back = 0
        i = int(s // hop)
        while i - 1 >= 0 and not voiced[i - 1]:
            back += hop
            i -= 1
            if back > 600:
                break
        i0, i1 = int(s // hop), max(int(s // hop) + 1, int(e // hop))
        seg = voiced[i0:i1]
        ratio = sum(seg) / max(1, len(seg))
        rows.append({
            "start": s, "end": e, "off": off, "back": back,
            "ratio": ratio, "text": c["text"], "lines": c.get("lines", []),
        })

    # 没被字幕覆盖的有声时长
    covered = [False] * len(env)
    for c in cues:
        for i in range(max(0, int(c["startMs"] // hop)), min(len(env), int(c["endMs"] // hop))):
            covered[i] = True
    miss = sum(1 for i in range(len(env)) if voiced[i] and not covered[i]) * hop

    return {
        "id": sid, "total": total, "cues": rows, "blocks": blocks,
        "miss": miss, "voiced_ms": sum(b - a for a, b in blocks),
    }


def main():
    all_ids = S.ids(PROJ)
    which = [int(sys.argv[1])] if len(sys.argv) > 1 else list(range(1, len(all_ids) + 1))
    all_off, all_ratio, worst = [], [], []
    print(f"{'幕':<3}{'条数':>5}{'起点偏差中位':>12}{'最大':>7}{'有声占比中位':>12}{'漏盖':>8}")
    for n in which:
        r = audit(all_ids[n - 1])
        offs = [abs(x["off"]) for x in r["cues"]]
        ratios = [x["ratio"] for x in r["cues"]]
        all_off += offs
        all_ratio += ratios
        worst += [(abs(x["off"]), n, x) for x in r["cues"]]
        print(f"{n:<3}{len(r['cues']):>5}{statistics.median(offs):>10.0f}ms"
              f"{max(offs):>6.0f}{statistics.median(ratios):>11.0%}{r['miss']:>6}ms")

    print()
    print(f"全部条目 {len(all_off)}")
    print(f"起点偏差 中位 {statistics.median(all_off):.0f}ms  90分位 "
          f"{sorted(all_off)[int(len(all_off) * .9)]:.0f}ms  最大 {max(all_off):.0f}ms")
    print(f"有声占比 中位 {statistics.median(all_ratio):.0%}  最低 {min(all_ratio):.0%}")

    worst.sort(key=lambda t: -t[0])
    print("\n起点偏差最大的 8 条：")
    for off, n, x in worst[:8]:
        print(f"  幕{n} {x['start']/1000:6.2f}s 偏差{off:+5.0f}ms 前静音{x['back']:3.0f}ms "
              f"占比{x['ratio']:.0%} | {x['text'][:26]}")

    low = sorted([x for n in which for x in audit(all_ids[n - 1])["cues"]], key=lambda x: x["ratio"])[:5]
    print("\n有声占比最低的 5 条：")
    for x in low:
        print(f"  {x['start']/1000:6.2f}-{x['end']/1000:6.2f}s 占比{x['ratio']:.0%} | {x['text'][:26]}")


if __name__ == "__main__":
    main()
