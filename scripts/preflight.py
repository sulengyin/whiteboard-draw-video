#!/usr/bin/env python3
"""提交前自检：敏感信息、本机绝对路径、大文件、语法。

    python3 scripts/preflight.py            # 检查所有已跟踪文件
    python3 scripts/preflight.py --quiet    # 只输出结论
    python3 scripts/preflight.py --min-iou 99

退出码 0 = 可以提交（可能有警告），1 = 有必须处理的问题。
"""

import argparse
import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"node_modules", ".git", "__pycache__"}
TEXT_EXT = {
    ".py", ".js", ".json", ".md", ".html", ".htm", ".css",
    ".txt", ".yml", ".yaml", ".toml", ".sh", ".cfg", ".ini",
}
BIG_FILE = 1_000_000  # 1 MB

PATTERNS = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("OpenAI 风格密钥", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("GitHub 令牌", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("AWS 密钥", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Bearer 令牌", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("私钥", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("本机绝对路径", re.compile(r"/storage/Users/[^/\s\"']+|/home/[a-z][a-z0-9_-]*/")),
]

# 这些行是文档里举例说明「不要这样做」，允许出现
ALLOW_LINE = re.compile(r"preflight|示例|反例|不要这样做")


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [f for f in out.split("\0") if f]


def skip(rel: str) -> bool:
    return any(part in SKIP_DIRS for part in Path(rel).parts)


def human(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.0f} KB"


def scan_text(path: Path, rel: str):
    """返回 (敏感命中, 路径命中)。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return [], []

    hits_s, hits_p = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW_LINE.search(line):
            continue
        for name, pat in PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            item = (rel, i, name, m.group(0)[:60])
            (hits_p if name == "本机绝对路径" else hits_s).append(item)
    return hits_s, hits_p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--min-iou", type=float, default=0,
                    help="兼容参数，本脚本不做路径吻合率检查，见 tests/smoke.py")
    a = ap.parse_args()

    files = [f for f in tracked_files() if not skip(f)]
    secrets, paths, big, syntax_err, py_count = [], [], [], [], 0

    for rel in files:
        p = ROOT / rel
        if not p.is_file():
            continue
        size = p.stat().st_size
        if size > BIG_FILE:
            big.append((rel, size))
        if p.suffix.lower() not in TEXT_EXT:
            continue
        s, pa = scan_text(p, rel)
        secrets += s
        paths += pa
        if p.suffix == ".py":
            py_count += 1
            try:
                ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError as e:
                syntax_err.append((rel, e.lineno, e.msg))

    problems = len(secrets) + len(paths) + len(syntax_err)
    if not a.quiet:
        print(f"检查 {len(files)} 个已跟踪文件\n")

        def block(title, items, fmt, level):
            mark = ("✗" if level == "fail" else "!") if items else "✓"
            print(f"{mark} {title}")
            if items:
                for it in items[:12]:
                    print("    " + fmt(it))
                if len(items) > 12:
                    print(f"    …… 另有 {len(items) - 12} 条")

        block("敏感信息", secrets, lambda t: f"{t[0]}:{t[1]}  {t[2]}  {t[3]}", "fail")
        block("本机绝对路径", paths, lambda t: f"{t[0]}:{t[1]}  {t[3]}", "fail")
        block("大文件（>1 MB）", big, lambda t: f"{t[0]}  {human(t[1])}", "warn")
        block("Python 语法", syntax_err, lambda t: f"{t[0]}:{t[1]}  {t[2]}", "fail")
        print(f"    （{py_count} 个 .py 文件）")

    if problems:
        print(f"\n有 {problems} 处需要处理，处理后重新运行。")
        return 1
    print("\n可以提交。" if not a.quiet else "可以提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
