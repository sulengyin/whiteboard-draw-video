#!/usr/bin/env python3
"""路径生成冒烟测试。

拿几张真实插画跑一遍 trace_paths，断言吻合率不低于阈值。
改动笔迹算法、阈值、笔宽之后跑一次，能立刻知道有没有把别的图搞崩。

    python3 tests/smoke.py --images ~/videos/xxx/scenes
    python3 tests/smoke.py --images ./pics --min-iou 99.5

图片不进仓库——用上一次工程的 scenes 目录即可。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_node() -> str:
    for c in (os.environ.get("WB_NODE"), shutil.which("node")):
        if c and Path(c).exists():
            return c
    sys.exit("找不到 node，可用 WB_NODE=/path/to/node 指定")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True, help="含插画的目录")
    ap.add_argument("--min-iou", type=float, default=99.0, help="最低吻合率，默认 99")
    ap.add_argument("--keep", action="store_true", help="保留临时产物")
    a = ap.parse_args()

    src = Path(a.images).expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"目录不存在：{src}")
    imgs = sorted(p for p in src.iterdir()
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not imgs:
        sys.exit(f"{src} 里没有 jpg/png")

    tmp = Path(tempfile.mkdtemp(prefix="wb-smoke-"))
    try:
        cmd = [find_node(), str(ROOT / "scripts" / "trace_paths.js"),
               "--in", str(src), "--out", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:])
            print(r.stderr[-2000:], file=sys.stderr)
            return 1

        report = json.loads((tmp / "_report.json").read_text(encoding="utf-8"))
        rows = report["files"]
        bad = [f for f in rows if f["iou"] < a.min_iou]

        print(f"{'图':<16}{'尺寸':>12}{'图形':>6}{'点数':>8}{'吻合':>9}")
        for f in rows:
            mark = "  " if f["iou"] >= a.min_iou else "✗ "
            print(f"{mark}{f['file']:<14}{f['w']}×{f['h']:<7}"
                  f"{f['regions']:>6}{f['points']:>8}{f['iou']:>8.2f}%")

        if bad:
            print(f"\n{len(bad)} 张低于 {a.min_iou}%："
                  + "、".join(f"{f['file']} {f['iou']:.2f}%" for f in bad))
            return 1
        print(f"\n全部通过（阈值 {a.min_iou}%）。")
        return 0
    finally:
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
