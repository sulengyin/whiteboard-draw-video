#!/usr/bin/env python3
"""预览台 + 落盘服务（白板视频工作流通用版）。

浏览器内录出来的 Blob 直接 POST 到 /upload，由这里写进 <工程>/out/，
并复制一份到系统「下载」目录 —— 不依赖浏览器下载目录有没有被拦。

    python3 serve.py                 # 默认端口 8000，工程根 = 本文件所在目录
    python3 serve.py 8010
    python3 serve.py 8010 --root ../my-project

约定（工程根下）：
    project.json     画幅/笔宽/导出参数
    playlist.json    scenes[{id,title,image,...}]
    audio/<id>.wav   配音
    out/             导出落盘
"""
import argparse
import json
import os
import re
import shutil
import ssl
import struct
import subprocess
import sys
import urllib.request
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "out"
AUDIO = ROOT / "audio"
BACKUP = AUDIO / "edge-backup"


def scene_ids():
    """从 playlist.json 取幕 id，换工程不用改代码。"""
    try:
        pj = json.loads((ROOT / "playlist.json").read_text(encoding="utf-8"))
        return [s.get("id") or f"scene-{i+1:02d}" for i, s in enumerate(pj["scenes"])]
    except Exception:
        return []


def download_dir():
    """找系统的下载目录。可用 WB_DOWNLOAD_DIR 覆盖，其余按平台惯例探测。"""
    cands = []
    env = os.environ.get("WB_DOWNLOAD_DIR")
    if env:
        cands.append(Path(env).expanduser())
    home = Path.home()
    cands += [home / "Download", home / "Downloads"]
    for p in cands:
        if p.is_dir():
            return p
    return None


def copy_to_download():
    """默认不往 Download 再复制一份——每支片子会白占一倍空间。
    project.json 里写 "copyToDownload": true 才复制。"""
    try:
        cfg = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
        return bool(cfg.get("copyToDownload"))
    except Exception:
        return False


def _top_boxes(data):
    i, n = 0, len(data)
    while i + 8 <= n:
        size = int.from_bytes(data[i:i + 4], "big")
        hdr = 8
        if size == 1:
            size = int.from_bytes(data[i + 8:i + 16], "big")
            hdr = 16
        elif size == 0:
            size = n - i
        if size < hdr or i + size > n:
            return
        yield data[i + 4:i + 8], i, i + size
        i += size


def fix_mp4_duration(path: Path):
    """MediaRecorder 出的是 fragmented MP4，mvhd.duration 恒为 0（播放器显示 0:00）。
    按 mdhd.timescale + 最后一个 tfdt 算出真实时长写回去。"""
    try:
        data = bytearray(path.read_bytes())
        moov = next((b for b in _top_boxes(data) if b[0] == b"moov"), None)
        if not moov:
            return None
        _, m0, m1 = moov
        mv = data.find(b"mvhd", m0, m1)
        if mv < 0:
            return None
        ver = data[mv + 4]
        d_off = mv + 4 + (24 if ver == 1 else 16)
        ts_off = mv + 4 + (20 if ver == 1 else 12)
        if struct.unpack(">Q", data[d_off:d_off + 8])[0] != 0:
            return path.stat().st_size

        scale = {}
        pos = m0
        while True:
            tk = data.find(b"tkhd", pos, m1)
            if tk < 0:
                break
            tv = data[tk + 4]
            tid = struct.unpack(">I", data[tk + 4 + (20 if tv == 1 else 12):
                                       tk + 4 + (24 if tv == 1 else 16)])[0]
            md = data.find(b"mdhd", tk, m1)
            if md > 0:
                dv = data[md + 4]
                sc = struct.unpack(">I", data[md + 4 + (20 if dv == 1 else 12):
                                              md + 4 + (24 if dv == 1 else 16)])[0]
                if sc:
                    scale[tid] = sc
            pos = tk + 8

        last = {}
        for typ, a, b in _top_boxes(data):
            if typ != b"moof":
                continue
            tf = data.find(b"tfhd", a, b)
            td = data.find(b"tfdt", a, b)
            if tf < 0 or td < 0:
                continue
            tid = struct.unpack(">I", data[tf + 8:tf + 12])[0]
            v = data[td + 4]
            t = struct.unpack(">Q", data[td + 8:td + 16])[0] if v == 1 \
                else struct.unpack(">I", data[td + 8:td + 12])[0]
            if t > last.get(tid, 0):
                last[tid] = t

        secs = max((t / scale.get(tid, 1) for tid, t in last.items()), default=0)
        if secs <= 0:
            return None
        mts = struct.unpack(">I", data[ts_off:ts_off + 4])[0] or 1000
        struct.pack_into(">Q", data, d_off, int(secs * mts))
        path.write_bytes(data)
        print(f"[mp4] duration → {secs:.1f}s", flush=True)
        return path.stat().st_size
    except Exception as e:
        print(f"[mp4] 跳过时长修复：{e}", flush=True)
        return None


def safe_name(raw: str) -> str:
    name = os.path.basename(raw or "").strip()
    name = re.sub(r"[^\w.\-]+", "_", name)
    if not name or name.startswith("."):
        name = "movie.webm"
    return name


def wav_seconds(path: Path):
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


def fetch_remote(url: str, timeout=180):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read(), (r.headers.get("Content-Type") or "")


def node_bin():
    for cand in (os.environ.get("NODE"), "/data/storage/el1/bundle/libs/arm64/node/bin/node"):
        if cand and Path(cand).is_file():
            return cand
    return shutil.which("node") or "node"


def mp3_to_wav(mp3: Path, wav: Path):
    """DubbingX 有时只给 mp3；本机没有 ffmpeg，借用 mpg123-decoder。"""
    script = Path(__file__).resolve().parent / "mp3_to_wav.js"
    subprocess.run([node_bin(), str(script), str(mp3), str(wav)], check=True,
                   timeout=300, capture_output=True)


def normalize_wav(path: Path, rate: int = 16000):
    """统一成 16k / 16bit / 单声道：对齐脚本用 array('h') 读，必须 mono。"""
    import audioop
    with wave.open(str(path)) as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raw = audioop.lin2lin(raw, sw, 2)
    if ch == 2:
        raw = audioop.tomono(raw, 2, 0.5, 0.5)
    if sr != rate:
        raw, _ = audioop.ratecv(raw, 2, 1, sr, rate, None)
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(raw)
    tmp.replace(path)
    return len(raw) / 2 / rate


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/audio_status"):
            ids = set(scene_ids())
            items = []
            for p in sorted(AUDIO.glob("*.wav")):
                if ids and p.stem not in ids:
                    continue
                items.append({"file": p.name, "id": p.stem,
                              "sec": wav_seconds(p), "size": p.stat().st_size})
            self._json({"items": items})
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/fetch_audio"):
            self._fetch_audio()
            return
        if not self.path.startswith("/upload"):
            self.send_error(404, "only /upload")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_error(400, "bad length")
            return
        if n <= 0:
            self.send_error(400, "empty body")
            return

        name = safe_name(self.headers.get("X-Filename", ""))
        OUT.mkdir(exist_ok=True)
        dst = OUT / name
        k = 1
        while dst.exists():
            dst = OUT / f"{Path(name).stem}-{k}{Path(name).suffix}"
            k += 1

        left = n
        with dst.open("wb") as f:
            while left > 0:
                buf = self.rfile.read(min(left, 1 << 20))
                if not buf:
                    break
                f.write(buf)
                left -= len(buf)

        size = dst.stat().st_size
        dl = None
        if dst.suffix.lower() in (".mp4", ".webm", ".mov"):
            fix_mp4_duration(dst)
            d = download_dir() if copy_to_download() else None
            if d:
                try:
                    target = d / dst.name
                    shutil.copy2(dst, target)
                    dl = str(target)
                except Exception as e:
                    print(f"[copy] 失败 {e}", flush=True)

        self._json({"path": str(dst), "name": dst.name, "size": size, "download": dl})
        print(f"[saved] {dst}  {size/1048576:.1f} MB", flush=True)

    def _fetch_audio(self):
        """配音页生成好后，把 DubbingX 的音频地址交给这里落盘并统一格式。"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
        except Exception:
            self._json({"ok": False, "msg": "bad body"}, 400)
            return

        url = (payload.get("url") or "").strip()
        ids = scene_ids()
        sid = (payload.get("id") or "").strip()
        if not sid:
            try:
                idx = int(payload.get("scene"))
                sid = ids[idx - 1] if 0 < idx <= len(ids) else ""
            except Exception:
                sid = ""
        if not url or not sid:
            self._json({"ok": False, "msg": "need url and scene/id"}, 400)
            return

        AUDIO.mkdir(exist_ok=True)
        dst = AUDIO / f"{sid}.wav"
        tmp = AUDIO / f".dl-{sid}.bin"
        try:
            raw, ctype = fetch_remote(url)
            if len(raw) < 2000:
                raise RuntimeError(f"音频太小（{len(raw)} 字节）")
            tmp.write_bytes(raw)
            if not raw.startswith(b"RIFF"):
                mp3_to_wav(tmp, tmp.with_suffix(".wav"))
                tmp.unlink(missing_ok=True)
                tmp = tmp.with_suffix(".wav")
            sec = normalize_wav(tmp)

            BACKUP.mkdir(parents=True, exist_ok=True)
            if dst.exists() and not (BACKUP / dst.name).exists():
                shutil.copy2(dst, BACKUP / dst.name)
            tmp.replace(dst)

            mf_path = AUDIO / "manifest.json"
            mf = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.exists() else {}
            old = mf.get(sid, {})
            mf[sid] = {"file": dst.name, "durMs": int(sec * 1000),
                       "chars": old.get("chars", 0), "voice": payload.get("voice") or "DubbingX"}
            mf_path.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[voice] {dst.name} {sec:.1f}s <- {ctype}", flush=True)
            self._json({"ok": True, "sec": round(sec, 2), "bytes": dst.stat().st_size})
        except Exception as e:
            print(f"[voice] 失败 {e}", flush=True)
            self._json({"ok": False, "msg": str(e)}, 500)
        finally:
            for p in (tmp, tmp.with_suffix(".wav")):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and "/upload" in str(args[0]):
            return
        super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=8000)
    ap.add_argument("--root", default=None, help="工程根目录（默认本文件所在目录）")
    a = ap.parse_args()

    global ROOT, OUT, AUDIO, BACKUP
    ROOT = Path(a.root).resolve() if a.root else Path(__file__).resolve().parent
    OUT = ROOT / "out"
    AUDIO = ROOT / "audio"
    BACKUP = AUDIO / "edge-backup"

    OUT.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"serving {ROOT}")
    print(f"open  http://127.0.0.1:{a.port}/index.html")
    print(f"save  {OUT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
