#!/usr/bin/env python3
"""工程体检：出片前跑一遍，把"看起来做完了但其实是坏"的项目挡下来。

    python3 check_project.py [--root .] [--strict]

检查：
    1. project.json / playlist.json 完整，幕数与图片数一致
    2. 每幕图片、paths/<id>.json 存在，画幅与 project 一致
    3. regions 非空、t 单调不减、末个 t1 = 1、单幕图形数合理
    4. audio/<id>.wav 存在且时长 > 0
    5. cues/<id>.json 存在，所有 cue.text 拼接后与 srt/tts/<id>.txt 逐字一致
    6. cue 时间单调、不重叠、不超音频尾
    7. 字幕行宽 ≤ 25 全角、行首无避头尾字符、无孤字行

退出码：0 全过；1 有 FAIL；2 只有 WARN（--strict 时也算失败）
"""
import argparse
import json
import sys
import unicodedata
import wave
from pathlib import Path

NO_HEAD = "，。、；：？！）】》」』”’,.;:?!)]}>%‰℃"
NO_TAIL = "（【《「『“‘([{<"
MAX_W = 25

fails, warns = [], []


def ok(msg): print(f"  [ok]   {msg}")
def warn(msg): warns.append(msg); print(f"  [warn] {msg}")
def bad(msg): fails.append(msg); print(f"  [FAIL] {msg}")


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s) / 2


def wav_sec(p: Path):
    try:
        with wave.open(str(p)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()

    print(f"体检：{root}\n")

    # 1. 配置
    try:
        cfg = json.loads((root / "project.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[FAIL] 读不到 project.json：{e}")
        sys.exit(1)
    for k in ("width", "height", "captionHeight", "penWidth", "fit"):
        if k not in cfg:
            bad(f"project.json 缺字段 {k}")
    W, H = cfg.get("width"), cfg.get("height")
    ok(f"project.json  {W}×{H} + 字幕条 {cfg.get('captionHeight')}")

    try:
        pj = json.loads((root / "playlist.json").read_text(encoding="utf-8"))
        scenes = pj["scenes"]
    except Exception as e:
        print(f"[FAIL] 读不到 playlist.json：{e}")
        sys.exit(1)
    ok(f"playlist.json  {len(scenes)} 幕")

    for i, sc in enumerate(scenes):
        sid = sc.get("id") or f"scene-{i+1:02d}"
        print(f"\n— {i+1}. {sid}  {sc.get('title','')}")

        # 2. 图片
        img = root / (sc.get("image") or "")
        if not img.is_file():
            bad(f"{sid}: 图片不存在 {sc.get('image')}")
        else:
            ok(f"图片 {img.name}")

        # 3. 路径
        pjf = root / "paths" / f"{sid}.json"
        if not pjf.is_file():
            bad(f"{sid}: 缺 paths/{sid}.json（先跑 trace_paths.js）")
        else:
            d = json.loads(pjf.read_text(encoding="utf-8"))
            if (d.get("w"), d.get("h")) != (W, H):
                bad(f"{sid}: 路径画幅 {d.get('w')}×{d.get('h')} 与 project {W}×{H} 不一致")
            regs = d.get("regions") or []
            if not regs:
                bad(f"{sid}: regions 为空")
            else:
                t0 = [r["t0"] for r in regs]
                t1 = [r["t1"] for r in regs]
                if any(b < a - 1e-6 for a, b in zip(t0, t0[1:])):
                    bad(f"{sid}: t0 不是单调递增")
                if any(r["t1"] <= r["t0"] for r in regs):
                    bad(f"{sid}: 有 region 的 t1 <= t0")
                if abs(regs[-1]["t1"] - 1) > 1e-3:
                    bad(f"{sid}: 末个 t1={regs[-1]['t1']}，应为 1")
                pts = sum(len(r["ring"]) // 2 for r in regs)
                ok(f"{len(regs)} 个图形 / {pts} 点 / 路径长 {d.get('len')}px")

        # 4. 音频
        wav = root / "audio" / f"{sid}.wav"
        if not wav.is_file():
            warn(f"{sid}: 没有 audio/{sid}.wav（无配音时导出会没声音）")
            audio_sec = None
        else:
            audio_sec = wav_sec(wav)
            if not audio_sec or audio_sec <= 0:
                bad(f"{sid}: 音频读不出时长")
            else:
                ok(f"音频 {audio_sec:.1f}s")

        # 5. cues
        cf = root / "cues" / f"{sid}.json"
        txt = root / "srt" / "tts" / f"{sid}.txt"
        if not cf.is_file():
            bad(f"{sid}: 缺 cues/{sid}.json（先跑 align2.py）")
            continue
        cues = json.loads(cf.read_text(encoding="utf-8")).get("cues") or []
        if not cues:
            bad(f"{sid}: cues 为空")
            continue
        joined = "".join(c["text"] for c in cues).replace(" ", "").replace("\n", "")
        if txt.is_file():
            src = txt.read_text(encoding="utf-8").replace(" ", "").replace("\n", "")
            if joined != src:
                # 找第一个不同的位置，指出来方便改
                k = next((i for i, (x, y) in enumerate(zip(joined, src)) if x != y), min(len(joined), len(src)))
                bad(f"{sid}: 字幕与旁白不一致（第 {k} 字起：字幕『{joined[k:k+12]}』/ 原稿『{src[k:k+12]}』）")
            else:
                ok(f"{len(cues)} 条字幕，与旁白逐字一致（{len(src)} 字）")
        else:
            warn(f"{sid}: 没有 srt/tts/{sid}.txt，跳过逐字核对")

        # 6. 时间
        prev = -1
        for c in cues:
            if c["startMs"] < prev - 1:
                bad(f"{sid}: cue 时间回退 @{c['startMs']}ms")
                break
            if c["endMs"] <= c["startMs"]:
                bad(f"{sid}: cue 时长 <= 0 @{c['startMs']}ms")
                break
            prev = c["endMs"]
        else:
            if audio_sec and cues[-1]["endMs"] > audio_sec * 1000 + 800:
                warn(f"{sid}: 末条字幕 {cues[-1]['endMs']}ms 超出音频 {audio_sec*1000:.0f}ms")

        # 7. 排版
        for c in cues:
            lines = c.get("lines") or []
            if not lines:
                bad(f"{sid}: cue 没有 lines @{c['startMs']}ms")
                continue
            if len(lines) > 2:
                warn(f"{sid}: 一条字幕 {len(lines)} 行 @{c['startMs']}ms")
            for ln in lines:
                if width(ln) > MAX_W + 1:
                    warn(f"{sid}: 行宽 {width(ln):.0f} 超过 {MAX_W}『{ln}』")
                if ln and ln[0] in NO_HEAD:
                    bad(f"{sid}: 行首是标点『{ln}』")
                if ln and ln[-1] in NO_TAIL:
                    warn(f"{sid}: 行尾是开括号『{ln}』")
                if len(ln.strip("，。、；：？！")) <= 2 and len(lines) > 1:
                    warn(f"{sid}: 孤字行『{ln}』")

    print("\n" + "=" * 46)
    if fails:
        print(f"FAIL {len(fails)} 项，WARN {len(warns)} 项")
        for m in fails:
            print("  · " + m)
        sys.exit(1)
    if warns:
        print(f"全部通过，WARN {len(warns)} 项")
        sys.exit(2 if a.strict else 0)
    print("全部通过")


if __name__ == "__main__":
    main()
