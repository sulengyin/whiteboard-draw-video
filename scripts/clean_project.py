#!/usr/bin/env python3
"""清理白板工程占用的磁盘，按「可重算 / 需重做 / 唯一产物」分级。

    python3 clean_project.py --root .                    # 只看占用明细
    python3 clean_project.py --root . --level 1 --dry-run
    python3 clean_project.py --root . --level 1          # 清缓存，秒级可重建
    python3 clean_project.py --root . --level 2          # 定稿后：连配音/字幕中间件一起清
    python3 clean_project.py --root . --level 3 --archive ~/Movies/白板视频   # 归档成片并删工程

各级删什么：
    L1  预览帧、paths/preview 调试图、out/ 里的旧版本（保留最新 N 个）、audio/edge-backup、__pycache__
    L2  L1 + 配音 wav、cues、srt 成品、paths/*.json（都能从 srt/tts 原稿和 scenes/ 重建，但要重跑几轮流程）
    L3  把最新成片移到 --archive，然后删掉整个工程目录

L1 之后工程仍可直接重录；L2 之后要重做配音和字幕对齐；L3 之后只剩成片。
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def du(p: Path) -> int:
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}G"


def newest_movies(root: Path):
    out = root / "out"
    if not out.is_dir():
        return []
    vids = [p for p in out.iterdir()
            if p.is_file() and p.suffix.lower() in (".mp4", ".webm", ".mov")]
    return sorted(vids, key=lambda p: p.stat().st_mtime, reverse=True)


def collect(root: Path, level: int, keep: int):
    """返回 [(路径, 字节, 说明)]。"""
    items = []

    def add(p: Path, why: str):
        if p.exists():
            items.append((p, du(p), why))

    if level >= 1:
        for d in ("preview", "paths/preview"):
            add(root / d, "预览帧（截帧可重出）")
        for name in ("__pycache__", ".DS_Store"):
            for p in root.rglob(name):
                add(p, "系统/编译缓存")
        for p in root.rglob("*.log"):
            add(p, "日志")
        add(root / "audio" / "edge-backup", "旧 TTS 备份音频")

        vids = newest_movies(root)
        for p in vids[max(keep, 0):]:
            add(p, "旧版成片（保留最新 %d 个）" % keep)

        # 早期在鸿蒙上试过 ffmpeg 静态包，跑不起来还占几十 M
        for p in (root / "tools").glob("ffmpeg*") if (root / "tools").is_dir() else []:
            if p.is_file() and p.stat().st_size > 5 * 1024 * 1024:
                add(p, "遗留 ffmpeg 二进制（此处不可用）")

    if level >= 2:
        audio = root / "audio"
        if audio.is_dir():
            for p in audio.glob("*.wav"):
                add(p, "配音（重做需登录配音页）")
            add(audio / "manifest.json", "配音清单")
        add(root / "cues", "字幕切块")
        if (root / "srt").is_dir():
            for p in (root / "srt").glob("*.srt"):
                add(p, "字幕成品")
            add(root / "srt" / "tts" / "spoken", "发音稿（可从原稿重算）")
        if (root / "paths").is_dir():
            for p in (root / "paths").glob("*.json"):
                add(p, "笔迹路径（可秒级重算）")

    return items


def report(root: Path, level: int, keep: int, dry: bool, archive: Path | None):
    total_before = du(root)
    print(f"工程：{root}")
    print(f"当前占用：{human(total_before)}")
    print()

    if level >= 3:
        vids = newest_movies(root)
        if not vids:
            sys.exit("out/ 里没有成片，拒绝删除工程")
        latest = vids[0]
        print(f"成片：{latest.name}  {human(du(latest))}")
        if not archive:
            sys.exit("L3 必须给 --archive <目录>，先把成片挪出去再删工程")
        if dry:
            print(f"[dry] 将 {latest} → {archive / latest.name}")
            print(f"[dry] 将删除整个 {root}，释放 {human(total_before)}")
            return
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / latest.name
        if target.exists() and target.stat().st_size == latest.stat().st_size:
            latest.unlink()
            print(f"归档位置已有同尺寸文件，直接删本地：{target}")
        else:
            shutil.move(str(latest), str(target))
            print(f"成片已归档：{target}")
        shutil.rmtree(root)
        print(f"工程已删除，释放 {human(total_before - du(target))}（成片留在归档目录）")
        return

    items = collect(root, level, keep)
    if not items:
        print("没有可清理的内容。")
        return

    freed = 0
    for p, size, why in items:
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        print(f"  {'[dry] ' if dry else ''}{rel:<28} {human(size):>8}  {why}")
        freed += size
        if not dry:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except OSError as e:
                print(f"    跳过：{e}")
                freed -= size

    left = du(root) - freed if dry else du(root)
    print()
    verb = "可释放" if dry else "已释放"
    print(f"{verb}：{human(freed)}")
    print(f"{'清后约' if dry else '现在'}：{human(max(left, 0))}")
    if level == 1:
        print("提示：定稿不再改后跑 --level 2 可再省配音与中间件；只想留成片就 --level 3 --archive <目录>。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="工程根目录")
    ap.add_argument("--level", type=int, choices=(1, 2, 3), default=0,
                    help="清理级别；不给只显示明细")
    ap.add_argument("--keep", type=int, default=1, help="out/ 里保留几个成片（默认 1）")
    ap.add_argument("--archive", default=None, help="L3 的成片归档目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不删")
    a = ap.parse_args()

    root = Path(a.root).expanduser().resolve()
    if not (root / "project.json").exists() and not (root / "playlist.json").exists():
        sys.exit(f"{root} 不像白板工程（project.json / playlist.json 都没有）")

    if a.level == 0:
        print(f"工程：{root}\n总占用：{human(du(root))}\n")
        cl = {"preview": "预览帧", "paths": "笔迹路径", "audio": "配音",
              "cues": "字幕切块", "srt": "字幕", "out": "成片", "scenes": "原图",
              "assets": "素材", "build": "脚本"}
        for p in sorted(root.iterdir(), key=lambda x: -du(x)):
            if p.name.startswith("."):
                continue
            tag = cl.get(p.name, "")
            print(f"  {p.name:<12} {human(du(p)):>8}  {tag}")
        print("\n想看会删什么：--level 1 --dry-run")
        return

    report(root, a.level, a.keep, a.dry_run, Path(a.archive).expanduser() if a.archive else None)


if __name__ == "__main__":
    main()
