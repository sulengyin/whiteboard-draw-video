#!/usr/bin/env python3
"""配音洗一遍再用：统一 48k / 去直流 / 留 -3dB 余量。

为什么必须做：
  1. 多数 TTS（DubbingX、edge-tts）会把响度顶到 0dBFS，源文件本身就有削波；
  2. 导出走 Opus/AAC 编码，重建波形会在峰值处过冲 0.5~1dB，
     源文件已经 1.0 的话，编码后必然超过满刻度 → 播放器硬削波，
     听感就是"电流声 / 滋滋声"；
  3. 16k 采样率的配音在 48k AudioContext 里要被浏览器重采样一次，
     统一到 48k 可以少一道不确定的环节。

用法：
  python3 prep_audio.py <工程目录> [--peak -3.0] [--rate 48000]

会就地重写 audio/*.wav（保留 audio/edge-backup/ 原件），
并把 audio/manifest.json 里的 durMs 按新文件更新。
可重复执行：已经是目标采样率就只做电平处理。
"""
import argparse
import json
import math
import os
import struct
import wave
from array import array

TAPS_PER_PHASE = 60          # 每相 60 抽头；8k 以上压到 -90dB，听不到镜像
HPF_HZ = 35.0                # 去直流 / 低频隆隆声
FADE_MS = 8.0                # 首尾淡入淡出，防止播放器在边界爆音


def read_wav(path):
    with wave.open(path, 'rb') as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f'{path}: 只支持 16bit PCM')
        ch, sr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    data = array('h')
    data.frombytes(raw)
    if ch > 1:                       # 配音应当是单声道；双声道就混成单声道
        mono = array('h', bytes(2 * (len(data) // ch)))
        for i in range(len(mono)):
            mono[i] = sum(data[i * ch + c] for c in range(ch)) // ch
        data = mono
    return data, sr


def write_wav(path, data, sr):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def _i0(x):
    """零阶第一类修正贝塞尔函数（Kaiser 窗用，老 Python 没有 math.i0）"""
    s, t = 1.0, 1.0
    for k in range(1, 40):
        t *= (x / (2 * k)) ** 2
        s += t
    return s


def design_polyphase(rate_in, rate_out, taps=TAPS_PER_PHASE):
    """返回 (L, M, phases)；phases[p][k] 是第 p 相的滤波系数。"""
    g = math.gcd(rate_in, rate_out)
    L, M = rate_out // g, rate_in // g          # 上采样 L 倍、下采样 M 倍
    fs_mid = rate_in * L                        # 零插值后的采样率
    # 归一化截止频率（相对 fs_mid），留 8% 过渡带
    fc = 0.5 * min(1.0 / L, 1.0 / M) * 0.90
    n_taps = taps * L
    mid = (n_taps - 1) / 2.0
    beta = 9.0
    den = _i0(beta)
    h = []
    for i in range(n_taps):
        x = i - mid
        s = 2 * fc if x == 0 else math.sin(2 * math.pi * fc * x) / (math.pi * x)
        r = 2 * i / (n_taps - 1) - 1
        w = _i0(beta * math.sqrt(max(0.0, 1 - r * r))) / den
        h.append(s * w)
    # 整体归一化（不能逐相归一化：会破坏线性相位，反而产生镜像）
    scale = L / sum(h)
    h = [c * scale for c in h]
    phases = [h[p::L] for p in range(L)]
    return L, M, phases, int(mid)


def resample(data, rate_in, rate_out):
    if rate_in == rate_out:
        return data
    L, M, phases, mid = design_polyphase(rate_in, rate_out)
    n_out = len(data) * L // M
    out = array('h', bytes(2 * n_out))
    taps = len(phases[0])
    n_in = len(data)
    # 以输出样点为单位推进；+mid 抵消滤波器的群延迟，保证与字幕对齐
    for n in range(n_out):
        pos = n * M + mid
        base = pos // L
        coeffs = phases[pos - base * L]
        acc = 0.0
        for k in range(taps):
            idx = base - k
            if 0 <= idx < n_in:
                acc += data[idx] * coeffs[k]
        v = acc
        out[n] = -32768 if v < -32768 else 32767 if v > 32767 else int(v)
    return out


def dc_block(data, sr):
    """一阶高通，去掉直流与 35Hz 以下的隆隆声。"""
    if not len(data):
        return data
    a = math.exp(-2 * math.pi * HPF_HZ / sr)
    out = array('h', bytes(2 * len(data)))
    prev_x = prev_y = 0.0
    for i, x in enumerate(data):
        y = a * (prev_y + x - prev_x)
        prev_x, prev_y = x, y
        out[i] = -32768 if y < -32768 else 32767 if y > 32767 else int(y)
    return out


def normalize(data, peak_dbfs):
    peak = max((abs(v) for v in data), default=0)
    if not peak:
        return data, 0.0
    target = 32767.0 * (10 ** (peak_dbfs / 20.0))
    g = target / peak
    out = array('h', bytes(2 * len(data)))
    for i, v in enumerate(data):
        x = v * g
        out[i] = -32768 if x < -32768 else 32767 if x > 32767 else int(x)
    return out, 20 * math.log10(peak / 32767.0)


def fade_edges(data, sr):
    n = int(sr * FADE_MS / 1000)
    if len(data) < 4 * n or n < 2:
        return data
    for i in range(n):
        k = i / n
        data[i] = int(data[i] * k)
        data[-1 - i] = int(data[-1 - i] * k)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('--peak', type=float, default=-3.0, help='目标峰值 dBFS，默认 -3')
    ap.add_argument('--rate', type=int, default=48000)
    args = ap.parse_args()

    audio_dir = os.path.join(args.project, 'audio')
    if not os.path.isdir(audio_dir):
        raise SystemExit(f'没找到 {audio_dir}')

    files = sorted(f for f in os.listdir(audio_dir)
                   if f.lower().endswith('.wav') and os.path.isfile(os.path.join(audio_dir, f)))
    if not files:
        raise SystemExit('audio/ 里没有 wav')

    manifest_path = os.path.join(audio_dir, 'manifest.json')
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding='utf-8') as fh:
            manifest = json.load(fh)

    print(f'{"文件":<10}{"原始":>14}{"处理后":>16}{"峰值":>10}')
    for name in files:
        path = os.path.join(audio_dir, name)
        data, sr = read_wav(path)
        dur0 = len(data) / sr
        data = resample(data, sr, args.rate)
        data = dc_block(data, args.rate)
        data, before_db = normalize(data, args.peak)
        data = fade_edges(data, args.rate)
        write_wav(path, data, args.rate)
        dur1 = len(data) / args.rate
        print(f'{name:<10}{sr:>6}Hz {dur0:>5.1f}s{args.rate:>7}Hz {dur1:>5.1f}s'
              f'{before_db:>9.1f}dB')
        key = os.path.splitext(name)[0]
        if key in manifest and isinstance(manifest[key], dict):
            manifest[key]['durMs'] = int(round(dur1 * 1000))
        else:
            manifest[key] = {'file': name, 'durMs': int(round(dur1 * 1000))}

    with open(manifest_path, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    print(f'\n已统一到 {args.rate}Hz / 峰值 {args.peak:g}dBFS，manifest.json 时长已更新')


if __name__ == '__main__':
    main()
