#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""幕解析：pipeline 里所有脚本共用，别再硬编码 scene-01。

幕 id 来自 playlist.json 的 scenes[].id，图片名、路径、音频、字幕、cues 全按同一 id 派生。
工程的 build/ 目录下运行，所以 PROJ = build/ 的上一级。
"""
from __future__ import annotations

import json
from pathlib import Path

BUILD = Path(__file__).resolve().parent
PROJ = BUILD.parent


def playlist(proj: Path = PROJ) -> dict:
    return json.loads((proj / "playlist.json").read_text(encoding="utf-8"))


def scenes(proj: Path = PROJ) -> list:
    return playlist(proj)["scenes"]


def ids(proj: Path = PROJ) -> list:
    return [s["id"] for s in scenes(proj)]


def tts_path(proj: Path, sid: str) -> Path:
    return proj / "srt" / "tts" / f"{sid}.txt"


def audio_path(proj: Path, sid: str) -> Path:
    return proj / "audio" / f"{sid}.wav"


def cues_path(proj: Path, sid: str) -> Path:
    return proj / "cues" / f"{sid}.json"


def slug(proj: Path = PROJ) -> str:
    try:
        cfg = json.loads((proj / "project.json").read_text(encoding="utf-8"))
        return cfg.get("slug") or "movie"
    except Exception:
        return "movie"
