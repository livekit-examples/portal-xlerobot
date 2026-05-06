"""Read-only robot diagnostic: shows live joint state in the terminal.

When LIVEKIT_URL and LIVEKIT_ROOM are set, also connects to a LiveKit
Portal session and publishes observations (joint state + camera frames)
so a remote operator can watch. Arms are torqued off — nothing is ever
commanded to the motors.

Usage::

    uv run xlerobot-portal-diagnose              # local-only, no Portal
    uv run xlerobot-portal-diagnose --fps 10     # faster terminal refresh
    uv run xlerobot-portal-diagnose --once       # single snapshot then exit
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from ..utils.common import (
    env_positive_int,
    load_env,
    mint_token,
    pace,
    required_env,
    safe_disconnect,
)
from ..utils.robot_builder import build_robot

IDENTITY = "xlerobot-diagnose"
BAR_WIDTH = 32
logger = logging.getLogger(__name__)


def _bar(value: float, lo: float, hi: float, width: int = BAR_WIDTH) -> str:
    span = hi - lo
    if span <= 0:
        return "[" + "?" * width + "]"
    frac = max(0.0, min(1.0, (value - lo) / span))
    pos = int(round(frac * (width - 1)))
    return "[" + "-" * pos + "|" + "-" * (width - 1 - pos) + "]"


def _print_state(obs: dict, joint_motors: list[str], first: bool, once: bool) -> None:
    if not once and not first:
        sys.stdout.write("\033[H\033[J")

    print("[diagnose] torque OFF — hand-move joints to watch values change")
    print(f"  {'motor':<36s} {'pos':>8s}   {'bar':<34s}")
    print("  " + "─" * 82)
    for m in joint_motors:
        key = f"{m}.pos"
        val = obs.get(key)
        if val is None:
            print(f"  {m:<36s} {'N/A':>8s}")
            continue
        is_gripper = m.endswith("gripper")
        lo_b, hi_b = (0.0, 100.0) if is_gripper else (-100.0, 100.0)
        bar = _bar(val, lo_b, hi_b)
        marker = "" if lo_b + 1 < val < hi_b - 1 else "  ← at/near limit"
        print(f"  {m:<36s} {val:+7.2f}   {bar}{marker}")

    print()
    print("  base velocity (body frame):")
    for k, unit in (("x.vel", "m/s"), ("y.vel", "m/s"), ("theta.vel", "deg/s")):
        v = obs.get(k, 0.0)
        print(f"    {k:<12s} = {v:+7.3f} {unit}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=5.0, help="Terminal refresh rate in Hz. Default 5.")
    parser.add_argument("--once", action="store_true", help="Print a single snapshot and exit.")
    args = parser.parse_args()

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING").upper())
    load_env()

    use_portal = bool(os.environ.get("LIVEKIT_URL") and os.environ.get("LIVEKIT_ROOM"))
    if use_portal:
        print(f"[diagnose] LIVEKIT_URL + LIVEKIT_ROOM set — publishing to Portal as '{IDENTITY}'")
    else:
        print("[diagnose] no LiveKit env vars — local only")

    fps = env_positive_int("PORTAL_FPS", 30) if use_portal else max(1, int(args.fps))
    robot = build_robot(with_safety_clamp=False)
    robot.connect()

    robot.bus1.disable_torque()
    robot.bus2.disable_torque()
    print("[diagnose] torque disabled — arms are limp")

    portal_teleop = None
    if use_portal:
        from lerobot_teleoperator_livekit import LiveKitTeleoperator, LiveKitTeleoperatorConfig
        url = required_env("LIVEKIT_URL")
        room = required_env("LIVEKIT_ROOM")
        portal_teleop = LiveKitTeleoperator(
            LiveKitTeleoperatorConfig(
                url=url,
                token=mint_token(IDENTITY, room),
                session=room,
                fps=fps,
            ),
            robot=robot,
        )
        portal_teleop.connect()
        print(f"[diagnose] connected to room '{room}' @ {fps} fps; ctrl-c to stop")

    joint_motors = robot.left_arm_motors + robot.right_arm_motors + robot.head_motors
    first = True
    camera_logged = False
    terminal_interval = 1.0 / args.fps
    last_terminal = 0.0

    try:
        for _ in pace(fps):
            obs = robot.get_observation()

            if not camera_logged:
                camera_logged = True
                cam_names = list(robot.cameras.keys())
                print(f"[diagnose] cameras: {cam_names or 'NONE'}")
                for name in cam_names:
                    frame = obs.get(name)
                    if frame is None:
                        print(f"[diagnose]   {name}: MISSING")
                    elif hasattr(frame, "shape"):
                        print(f"[diagnose]   {name}: {frame.shape} {frame.dtype}")

            if portal_teleop is not None:
                portal_teleop.send_feedback(obs)

            now = time.perf_counter()
            if now - last_terminal >= terminal_interval:
                last_terminal = now
                _print_state(obs, joint_motors, first, args.once)
                first = False

            if args.once:
                break

    except KeyboardInterrupt:
        print("\n[diagnose] stopping ...")
    finally:
        if portal_teleop is not None:
            with safe_disconnect("portal"):
                portal_teleop.disconnect()
        with safe_disconnect("robot"):
            robot.disconnect()


if __name__ == "__main__":
    main()
