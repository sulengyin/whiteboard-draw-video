#!/usr/bin/env python3
"""修 MediaRecorder 录出来的 MP4：时间轴全是错的。

Chrome 的 MP4 复用器有三个毛病，播放器只要吃其中一个就会出怪声：

1. **Opus 音轨的 sample duration 按"包到货的墙钟间隔"记**（21ms / 37.5ms 乱跳），
   整条音轨比包内真实帧长短 5%。按时间戳播放的播放器会把相邻包叠在一起，
   听感就是持续的"电流声 / 滋滋声"。Chrome 自己顺序解码，所以本机预览不明显。
2. **每轨的 tkhd.duration 写的是 0**，播放器算不出轨道长度。
3. **视频轨的 mdhd.duration 是首帧长度**（30fps 下 0.04 秒），而实际有 49 秒。
   有的播放器拿它当视频时间轴，就会去"拉伸"音轨对齐 → 满篇沙沙声。

本脚本：
  - 按每个 Opus 包的 TOC 字节算真实帧长，重写 sample duration / tfdt；
  - 按 trun 累加出每轨真实时长，回写 mdhd / tkhd / mvhd；
  - 视频轨的 sample 表一个字不动（VFR 是正常现象）。

用法：
  python3 retime_mp4.py in.mp4 [out.mp4]     # 不给 out 就原地改
  python3 retime_mp4.py --check in.mp4       # 只体检，不写
"""
import struct
import sys
from pathlib import Path

# Opus TOC config(5bit) → 每帧采样数（48k）
FRAME_SAMPLES = [
    480, 960, 1920, 2880,        # 0-3   SILK NB 10/20/40/60ms
    480, 960, 1920, 2880,        # 4-7   SILK MB
    480, 960, 1920, 2880,        # 8-11  SILK WB
    480, 960,                     # 12-13 Hybrid SWB 10/20ms
    480, 960,                     # 14-15 Hybrid FB 10/20ms
    120, 240, 480, 960,           # 16-19 CELT NB 2.5/5/10/20ms
    120, 240, 480, 960,           # 20-23 CELT WB
    120, 240, 480, 960,           # 24-27 CELT SWB
    120, 240, 480, 960,           # 28-31 CELT FB
]

# trun 每个字段的宽度（按 flags 依次排布）
TRUN_FIELDS = ((0x100, 4), (0x200, 4), (0x400, 4), (0x800, 4))


def full_flags(buf, off):
    """完整 box 的 version+flags：+8 起 4 字节，低 24 位才是 flags。"""
    return struct.unpack(">I", buf[off + 8:off + 12])[0] & 0xFFFFFF


def top_boxes(buf):
    i, n = 0, len(buf)
    while i + 8 <= n:
        size = struct.unpack(">I", buf[i:i + 4])[0]
        typ = bytes(buf[i + 4:i + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = n - i
        if size < hdr:
            break
        yield typ, i, i + size, hdr
        i += size


def children(buf, start, end):
    i = start
    while i + 8 <= end:
        size = struct.unpack(">I", buf[i:i + 4])[0]
        typ = bytes(buf[i + 4:i + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            break
        yield typ, i, i + size, hdr
        i += size


def walk_traks(buf):
    """遍历 moov 下的 trak，产出 {tid, ts, mdhd, tkhd, handler}。"""
    for typ, a, b, _ in top_boxes(buf):
        if typ != b"moov":
            continue
        for t2, m0, m1, _ in children(buf, a + 8, b):
            if t2 != b"trak":
                continue
            info = {"tid": None, "ts": None, "mdhd": None, "tkhd": None, "handler": None}
            for t3, k0, k1, _ in children(buf, m0 + 8, m1):
                if t3 == b"tkhd":
                    v = buf[k0 + 8]
                    info["tkhd"] = k0
                    info["tid"] = struct.unpack(">I", buf[k0 + (28 if v == 1 else 20):
                                                        k0 + (32 if v == 1 else 24)])[0]
                if t3 == b"mdia":
                    for t4, j0, j1, _ in children(buf, k0 + 8, k1):
                        if t4 == b"hdlr":
                            info["handler"] = bytes(buf[j0 + 16:j0 + 20])
                        elif t4 == b"mdhd":
                            v = buf[j0 + 8]
                            info["mdhd"] = j0
                            info["ts"] = struct.unpack(">I", buf[j0 + (28 if v == 1 else 20):
                                                              j0 + (32 if v == 1 else 24)])[0]
            if info["tid"]:
                yield info


def find_audio_track(buf):
    """返回 (track_id, timescale, mdhd_box_offset)。"""
    for t in walk_traks(buf):
        if t["handler"] == b"soun" and t["ts"]:
            return t["tid"], t["ts"], t["mdhd"]
    return None, None, None


def read_tfhd_track(buf, tfhd):
    flags = full_flags(buf, tfhd)
    q = tfhd + 12
    if flags & 0x01:
        q += 8                      # base_data_offset
    return struct.unpack(">I", buf[q:q + 4])[0]


def trun_layout(buf, trun):
    """返回 (count, 首个样本字段偏移, 步长)。"""
    f = full_flags(buf, trun)
    cnt = struct.unpack(">I", buf[trun + 12:trun + 16])[0]
    p = trun + 16
    if f & 0x01:
        p += 4
    if f & 0x04:
        p += 4
    step = sum(w for bit, w in TRUN_FIELDS if f & bit)
    return f, cnt, p, step


def sample_size(buf, q, f, pos):
    """样本大小字段的位置（duration 在 size 前 4 字节）。"""
    return struct.unpack(">I", buf[q + pos:q + pos + 4])[0]


def opus_duration(toc, nxt):
    """按 TOC 算真实采样数（48k）。"""
    per = FRAME_SAMPLES[toc >> 3]
    code = toc & 3
    if code == 0:
        return per
    if code in (1, 2):
        return per * 2
    return per * (nxt & 0x3F)          # code 3：帧数在下一字节低 6 位


def track_durations(buf):
    """按 trun 累加每轨的真实时长（各自 timescale）。"""
    dur = {}
    for typ, a, b, _ in top_boxes(buf):
        if typ != b"moof":
            continue
        for t2, m0, m1, _ in children(buf, a + 8, b):
            if t2 != b"traf":
                continue
            tfhd = trun = None
            for t3, o0, o1, _ in children(buf, m0 + 8, m1):
                if t3 == b"tfhd":
                    tfhd = o0
                elif t3 == b"trun":
                    trun = o0
            if tfhd is None or trun is None:
                continue
            tid = read_tfhd_track(buf, tfhd)
            f, cnt, p, step = trun_layout(buf, trun)
            if f & 0x100:
                dur[tid] = dur.get(tid, 0) + sum(
                    struct.unpack(">I", buf[p + k * step:p + k * step + 4])[0]
                    for k in range(cnt))
    return dur


def retime(src, dst=None, check_only=False):
    """修复音轨时长并回填各轨元数据，返回 (包数, 秒数)。"""
    src = Path(src)
    dst = Path(dst) if dst else src
    buf = bytearray(src.read_bytes())

    tid, ts, mdhd = find_audio_track(buf)
    if tid is None:
        raise RuntimeError("没有找到音轨")

    cursor = 0
    fixed = 0
    for typ, a, b, _ in top_boxes(buf):
        if typ != b"moof":
            continue
        for t2, m0, m1, _ in children(buf, a + 8, b):
            if t2 != b"traf":
                continue
            tfhd = trun = tfdt = None
            for t3, o0, o1, _ in children(buf, m0 + 8, m1):
                if t3 == b"tfhd":
                    tfhd = o0
                elif t3 == b"trun":
                    trun = o0
                elif t3 == b"tfdt":
                    tfdt = o0
            if tfhd is None or trun is None:
                continue
            if read_tfhd_track(buf, tfhd) != tid:
                continue

            f, cnt, p, step = trun_layout(buf, trun)
            if not (f & 0x100 and f & 0x200):
                continue                 # 没有 duration+size，没法重排
            data_off = 0
            q0 = trun + 16
            if f & 0x01:
                data_off = struct.unpack(">i", buf[q0:q0 + 4])[0]
            base = a + data_off          # data_offset 相对 moof 起点

            if not check_only and tfdt is not None:
                v = buf[tfdt + 8]
                if v == 1:
                    struct.pack_into(">Q", buf, tfdt + 12, cursor)
                else:
                    struct.pack_into(">I", buf, tfdt + 12, cursor)

            off = base
            for k in range(cnt):
                q = p + k * step
                size = struct.unpack(">I", buf[q + 4:q + 8])[0]
                n48 = opus_duration(buf[off], buf[off + 1] if size > 1 else 0)
                n_units = n48 * ts // 48000
                if not check_only:
                    struct.pack_into(">I", buf, q, n_units)
                cursor += n_units
                fixed += 1
                off += size

    # 各轨真实时长 → mdhd（轨道 timescale）、tkhd（movie timescale）、mvhd（取最长）
    dur = track_durations(buf)
    if not check_only:
        moov = next((x for x in top_boxes(buf) if x[0] == b"moov"), None)
        mvts = 1000
        if moov:
            for t2, m0, m1, _ in children(buf, moov[1] + 8, moov[2]):
                if t2 == b"mvhd":
                    v = buf[m0 + 8]
                    mvts = struct.unpack(">I", buf[m0 + (28 if v == 1 else 20):
                                                  m0 + (32 if v == 1 else 24)])[0] or 1000
                    break
        longest = 0
        for t in walk_traks(buf):
            d = dur.get(t["tid"], 0)
            if not d or not t["ts"]:
                continue
            if t["mdhd"]:
                v = buf[t["mdhd"] + 8]
                if v == 1:
                    struct.pack_into(">Q", buf, t["mdhd"] + 32, d)
                else:
                    struct.pack_into(">I", buf, t["mdhd"] + 24, d)
            if t["tkhd"]:
                mt = int(round(d * mvts / t["ts"]))
                longest = max(longest, mt)
                v = buf[t["tkhd"] + 8]
                if v == 1:
                    struct.pack_into(">Q", buf, t["tkhd"] + 32, mt)
                else:
                    struct.pack_into(">I", buf, t["tkhd"] + 28, mt)
        if moov and longest:
            for t2, m0, m1, _ in children(buf, moov[1] + 8, moov[2]):
                if t2 == b"mvhd":
                    v = buf[m0 + 8]
                    if v == 1:
                        struct.pack_into(">Q", buf, m0 + 32, longest)
                    else:
                        struct.pack_into(">I", buf, m0 + 24, longest)
                    break

    if not check_only:
        dst.write_bytes(buf)
    return fixed, cursor / (ts or 48000)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    check = "--check" in sys.argv
    if not args:
        raise SystemExit("用法：retime_mp4.py [--check] in.mp4 [out.mp4]")
    n, sec = retime(args[0], args[1] if len(args) > 1 else None, check_only=check)
    print(f"{'体检' if check else '重排'}：{n} 个包，{sec:.2f}s")


if __name__ == "__main__":
    main()
