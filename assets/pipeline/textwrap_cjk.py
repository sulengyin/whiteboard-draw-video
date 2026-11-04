#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中日韩文本断行：按显示宽度折行，并遵守「标点不单独成行」的排版规则。

规则（简化的避头尾）：
- 行首不允许出现收尾类标点：，。、；：？！）】》」』”’…—·% 等；
- 行尾不允许出现起首类标点：（【《「『“‘ 等；
- 任何一行都不能只由标点组成。
"""
from __future__ import annotations

from typing import Iterable, List

# 不能出现在行首（避头）
NO_LINE_START = "，。、；：？！）〕】》」』”’…·%,.;:?!)]}>"
# 不能出现在行尾（避尾）
NO_LINE_END = "（〔【《「『“‘([{<"


def char_w(ch: str) -> float:
    """粗略显示宽度：全角 1，半角 0.5。"""
    if ch == "\u3000":
        return 1.0
    code = ord(ch)
    if code < 0x2E80:
        return 0.5
    return 1.0


def width(text: str) -> float:
    return sum(char_w(c) for c in text)


def is_punct_only(line: str) -> bool:
    return bool(line) and all(c in NO_LINE_START + NO_LINE_END + "—-·" for c in line)


def _cut_index(text: str, start: int, max_w: float) -> int:
    """从 start 起尽量填满 max_w，返回断点下标。"""
    w, j = 0.0, start
    while j < len(text) and w + char_w(text[j]) <= max_w:
        w += char_w(text[j])
        j += 1
    # 至少要断出一个字符，避免死循环
    if j <= start:
        j = start + 1

    # 优先在标点后断行：一行塞满再硬断会把词语劈开，读起来别扭
    floor = start + max(1, int(max_w * 0.55))
    for k in range(j - 1, floor - 1, -1):
        if text[k] in "，。、；：？！":
            j = k + 1
            break

    # 避头：下一行不以收尾标点开头
    while j < len(text) and j - 1 > start and text[j] in NO_LINE_START:
        j -= 1
    # 避尾：本行不以起首标点结尾
    while j < len(text) and j - 1 > start and text[j - 1] in NO_LINE_END:
        j += 1
    return j


def wrap(text: str, max_w: float = 20) -> List[str]:
    """把一段话折成若干行，遵守避头尾规则。"""
    text = text.replace("\r", "").strip()
    lines: List[str] = []
    i = 0
    while i < len(text):
        if width(text[i:]) <= max_w:
            lines.append(text[i:])
            break
        j = _cut_index(text, i, max_w)
        line = text[i:j].rstrip()
        # 兜底：整行只有标点时，把前一行最后一个字拖下来一起走
        if is_punct_only(line) and lines:
            line = (lines.pop()[-1] + line).strip()
            if lines and is_punct_only(lines[-1]):
                pass
        lines.append(line)
        i = j
    # 清掉可能出现的纯标点空行
    lines = [ln for ln in lines if ln and not is_punct_only(ln)]
    return lines


def cue_lines(text: str, max_w: float = 20, max_lines: int = 2) -> List[List[str]]:
    """把一段话切成若干条字幕，每条最多 max_lines 行。"""
    lines = wrap(text, max_w)
    cues: List[List[str]] = []
    for k in range(0, len(lines), max_lines):
        cues.append(lines[k:k + max_lines])
    return cues


def check(lines: Iterable[str]) -> List[str]:
    """返回违规行说明；空列表表示全部合规。"""
    bad: List[str] = []
    for ln in lines:
        if not ln.strip():
            bad.append("空行")
            continue
        if is_punct_only(ln):
            bad.append(f"整行都是标点：{ln}")
        if ln[0] in NO_LINE_START:
            bad.append(f"行首是标点：{ln}")
        if ln[-1] in NO_LINE_END:
            bad.append(f"行尾是标点：{ln}")
    return bad


if __name__ == "__main__":
    demo = "你有没有遇到过这种情况：昨天还能访问的地址，今天突然就打不开了。一查，本机的 IP 从 192.168.11.28 变成了 192.168.3.158。"
    ls = wrap(demo, 20)
    for line in ls:
        print(f"|{line}|")
    print("问题：", check(ls) or "无")
