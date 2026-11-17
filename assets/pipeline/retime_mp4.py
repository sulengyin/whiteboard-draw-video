#!/usr/bin/env python3
"""修 MediaRecorder 录出来的 MP4：音轨时长表是错的。

现象：Chrome 把 Opus 装进 MP4 时，trun 里的 sample duration 记的不是
包里真实的帧长（通常 3×20ms = 60ms），而是按到货时间瞎记（21ms / 37.5ms
乱跳），整条音轨因此短 5% 左右。按时间戳播放的播放器会把相邻包叠在一起，
听感就是持续的"电流声 / 滋滋声"；Chrome 自己顺序解码，所以本机预览不明显。

本脚本按每个 Opus 包的 TOC 字节算真实帧长，重写
  - 每个 sample 的 duration
  - 每片 traf 的 tfdt（改成严格累加）
  - mdhd.duration / mvhd.duration
视频轨完全不动。

用法：
  python3 retime_mp4.py in.mp4 [out.mp4]     # 不给 out 就原地改
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


def find_audio_track(buf):
    """返回 (track_id, timescale, mdhd_box_offset)。"""
    for typ, a, b, _ in top_boxes(buf):
        if typ != b"moov":
            continue
        for t2, m0, m1, _ in children(buf, a + 8, b):
            if t2 != b"trak":
                continue
            tid = None
            for t3, k0, k1, _ in children(buf, m0 + 8, m1):
                if t3 == b"tkhd":
                    v = buf[k0 + 8]
                    tid = struct.unpack(">I", buf[k0 + (28 if v == 1 else 20):
                                                k0 + (32 if v == 1 else 24)])[0]
                if t3 == b"mdia":
                    hdlr = ts = mdhd = None
                    for t4, j0, j1, _ in children(buf, k0 + 8, k1):
                        if t4 == b"hdlr":
                            hdlr = bytes(buf[j0 + 16:j0 + 20])
                        elif t4 == b"mdhd":
                            mdhd = j0
                            v = buf[j0 + 8]
                            ts = struct.unpack(">I", buf[j0 + (28 if v == 1 else 20):
                                                        j0 + (32 if v == 1 else 24)])[0]
                    if hdlr == b"soun" and ts:
                        return tid, ts, mdhd
    return None, None, None


def opus_duration(toc, nxt):
    """按 TOC 算真实采样数（48k）。"""
    per = FRAME_SAMPLES[toc >> 3]
    code = toc & 3
    if code == 0:
        return per
    if code in (1, 2):
        return per * 2
    return per * (nxt & 0x3F)          # code 3：帧数在下一字节低 6 位


def retime(src, dst=None):
    """就地（或另存）重排音轨，返回 (包数, 秒数)。"""
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
            if not (tfhd and trun):
                continue
            tflags = full_flags(buf, tfhd)
            q = tfhd + 12
            if tflags & 0x01:
                q += 8                      # base_data_offset
            if struct.unpack(">I", buf[q:q + 4])[0] != tid:
                continue

            f = full_flags(buf, trun)
            cnt = struct.unpack(">I", buf[trun + 12:trun + 16])[0]
            p = trun + 16
            data_off = 0
            if f & 0x01:
                data_off = struct.unpack(">i", buf[p:p + 4])[0]
                p += 4
            if f & 0x04:
                p += 4
            d_step = 4 if f & 0x100 else 0
            s_step = 4 if f & 0x200 else 0
            step = d_step + s_step + (4 if f & 0x400 else 0) + (4 if f & 0x800 else 0)
            if not (d_step and s_step):
                continue

            base = a + data_off          # data_offset 相对 moof 起点
            if tfdt is not None:
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
                struct.pack_into(">I", buf, q, n_units)
                cursor += n_units
                fixed += 1
                off += size

    if mdhd:
        v = buf[mdhd + 8]
        if v == 1:
            struct.pack_into(">Q", buf, mdhd + 32, cursor)
        else:
            struct.pack_into(">I", buf, mdhd + 24, cursor)

    moov = next((x for x in top_boxes(buf) if x[0] == b"moov"), None)
    if moov:
        mv = buf.find(b"mvhd", moov[1], moov[2]) - 4      # find 命中的是 type，往前 4 字节才是 box 起点
        if mv > 0:
            v = buf[mv + 8]
            mts = struct.unpack(">I", buf[mv + (28 if v == 1 else 20):
                                          mv + (32 if v == 1 else 24)])[0] or 1000
            dur = int(cursor / (ts or 48000) * mts)
            if v == 1:
                struct.pack_into(">Q", buf, mv + 32, dur)
            else:
                struct.pack_into(">I", buf, mv + 24, dur)

    dst.write_bytes(buf)
    return fixed, cursor / (ts or 48000)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法：retime_mp4.py in.mp4 [out.mp4]")
    n, sec = retime(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(f"音轨重排：{n} 个包，{sec:.2f}s")


if __name__ == "__main__":
    main()
