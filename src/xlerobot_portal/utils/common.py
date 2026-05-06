"""Env loading, token minting, and FPS pacing helpers."""
from __future__ import annotations

import datetime
import logging
import os
import pathlib
import time
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
from livekit import api
from livekit.protocol.room import RoomConfiguration


def load_env() -> None:
    """Load `.env` then `.env.local` from the current working directory."""
    cwd = pathlib.Path.cwd()
    for name, override in ((".env", False), (".env.local", True)):
        p = cwd / name
        if p.exists():
            load_dotenv(p, override=override)


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} must be set (see .env.example). Current working dir: {pathlib.Path.cwd()} — "
            "run from software/xlerobot_portal so .env is picked up."
        )
    return value


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def env_positive_int(name: str, default: int) -> int:
    """Like :func:`env_int` but rejects zero/negative (prevents 0-FPS pacing)."""
    value = env_int(name, default)
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer (got {value!r})")
    return value


def env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw else default


def env_optional_path(name: str) -> str | None:
    """Read an optional path var. Empty string or unset both resolve to None."""
    raw = os.environ.get(name, "").strip()
    return raw or None


def env_camera_id(name: str, default: str) -> int | str:
    """Read a camera identifier env var with cross-platform semantics.

    Returns ``int`` when the value is all digits (macOS AVFoundation index),
    string otherwise (Linux ``/dev/videoN`` path).
    """
    raw = os.environ.get(name, default)
    return int(raw) if raw.isdigit() else raw


def env_optional_float(name: str) -> float | None:
    """Parse an optional float env var. Unset or empty resolves to None."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return float(raw)


def env_bool(name: str, default: bool = False) -> bool:
    """Parse an env var as bool. Accepts 1/true/yes/on (case-insensitive)."""
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def mint_token(identity: str, room: str) -> str:
    """Mint a LiveKit JWT for `identity` in `room` with low-latency playout."""
    key = required_env("LIVEKIT_API_KEY")
    secret = required_env("LIVEKIT_API_SECRET")
    grants = api.VideoGrants(
        room_join=True, room=room, can_publish=True, can_subscribe=True
    )
    room_cfg = RoomConfiguration(name=room, min_playout_delay=0, max_playout_delay=1)
    return (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_grants(grants)
        .with_room_config(room_cfg)
        .with_ttl(datetime.timedelta(hours=6))
        .to_jwt()
    )


@contextmanager
def suppress_clamp_warnings():
    """Silence lerobot's per-tick "Relative goal position magnitude had to
    be clamped to be safe" warnings."""
    class _F(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
            return "Relative goal position magnitude had to be clamped" not in record.getMessage()

    flt = _F()
    root = logging.getLogger()
    root.addFilter(flt)
    try:
        yield
    finally:
        root.removeFilter(flt)


@contextmanager
def safe_disconnect(label: str):
    """Swallow and log exceptions from a disconnect call."""
    try:
        yield
    except Exception:  # pragma: no cover
        logging.getLogger(__name__).exception("error during %s disconnect", label)


def require_path(label: str, path: str | None) -> str:
    """Validate an env-provided filesystem path exists."""
    if not path:
        raise RuntimeError(f"{label} path is empty")
    if not pathlib.Path(path).exists():
        raise RuntimeError(f"{label} path {path!r} does not exist on this machine")
    return path


def pace(fps: int) -> Iterator[int]:
    """Yield tick indices at `fps`. Sleeps between ticks; resets on overrun."""
    interval = 1.0 / fps
    next_tick = time.perf_counter()
    i = 0
    while True:
        yield i
        i += 1
        next_tick += interval
        now = time.perf_counter()
        sleep_for = next_tick - now
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_tick = now
