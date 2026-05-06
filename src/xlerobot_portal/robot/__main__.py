"""Runs on the **physical XLeRobot**.

Deploys the robot behind a LiveKit Portal ``Role.ROBOT`` session. The remote
operator drives the arms + base; this side publishes observations (joint
positions + camera frames) back upstream.

Usage::

    cd xlerobot-portal
    cp .env.example .env      # fill LIVEKIT_* + serial ports
    uv run xlerobot-portal-robot
"""
from __future__ import annotations

import logging
import os

from lerobot_teleoperator_livekit import (
    LiveKitTeleoperator,
    LiveKitTeleoperatorConfig,
)

from ..utils.common import (
    env_positive_int,
    load_env,
    mint_token,
    pace,
    required_env,
    safe_disconnect,
)
from ..utils.robot_builder import build_robot

IDENTITY = "xlerobot-robot"
logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    load_env()
    url = required_env("LIVEKIT_URL")
    room = required_env("LIVEKIT_ROOM")
    fps = env_positive_int("PORTAL_FPS", 30)

    robot = build_robot(with_safety_clamp=True)
    robot.connect()

    teleop = LiveKitTeleoperator(
        LiveKitTeleoperatorConfig(
            url=url,
            token=mint_token(IDENTITY, room),
            session=room,
            fps=fps,
        ),
        robot=robot,
    )
    teleop.connect()

    print(f"[robot] '{IDENTITY}' in '{room}' @ {fps} fps; ctrl-c to stop")
    camera_health_logged = False

    try:
        for _ in pace(fps):
            obs = robot.get_observation()

            if not camera_health_logged:
                camera_health_logged = True
                cam_names = list(robot.cameras.keys())
                print(f"[robot] cameras: {cam_names or 'NONE'}")
                for name in cam_names:
                    frame = obs.get(name)
                    if frame is None:
                        print(f"[robot]   {name}: MISSING")
                    elif hasattr(frame, "shape"):
                        print(f"[robot]   {name}: {frame.shape} {frame.dtype}")

            teleop.send_feedback(obs)
            action = teleop.get_action()
            if action:
                robot.send_action(action)

    except KeyboardInterrupt:
        print("\n[robot] stopping ...")
    finally:
        with safe_disconnect("teleop"):
            teleop.disconnect()
        with safe_disconnect("robot"):
            robot.disconnect()


if __name__ == "__main__":
    main()
