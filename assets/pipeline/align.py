#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把字幕对齐到配音的真实停顿上。

一次性合成整幕音频后，句子之间没有标好的时间戳；按字数比例估算，
字幕会平均偏 0.4 秒、最多 1.4 秒，看起来就是「字幕和发音对不上」。
这里用能量包络找出每个语音块的起点（= 静音结束处），再用 DP 把每一句
吸到最合适的停顿上：代价同时看「位置」和「这一屏的字数 ÷ 时长」。

用法：
    python3 build/align.py              # 生成 cues/scene-0X.json 与 cues/all.json
    python3 build/align.py scene-01     # 只打印该幕的停顿分布（诊断）
"""
from __future__ import annotations

import array
import json
import math
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJ = ROOT.parent


def envelope(path: Path, hop_ms: int = 10, win_ms: int = 25):
    """返回每 hop 一帧的 RMS（dBFS）与 hop 秒长。"""
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    pcm = array.array("h")
    pcm.frombytes(raw)
    if sys.byteorder == "big":
        pcm.byteswap()

    hop = int(sr * hop_ms / 1000)
    win = int(sr * win_ms / 1000)
    out = []
    for i in range(0, len(pcm) - win, hop):
        seg = pcm[i:i + win]
        acc = 0
        for v in seg:
            acc += v * v
        rms = math.sqrt(acc / win) / 32768.0
        out.append(20 * math.log10(rms) if rms > 1e-7 else -140.0)
    return out, hop_ms


def silences(env, hop_ms, floor_db=-40.0, min_gap_ms=120):
    """语音门限用相对峰值自适应，返回 [(startMs, endMs, midMs)]。"""
    peak = max(env)
    thr = max(floor_db, peak - 32.0)
    voiced = [v > thr for v in env]

    runs = []
    i = 0
    n = len(voiced)
    while i < n:
        if not voiced[i]:
            j = i
            while j < n and not voiced[j]:
                j += 1
            if (j - i) * hop_ms >= min_gap_ms:
                runs.append((i * hop_ms, j * hop_ms))
            i = j
        else:
            i += 1
    return runs, thr, peak


def dp_align(expect, cand, weights, avg_cps, min_gap=450, max_off=1600, w_rate=1.0):
    """把 m 个字幕起点吸附到 k 个候选锚点（严格递增、间距受限）。

    代价 = 位置偏差² + 语速偏差²：只贴位置的话，一条短句可能吞掉两句的时长，
    屏幕上就会出现「半天不换」或「一闪而过」。要求这一屏的字数÷时长接近
    全幕平均语速，切点才落在真正的句子边界上。
    """
    m, k = len(expect), len(cand)
    if m == 0 or k < m:
        return None
    INF = float("inf")
    dp = [[INF] * k for _ in range(m)]
    prev = [[-1] * k for _ in range(m)]
    for j in range(k):
        off = abs(cand[j] - expect[0])
        dp[0][j] = off * off if off <= max_off else INF

    RATE = 360000.0 * w_rate
    for i in range(1, m):
        want = max(300.0, weights[i - 1] / avg_cps * 1000.0)
        for j in range(k):
            off = abs(cand[j] - expect[i])
            if off > max_off:
                continue
            best, bj = INF, -1
            for p in range(j - 1, -1, -1):
                dur = cand[j] - cand[p]
                if dur < min_gap:
                    break
                base = dp[i - 1][p]
                if base == INF:
                    continue
                v = base + ((dur - want) / want) ** 2 * RATE
                if v < best:
                    best, bj = v, p
            if bj >= 0:
                dp[i][j] = best + off * off
                prev[i][j] = bj

    end_j = min(range(k), key=lambda j: dp[m - 1][j])
    if dp[m - 1][end_j] == INF:
        return None
    path = [end_j]
    for i in range(m - 1, 0, -1):
        path.append(prev[i][path[-1]])
    path.reverse()
    return path


def voiced_blocks(env, hop_ms):
    """返回连续有声区间 [(startMs, endMs)]，太短的碎片并到邻段。"""
    runs, _thr, _peak = silences(env, hop_ms)
    total = len(env) * hop_ms
    blocks, cur = [], [0, None]
    prev_end = 0
    for a, b in runs:
        if a > prev_end:
            blocks.append((prev_end, a))
        prev_end = b
    if prev_end < total:
        blocks.append((prev_end, total))
    blocks = [b for b in blocks if b[1] - b[0] > 60]
    # 合并 60ms 以内的碎块（爆破音、气口）
    merged = []
    for a, b in blocks:
        if merged and a - merged[-1][1] < 60:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def map_speech_pos(blocks, frac):
    """把「已说出的语音比例」映射回绝对时间：跳过静音，只数真正发声的时长。"""
    total = sum(b - a for a, b in blocks)
    want = total * frac
    for a, b in blocks:
        d = b - a
        if want <= d:
            return a + want
        want -= d
    return blocks[-1][1]


def split_sentences(text):
    """按句末标点切句，标点跟着上一句走。"""
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。！？":
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def split_clauses(sent):
    """按句内停顿切小句，标点跟着前一小句。"""
    out, buf = [], ""
    for ch in sent:
        buf += ch
        if ch in "，、；：":
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return [c for c in out if c]


def layout(text, target=20.0, hard=26.0):
    """排字幕行：优先在小句（逗号、顿号）处换行，实在放不下才硬断。

    宁可某行略长（最多 hard 字），也不把「窗口」劈成「窗 / 口」。
    """
    import textwrap_cjk as T

    clauses = split_clauses(text)
    lines, cur = [], ""
    for c in clauses:
        if not cur:
            cur = c
        elif T.width(cur) + T.width(c) <= hard:
            cur += c
        else:
            lines.append(cur)
            cur = c
    if cur:
        lines.append(cur)

    out = []
    for ln in lines:
        if T.width(ln) <= hard + 2:
            out.append(ln.strip())
        else:
            out.extend(T.wrap(ln, target + 2))
    out = [ln for ln in out if ln]
    # 别让某行只剩「一查，」这种三五个字：并到下一行去
    i = 0
    while i < len(out) - 1:
        if T.width(out[i]) < 8 and T.width(out[i]) + T.width(out[i + 1]) <= hard + 4:
            out[i:i + 2] = [out[i] + out[i + 1]]
        else:
            i += 1
    return out


def pack_cues(sent):
    """一句话 → 若干条字幕（每条 1-2 行），不产生只有两个字的小尾巴。"""
    import textwrap_cjk as T

    lines = layout(sent)
    if len(lines) <= 2:
        return [lines]
    k = math.ceil(len(lines) / 2)
    per = math.ceil(len(lines) / k)
    groups = [lines[i:i + per] for i in range(0, len(lines), per)]
    # 末尾只剩几个字时，把最后两组并起来重排，宁可宽一点也不留残句
    if len(groups) >= 2 and T.width("".join(groups[-1])) < 10:
        merged = "".join(groups[-2] + groups[-1])
        two = T.wrap(merged, 30)
        if len(two) == 2:
            groups[-2:] = [two]
        else:
            groups[-2:] = [groups[-2] + groups[-1]]

    # 一屏塞太多字读不完：两行超过 46 字就拆成一屏一行
    out = []
    for g in groups:
        if len(g) == 2 and T.width("".join(g)) > 46:
            out.extend([[ln] for ln in g])
        else:
            out.append(g)
    return out


def build():
    import textwrap_cjk as T

    tl = json.loads((ROOT / "timeline.json").read_text(encoding="utf-8"))
    playlist = json.loads((PROJ / "playlist.json").read_text(encoding="utf-8"))
    scenes = {s["scene"]: s for s in tl["scenes"]}

    out_dir = PROJ / "cues"
    out_dir.mkdir(exist_ok=True)
    all_cues, drift = [], []

    for sc in playlist["scenes"]:
        num = int(sc["id"].split("-")[1])
        segs = [s for s in tl["segments"] if s["id"] in sc["segments"]]

        # 字幕单元 = 一句话；太长就在逗号处拆成几条
        units = []
        for seg in segs:
            for sent in split_sentences(seg["text"]):
                for lines in pack_cues(sent):
                    units.append(("".join(lines), lines, seg["id"]))
        if not units:
            continue

        wav = PROJ / "audio" / f"scene-{num:02d}.wav"
        env, hop = envelope(wav)
        blocks = voiced_blocks(env, hop)
        cand = [max(0, a - 60) for a, _ in blocks]

        weights = [max(1, sum(1 for c in u[0] if c not in "，。、；：？！…—·“”‘’（）《》 ")) for u in units]
        total_w = sum(weights)
        avg_cps = total_w / max(1.0, sum(b - a for a, b in blocks) / 1000.0)

        acc = 0.0
        expect = []
        for w in weights:
            expect.append(map_speech_pos(blocks, acc / total_w))
            acc += w

        path = dp_align(expect, cand, weights, avg_cps, min_gap=500, max_off=3000, w_rate=4.0)
        if path is None:
            print(f"幕{num}: 锚点不足（{len(cand)} < {len(units)}），退回字数比例")
            picks = expect
        else:
            picks = [cand[j] for j in path]

        cues = []
        for i, (text, lines, sid) in enumerate(units):
            s = picks[i]
            # 结尾接下一句起点，让字幕连续覆盖；最后一句收在语音结束
            e = picks[i + 1] if i + 1 < len(picks) else blocks[-1][1]
            cues.append({"startMs": int(s), "endMs": int(e), "text": text, "lines": lines, "seg": sid})
            if path is not None:
                drift.append(abs(s - expect[i]))

        data = {"scene": num, "durationMs": int((blocks[-1][1] - blocks[0][0])), "cues": cues}
        (out_dir / f"scene-{num:02d}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        all_cues.append(data)
        print(f"幕{num}: {len(cues)} 条字幕，锚点 {len(cand)}，最大校正 {max((abs(p-e) for p,e in zip(picks,expect)), default=0):.0f}ms")

    (PROJ / "cues" / "all.json").write_text(json.dumps({"scenes": all_cues}, ensure_ascii=False, indent=1), encoding="utf-8")
    if drift:
        drift.sort()
        print(f"校正量 中位 {drift[len(drift)//2]:.0f}ms  90分位 {drift[int(len(drift)*0.9)]:.0f}ms  最大 {drift[-1]:.0f}ms")
    print(f"字幕总条数 {sum(len(s['cues']) for s in all_cues)} → cues/")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1].startswith("scene-"):
        key = sys.argv[1]
        p = PROJ / "audio" / f"{key}.wav"
        env, hop = envelope(p)
        runs, thr, peak = silences(env, hop)
        print(f"{key}  帧数 {len(env)}  峰值 {peak:.1f}dB  门限 {thr:.1f}dB")
        print(f"静音段 {len(runs)} 个（>=120ms）")
        inner = [r for r in runs if r[0] > 300 and r[1] < len(env) * hop - 300]
        print(f"句中静音 {len(inner)} 个")
        for a, b in inner[:40]:
            print(f"  {a/1000:7.2f}s - {b/1000:7.2f}s  停顿 {(b-a):4d}ms")
        return
    build()


if __name__ == "__main__":
    main()
