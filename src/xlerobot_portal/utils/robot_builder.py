"""Shared XLerobot construction helper used by the robot and diagnose commands."""
from __future__ import annotations

import os

from lerobot.cameras import Cv2Rotation
from lerobot.cameras.opencv import OpenCVCameraConfig
from lerobot.robots.xlerobot import XLerobot, XLerobotConfig

from .common import (
    env_camera_id,
    env_bool,
    env_optional_float,
    env_positive_int,
    env_str,
)

DEFAULT_CAMERA_WIDTH = 640
DEFAULT_CAMERA_HEIGHT = 480
ALL_CAMERAS = ("left_wrist", "right_wrist", "head")


def _active_robot_cameras() -> tuple[str, ...]:
    """Return the cameras to open on the robot side.

    Reads ``XLEROBOT_CAMERAS`` (comma-separated). Unset or empty = all three.
    Unknown names are silently ignored so a typo doesn't hard-crash.
    """
    raw = os.environ.get("XLEROBOT_CAMERAS", "").strip()
    if not raw:
        return ALL_CAMERAS
    chosen = tuple(n.strip() for n in raw.split(",") if n.strip() in ALL_CAMERAS)
    if not chosen:
        print(f"[robot] XLEROBOT_CAMERAS={raw!r} contained no valid names — using all cameras")
        return ALL_CAMERAS
    skipped = [n for n in ALL_CAMERAS if n not in chosen]
    if skipped:
        print(f"[robot] cameras disabled via XLEROBOT_CAMERAS: {', '.join(skipped)}")
    return chosen


def _build_camera_cfg(fps: int) -> dict:
    width = env_positive_int("XLEROBOT_CAM_WIDTH", DEFAULT_CAMERA_WIDTH)
    height = env_positive_int("XLEROBOT_CAM_HEIGHT", DEFAULT_CAMERA_HEIGHT)
    active = _active_robot_cameras()

    cam_defs = {
        "left_wrist": ("XLEROBOT_CAM_LEFT_WRIST", "/dev/video0"),
        "right_wrist": ("XLEROBOT_CAM_RIGHT_WRIST", "/dev/video2"),
        "head": ("XLEROBOT_CAM_HEAD", "/dev/video4"),
    }
    cfg: dict = {}
    for name, (env_var, default) in cam_defs.items():
        if name not in active:
            continue
        raw = os.environ.get(env_var, default)
        if not raw:
            print(f"[robot] {name}: skipped ({env_var} is empty)")
            continue
        cfg[name] = OpenCVCameraConfig(
            index_or_path=env_camera_id(env_var, default),
            fps=fps,
            width=width,
            height=height,
            rotation=Cv2Rotation.NO_ROTATION,
            fourcc="MJPG",
        )
    return cfg


def build_robot(*, with_safety_clamp: bool = True) -> XLerobot:
    """Construct an :class:`XLerobot` from environment variables.

    Args:
        with_safety_clamp: When True, applies ``XLEROBOT_MAX_RELATIVE_TARGET``
            to cap per-tick motor travel (used by the robot command).
            Set False for read-only diagnostics where no actions are sent.
    """
    fps = env_positive_int("PORTAL_FPS", 30)
    cam_cfg = _build_camera_cfg(fps)

    kwargs: dict = dict(
        id=env_str("XLEROBOT_ID", "xlerobot"),
        port1=env_str("XLEROBOT_PORT1", "/dev/ttyACM0"),
        port2=env_str("XLEROBOT_PORT2", "/dev/ttyACM1"),
        cameras=cam_cfg,
    )
    if with_safety_clamp:
        kwargs["max_relative_target"] = env_optional_float("XLEROBOT_MAX_RELATIVE_TARGET")

    return XLerobot(XLerobotConfig(**kwargs))
