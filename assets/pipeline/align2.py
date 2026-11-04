#!/usr/bin/env python3
"""按「标点 ↔ 停顿」对齐字幕，替代原来按字数比例的做法。

原理：配音是照稿合成的，稿子里的每个标点都会带来一个长度不同的停顿——
句号/问号约 450ms，逗号约 170ms，顿号更短。音频里能测出所有停顿的时长，
于是可以拿「标点序列」和「停顿序列」做一次序列比对（DTW），
把每个句号钉到对应的那个长停顿上。字幕的换屏点就是这些句号之后的开口时刻。

原来按字数推位置，遇到「IP 是门牌号，会变」这种前后字数不均衡的句子，
会认错停顿——字幕比声音晚一句（实测差 1.5 秒）。这个方法不会。

    python3 build/align2.py          # 重算 cues/
    python3 build/make_srt.py        # 再生成 SRT
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenes as S  # noqa: E402
import textwrap_cjk as T  # noqa: E402
from align import envelope, silences, voiced_blocks  # noqa: E402
try:
    from asr_anchors import ANCHORS
except ImportError:          # 没跑 ASR 时退化成纯停顿对齐
    ANCHORS = {}

PROJ = Path(__file__).resolve().parent.parent
ROOT = Path(__file__).resolve().parent

PAUSE_CHARS = "，。、；：？！…"
# 各标点后面 TTS 大致会停多久（毫秒），只用来判断「这个停顿更像哪种标点」
EXPECT = {"？": 450, "。": 450, "！": 450, "，": 170, "、": 90, "；": 260, "：": 300, "…": 350}
END_CHARS = "。！？"
MAXW = 26          # 需要拆句时，每片的宽度上限
SPLIT_MAX = 46     # 整句宽度不超过这个值就不拆，一条字幕两行放得下


def _split_by_width(text, spans):
    """把每句按宽度切开，切点优先落在逗号后。"""
    res = []
    for a, b in spans:
        k = a
        while k < b:
            w, cut, comma = 0.0, None, None
            j = k
            while j < b:
                w += 1 if ord(text[j]) > 127 else 0.55
                if text[j] in "，；：":
                    comma = j + 1
                if w >= MAXW:
                    break
                j += 1
            if j >= b:
                res.append((k, b))
                break
            cut = comma if (comma and comma > k + 6) else j
            # 尾巴太短就把这一刀挪到句末，宁可一条长一点
            if T.width(text[cut:b]) < 12:
                res.append((k, b))
                break
            res.append((k, cut))
            k = cut
    return res


def match_pauses(tokens, pauses):
    """标点序列 ↔ 停顿序列的单调比对，返回 {标点下标: 停顿下标}。"""
    n, m = len(tokens), len(pauses)
    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    bk = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            cur = dp[i][j]
            if cur == INF:
                continue
            if i < n and j < m:
                dur, exp = pauses[j][2], EXPECT[tokens[i]]
                c = (math.log((dur + 40) / (exp + 40))) ** 2
                if cur + c < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = cur + c
                    bk[i + 1][j + 1] = ("m", i, j)
            if i < n and cur + 0.8 < dp[i + 1][j]:
                dp[i + 1][j] = cur + 0.8
                bk[i + 1][j] = ("t", i, j)
            if j < m and cur + 0.35 < dp[i][j + 1]:
                dp[i][j + 1] = cur + 0.35
                bk[i][j + 1] = ("p", i, j)

    i, j, out = n, m, {}
    while i > 0 or j > 0:
        move, pi, pj = bk[i][j]
        if move == "m":
            out[pi] = pj
        i, j = pi, pj
    return out


def align_pieces(spans, text, pauses, onset, speech_end, cps, weights):
    """给每一句挑一个停顿作为它的结尾。

    两条约束一起用：
    - 这一句说了多久，要和它的字数匹配（字多就该说得久）；
    - 这个结尾的停顿长度，要和它的收尾标点匹配（句号后停得久，逗号后停得短）。
    只看停顿长短会串位，只看字数会在长短句交替处跑偏。
    """
    n, m = len(spans), len(pauses)
    if n == 0 or m == 0:
        return None
    exp = [max(400.0, w / cps * 1000.0) for w in weights]

    def dcost(actual, want):
        return 3.0 * (math.log(max(120.0, actual) / want)) ** 2

    def pcost(i, j):
        ch = text[spans[i][1] - 1]
        e = EXPECT.get(ch, 300)
        return 1.0 * (math.log((pauses[j][2] + 40) / (e + 40))) ** 2

    INF = float("inf")
    dp = [[INF] * m for _ in range(max(1, n - 1))]
    bk = [[-1] * m for _ in range(max(1, n - 1))]

    for j in range(m):
        span = pauses[j][1] - onset
        dp[0][j] = dcost(span, exp[0]) + pcost(0, j)

    for i in range(1, n - 1):
        for j in range(m):
            best, arg = INF, -1
            for jp in range(j):
                if dp[i - 1][jp] == INF:
                    continue
                span = pauses[j][1] - pauses[jp][1]
                v = dp[i - 1][jp] + dcost(span, exp[i])
                if v < best:
                    best, arg = v, jp
            if arg >= 0:
                dp[i][j] = best + pcost(i, j)
                bk[i][j] = arg

    last = n - 1
    best, arg = INF, -1
    for j in range(m):
        if dp[last - 1][j] == INF if last - 1 >= 0 else False:
            continue
        span = speech_end - pauses[j][1]
        base = dp[last - 1][j] if last - 1 >= 0 else 0.0
        v = base + dcost(span, exp[last])
        if v < best:
            best, arg = v, j
    if arg < 0:
        return None

    path = [arg]
    for i in range(last - 1, 0, -1):
        path.append(bk[i][path[-1]])
    path.reverse()
    return path          # 第 i 句以 pauses[path[i]] 结尾


def speech_pos(blocks, frac, lo, hi):
    """在 [lo,hi] 这段里，把「已说出的比例」映射回时间（跳过静音）。"""
    seg = [(max(a, lo), min(b, hi)) for a, b in blocks if b > lo and a < hi]
    seg = [(a, b) for a, b in seg if b > a]
    total = sum(b - a for a, b in seg) or 1
    want = total * frac
    for a, b in seg:
        if want <= b - a:
            return a + want
        want -= b - a
    return seg[-1][1] if seg else hi


def frac_at(blocks, t, lo, hi):
    """反查：时间 t 时，这段话已经说出了几成。"""
    seg = [(max(a, lo), min(b, hi)) for a, b in blocks if b > lo and a < hi]
    seg = [(a, b) for a, b in seg if b > a]
    total = sum(b - a for a, b in seg) or 1
    got = 0
    for a, b in seg:
        if t <= a:
            break
        got += min(t, b) - a
    return got / total


def split_sentence(text, a, b, s0, e0, blocks, pauses):
    """把一句排成 1 条还是几条。

    两种断点：
    - 宽度超了，在逗号处均匀切开；
    - 句子里有明显停顿（>=380ms，TTS 常把长句在这里断句），
      哪怕宽度没超也在这里断开，否则后半句会在声音还没到时提前显示。
    """
    if T.width(text[a:b]) <= SPLIT_MAX and not any(
            dur >= 380 and s0 < pa < e0 for pa, _pb, dur in pauses):
        return [(a, b)]

    cuts = set()
    if T.width(text[a:b]) > SPLIT_MAX:
        k = a
        while k < b:
            w, comma, j = 0.0, None, k
            while j < b:
                w += 1 if ord(text[j]) > 127 else 0.55
                if text[j] in "，；：":
                    comma = j + 1
                if w >= MAXW:
                    break
                j += 1
            if j >= b:
                break
            cut = comma if (comma and comma > k + 6) else j
            if T.width(text[cut:b]) < 12:
                break
            cuts.add(cut)
            k = cut

    for pa, pb, dur in pauses:
        if dur < 380 or not (s0 < pa < e0):
            continue
        f = frac_at(blocks, pb, s0, e0)
        pos = a + int(f * (b - a))
        cand = [k for k in range(max(a + 4, pos - 10), min(b - 1, pos + 11)) if text[k] in "，；："]
        if cand:
            k = min(cand, key=lambda k: abs(k - pos))
            cuts.add(k + 1)

    cuts = sorted(c for c in cuts if a + 4 < c < b - 4)
    out, prev = [], a
    for c in cuts:
        if T.width(text[prev:c]) < 8:
            continue
        out.append((prev, c))
        prev = c
    out.append((prev, b))
    return out


def norm(s):
    """只留汉字和小写字母，去掉标点、空格、数字（ASR 与稿子的数字写法不一致）。"""
    out, idx = [], []
    for i, ch in enumerate(s):
        if ch.isdigit():
            continue
        c = ch.lower()
        if "\u4e00" <= c <= "\u9fff" or "a" <= c <= "z":
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def apply_anchors(num, text, onset, speech_end, lead=200):
    """把 ASR 分段起点钉到稿子里的对应位置。"""
    ns, idx = norm(text)
    res, cur = [], 0
    pool = ANCHORS.get(num) or ANCHORS.get(str(num)) or []
    for sec, head in pool:
        h, _ = norm(head)
        if not h:
            continue
        pos = ns.find(h, cur)
        if pos < 0:
            continue
        oi = idx[pos]
        t = int(sec * 1000) + lead
        if not (onset <= t <= speech_end):
            continue
        if res and (oi <= res[-1][0] or t <= res[-1][1]):
            continue
        res.append((oi, t))
        cur = pos + len(h)
    return res


def emit(text, a, b, s0, e0, blocks, pauses):
    """一句 → 一条或几条字幕，时间在这个区间内分配。"""
    parts = split_sentence(text, a, b, s0, e0, blocks, pauses)
    if len(parts) == 1:
        return [(a, b, s0, e0)]
    wsum = sum(max(1, T.width(text[x:y])) for x, y in parts)
    acc, out = 0.0, []
    near = [q for q in pauses if s0 <= q[1] <= e0]
    for idx2, (x, y) in enumerate(parts):
        t0 = speech_pos(blocks, acc / wsum, s0, e0)
        if idx2 and near:
            q = min(near, key=lambda q: abs(q[1] - t0))
            if abs(q[1] - t0) <= 500:
                t0 = q[1]
        acc += max(1, T.width(text[x:y]))
        if idx2 + 1 < len(parts):
            t1 = speech_pos(blocks, acc / wsum, s0, e0)
            if near:
                q = min(near, key=lambda q: abs(q[1] - t1))
                if abs(q[1] - t1) <= 500:
                    t1 = q[1]
        else:
            t1 = e0
        out.append((x, y, t0, max(t1, t0 + 350)))
    return out


def align_scene(num, text, blocks, pauses, onset, speech_end, cps):
    """先按 ASR 锚点切块，块内再用「时长 + 标点停顿」对齐；误差不会跨块累积。"""
    anchors = apply_anchors(num, text, onset, speech_end)
    pts = [(0, onset)] + anchors + [(len(text), speech_end)]
    cues = []
    for (i0, t0), (i1, t1) in zip(pts, pts[1:]):
        if i1 - i0 < 2 or t1 - t0 < 250:
            continue
        spans = [(a + i0, b + i0) for a, b in _sentence_spans(text[i0:i1])]
        if not spans:
            continue
        weights = [max(1, T.width(text[a:b])) for a, b in spans]
        local = [p for p in pauses if t0 <= p[1] <= t1]
        path = align_pieces(spans, text, local, t0, t1, cps, weights) if len(spans) > 1 else None
        prev = t0
        for i, (a, b) in enumerate(spans):
            end = local[path[i]][1] if (path and i < len(path)) else t1
            end = min(max(end, prev + 300), t1)
            cues += emit(text, a, b, prev, end, blocks, local)
            prev = end

    # 收尾：时间必须单调、首尾相接，不能有缝也不能重叠
    cues.sort(key=lambda c: c[2])
    for i in range(len(cues)):
        if i:
            cues[i] = (cues[i][0], cues[i][1], max(cues[i][2], cues[i - 1][3]), cues[i][3])
        cues[i] = (cues[i][0], cues[i][1], cues[i][2], max(cues[i][3], cues[i][2] + 300))
    for i in range(len(cues) - 1):
        cues[i] = (cues[i][0], cues[i][1], cues[i][2], cues[i + 1][2])
    if cues:
        cues[-1] = (cues[-1][0], cues[-1][1], cues[-1][2], max(cues[-1][3], cues[-1][2] + 300))
    return cues


def build():
    pl = S.playlist(PROJ)
    out_dir = PROJ / "cues"
    out_dir.mkdir(parents=True, exist_ok=True)
    all_cues = []

    for idx, sc in enumerate(pl["scenes"], 1):
        sid = sc["id"]
        src = S.tts_path(PROJ, sid)
        if not src.exists():
            print(f"幕{idx}（{sid}）：缺 {src.relative_to(PROJ)}，跳过")
            continue
        text = src.read_text(encoding="utf-8").strip()

        env, hop = envelope(S.audio_path(PROJ, sid))
        runs, _thr, _peak = silences(env, hop)
        blocks = voiced_blocks(env, hop)
        onset, speech_end = blocks[0][0], blocks[-1][1]
        pauses = [(a, b, b - a) for a, b in runs
                  if b - a >= 60 and a > onset + 80 and b < speech_end - 80]

        cps = sum(max(1, T.width(ch)) for ch in text) / max(0.1, (speech_end - onset) / 1000.0)
        cues = align_scene(sid, text, blocks, pauses, onset, speech_end, cps)
        cues = [c for c in cues if text[c[0]:c[1]].strip()]   # 空白片段不进字幕
        if not cues:
            print(f"幕{idx}（{sid}）：没算出来")
            continue

        data = {"scene": idx, "id": sid, "durationMs": int(speech_end - onset),
                "cues": [{"startMs": int(c[2]), "endMs": int(c[3]), "text": text[c[0]:c[1]],
                          "lines": _lines(text[c[0]:c[1]]), "seg": sid} for c in cues]}
        (out_dir / f"{sid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        all_cues.append(data)
        print(f"幕{idx}（{sid}）：{len(cues)} 条，锚点 {len(apply_anchors(sid, text, onset, speech_end))}，"
              f"语速 {cps:.1f} 字/秒")

    (out_dir / "all.json").write_text(json.dumps({"scenes": all_cues}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"字幕总条数 {sum(len(s['cues']) for s in all_cues)}")


LINE_MAX = 25        # 1024px 画布、36px 字号，一行最多约 25 个汉字


def _lines(piece):
    """一条字幕排 1–2 行：能一行放下就不拆，必须拆时在标点处对半开。

    之前用 T.wrap(piece, 20) 硬折，会出现「按协议版本 / 分，」这种把词劈开、
    第二行只剩两个字加标点的情况，看着像断错了。
    """
    if T.width(piece) <= LINE_MAX:
        return [piece]

    # 在逗号、分号处找一个让两行长度最接近的切点
    best = None
    for i, ch in enumerate(piece[:-1]):
        if ch not in "，；：、":
            continue
        lw, rw = T.width(piece[:i + 1]), T.width(piece[i + 1:])
        if lw <= LINE_MAX and rw <= LINE_MAX:
            gap = abs(lw - rw)
            if best is None or gap < best[0]:
                best = (gap, i + 1)
    if best:
        return [piece[:best[1]], piece[best[1]:]]

    lines = T.wrap(piece, LINE_MAX)
    if len(lines) > 2:
        # 实在太长，把后两行并成一行，宁可挤一点也别出现第三条
        lines = [lines[0], "".join(lines[1:])]
    return lines


def _sentence_spans(text):
    spans, start = [], 0
    for i, ch in enumerate(text):
        if ch in END_CHARS:
            spans.append((start, i + 1))
            start = i + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans


if __name__ == "__main__":
    build()
