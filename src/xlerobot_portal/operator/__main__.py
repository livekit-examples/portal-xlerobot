"""Runs on the **operator's desk** alongside two physical SO-101 leader arms.

Drives the remote XLeRobot (arms + base) through a LiveKit Portal session.
Each tick reads both leader arms, sends the merged action over the wire, and
pulls back the synced remote observation for rerun visualization and optional
dataset recording.

Keys:
  * ``w a s d q e`` — drive the mobile base (forward/back, strafe, rotate).
  * ``↑ ↓ ← →``     — pan/tilt the head.
  * ``n m``         — cycle base speed (slow / medium / fast).
  * ``r``           — toggle episode recording.
  * ``[``           — discard the in-flight episode.
  * ``x``           — clean quit.
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np
import rerun as rr
import rerun.blueprint as rrb
from lerobot.teleoperators.keyboard import KeyboardTeleop, KeyboardTeleopConfig
from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
from lerobot.utils.visualization_utils import init_rerun
from lerobot_robot_livekit import LiveKitRobot, LiveKitRobotConfig

from ..utils.common import (
    env_bool,
    env_optional_path,
    env_positive_int,
    env_str,
    load_env,
    mint_token,
    pace,
    required_env,
    require_path,
    safe_disconnect,
)
from ..utils.keymap import prefix_arm
from ..utils.recorder import DatasetRecorder

IDENTITY = "xlerobot-operator"
DEFAULT_CAMERA_WIDTH = 640
DEFAULT_CAMERA_HEIGHT = 480
ALL_CAMERAS = ("left_wrist", "head", "right_wrist")

XLEROBOT_ACTION_KEYS = (
    "left_arm_shoulder_pan.pos",
    "left_arm_shoulder_lift.pos",
    "left_arm_elbow_flex.pos",
    "left_arm_wrist_flex.pos",
    "left_arm_wrist_roll.pos",
    "left_arm_gripper.pos",
    "right_arm_shoulder_pan.pos",
    "right_arm_shoulder_lift.pos",
    "right_arm_elbow_flex.pos",
    "right_arm_wrist_flex.pos",
    "right_arm_wrist_roll.pos",
    "right_arm_gripper.pos",
    "head_motor_1.pos",
    "head_motor_2.pos",
    "x.vel",
    "y.vel",
    "theta.vel",
)

BASE_KEYS = {
    "forward": "w",
    "backward": "s",
    "strafe_left": "a",
    "strafe_right": "d",
    "rotate_left": "q",
    "rotate_right": "e",
}
HEAD_KEYS = {
    "pan_left": "left",
    "pan_right": "right",
    "tilt_up": "up",
    "tilt_down": "down",
}
HEAD_SPEED_UPS = 40.0
SPEED_LEVELS = (
    {"xy": 0.1, "theta": 30.0},
    {"xy": 0.2, "theta": 60.0},
    {"xy": 0.3, "theta": 90.0},
)
logger = logging.getLogger(__name__)


def _active_cameras() -> tuple[str, ...]:
    raw = os.environ.get("XLEROBOT_CAMERAS", "").strip()
    if not raw:
        return ALL_CAMERAS
    chosen = tuple(n.strip() for n in raw.split(",") if n.strip() in ALL_CAMERAS)
    if not chosen:
        print(f"[operator] XLEROBOT_CAMERAS={raw!r} had no valid names — using all cameras")
        return ALL_CAMERAS
    skipped = [n for n in ALL_CAMERAS if n not in chosen]
    if skipped:
        print(f"[operator] cameras disabled: {', '.join(skipped)}")
    return chosen


@dataclass
class _SchemaStub:
    """Duck-typed teleop passed to LiveKitRobot to set the wire schema."""
    action_features: dict = field(
        default_factory=lambda: {k: float for k in XLEROBOT_ACTION_KEYS}
    )


def _log_rerun_dict(namespace: str, data: dict) -> None:
    for key, value in data.items():
        entity = f"{namespace}/{key}"
        if isinstance(value, np.ndarray):
            rr.log(entity, rr.Image(value).compress())
        elif isinstance(value, (int, float)):
            rr.log(entity, rr.Scalars(float(value)))


def _build_blueprint(cameras: tuple[str, ...]) -> rrb.Blueprint:
    return rrb.Blueprint(
        rrb.Vertical(
            rrb.Horizontal(
                *(rrb.Spatial2DView(origin=f"/observation/{cam}", name=cam) for cam in cameras)
            ),
            rrb.Horizontal(
                rrb.TimeSeriesView(origin="/observation", name="Follower state"),
                rrb.TimeSeriesView(origin="/action", name="Commanded action"),
                rrb.TimeSeriesView(origin="/metrics", name="Portal metrics"),
            ),
            row_shares=[2, 1],
        ),
        collapse_panels=True,
    )


def _us_to_ms(us: int | None) -> float | None:
    return us / 1e3 if us else None


def _log_metrics(m) -> None:
    rtt_last = _us_to_ms(m.rtt.rtt_us_last)
    rtt_mean = _us_to_ms(m.rtt.rtt_us_mean)
    if rtt_last is not None:
        rr.log("metrics/rtt_last_ms", rr.Scalars(rtt_last))
    if rtt_mean is not None:
        rr.log("metrics/rtt_mean_ms", rr.Scalars(rtt_mean))
    sync_p50 = _us_to_ms(m.sync.match_delta_us_p50)
    sync_p95 = _us_to_ms(m.sync.match_delta_us_p95)
    if sync_p50 is not None:
        rr.log("metrics/sync_delta_p50_ms", rr.Scalars(sync_p50))
    if sync_p95 is not None:
        rr.log("metrics/sync_delta_p95_ms", rr.Scalars(sync_p95))
    state_jitter = _us_to_ms(m.transport.state_jitter_us) or 0.0
    action_jitter = _us_to_ms(m.transport.action_jitter_us) or 0.0
    rr.log("metrics/state_jitter_ms", rr.Scalars(state_jitter))
    rr.log("metrics/action_jitter_ms", rr.Scalars(action_jitter))
    for name, us in m.transport.frame_jitter_us.items():
        rr.log(f"metrics/frame_jitter_ms/{name}", rr.Scalars((_us_to_ms(us) or 0.0)))
    rr.log("metrics/states_dropped", rr.Scalars(m.sync.states_dropped))
    rr.log("metrics/stale_observations", rr.Scalars(m.sync.stale_observations_emitted))


class OperatorKeyboardTeleop(KeyboardTeleop):
    """KeyboardTeleop that also reports arrow keys as string names."""

    def _arrow_name(self, key) -> str | None:
        try:
            from pynput.keyboard import Key
        except Exception:
            return None
        return {Key.up: "up", Key.down: "down", Key.left: "left", Key.right: "right"}.get(key)

    def _on_press(self, key):
        super()._on_press(key)
        if (name := self._arrow_name(key)) is not None:
            self.event_queue.put((name, True))

    def _on_release(self, key):
        super()._on_release(key)
        if (name := self._arrow_name(key)) is not None:
            self.event_queue.put((name, False))


class BaseKeyboardDriver:
    """Translate pressed keys into body-frame base velocities."""

    def __init__(self) -> None:
        self._speed_index = 0
        self._rotation_deg = float(env_positive_int("XLEROBOT_BASE_ROTATION", 0))
        if self._rotation_deg:
            print(f"[operator] base rotation: {self._rotation_deg:.0f}° CCW")

    def action(self, pressed: set[str]) -> dict[str, float]:
        speed = SPEED_LEVELS[self._speed_index]
        xy, theta = speed["xy"], speed["theta"]
        x_raw = y_raw = theta_cmd = 0.0
        if BASE_KEYS["forward"] in pressed:
            x_raw += xy
        if BASE_KEYS["backward"] in pressed:
            x_raw -= xy
        if BASE_KEYS["strafe_left"] in pressed:
            y_raw += xy
        if BASE_KEYS["strafe_right"] in pressed:
            y_raw -= xy
        if BASE_KEYS["rotate_left"] in pressed:
            theta_cmd += theta
        if BASE_KEYS["rotate_right"] in pressed:
            theta_cmd -= theta
        rad = math.radians(self._rotation_deg)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        return {
            "x.vel": cos_r * x_raw - sin_r * y_raw,
            "y.vel": sin_r * x_raw + cos_r * y_raw,
            "theta.vel": theta_cmd,
        }

    def step_speed(self, direction: int) -> int:
        self._speed_index = max(0, min(len(SPEED_LEVELS) - 1, self._speed_index + direction))
        return self._speed_index


class HeadKeyboardDriver:
    """Translate arrow keys into head pan/tilt position targets."""

    def __init__(self, step_per_tick: float) -> None:
        self._step = step_per_tick
        self._pan: float | None = None
        self._tilt: float | None = None
        self._last_obs_pan = 0.0
        self._last_obs_tilt = 0.0

    def update_from_observation(self, obs: dict) -> None:
        if "head_motor_1.pos" in obs:
            self._last_obs_pan = float(obs["head_motor_1.pos"])
        if "head_motor_2.pos" in obs:
            self._last_obs_tilt = float(obs["head_motor_2.pos"])

    def action(self, pressed: set[str]) -> dict[str, float]:
        arrow_active = any(HEAD_KEYS[k] in pressed for k in HEAD_KEYS)
        if self._pan is None:
            if not arrow_active:
                return {}
            self._pan = self._last_obs_pan
            self._tilt = self._last_obs_tilt
        if HEAD_KEYS["pan_left"] in pressed:
            self._pan = max(-100.0, self._pan - self._step)
        if HEAD_KEYS["pan_right"] in pressed:
            self._pan = min(100.0, self._pan + self._step)
        if HEAD_KEYS["tilt_up"] in pressed:
            self._tilt = max(-100.0, self._tilt - self._step)
        if HEAD_KEYS["tilt_down"] in pressed:
            self._tilt = min(100.0, self._tilt + self._step)
        return {"head_motor_1.pos": self._pan, "head_motor_2.pos": self._tilt}


class EdgeDetector:
    """Fire once per key press (rising edge)."""

    def __init__(self, keys: set[str]) -> None:
        self._keys = keys
        self._prev: set[str] = set()

    def fire(self, pressed: set[str]) -> set[str]:
        rising = (pressed & self._keys) - self._prev
        self._prev = pressed & self._keys
        return rising


def _build_recorder(robot: LiveKitRobot, fps: int, cameras: tuple[str, ...]) -> DatasetRecorder:
    return DatasetRecorder(
        repo_id=env_str("XLEROBOT_DATASET_REPO_ID", "local/xlerobot"),
        fps=fps,
        observation_features=robot.observation_features,
        action_features=robot.action_features,
        task=env_str("XLEROBOT_DATASET_TASK", "bimanual manipulation"),
        root=env_optional_path("XLEROBOT_DATASET_ROOT"),
        num_cameras=len(cameras),
    )


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    load_env()
    cameras = _active_cameras()
    url = required_env("LIVEKIT_URL")
    room = required_env("LIVEKIT_ROOM")
    fps = env_positive_int("PORTAL_FPS", 30)

    swap_arms = env_bool("XLEROBOT_SWAP_ARMS", default=False)
    left_prefix = "right" if swap_arms else "left"
    right_prefix = "left" if swap_arms else "right"
    if swap_arms:
        print("[operator] XLEROBOT_SWAP_ARMS=1 — left leader drives right arm (and vice versa)")

    left_port = require_path("SO101_LEFT_LEADER_PORT", required_env("SO101_LEFT_LEADER_PORT"))
    right_port = require_path("SO101_RIGHT_LEADER_PORT", required_env("SO101_RIGHT_LEADER_PORT"))

    left_leader = SO101Leader(SO101LeaderConfig(
        id=env_str("SO101_LEFT_LEADER_ID", "so101_left_leader"),
        port=left_port,
    ))
    right_leader = SO101Leader(SO101LeaderConfig(
        id=env_str("SO101_RIGHT_LEADER_ID", "so101_right_leader"),
        port=right_port,
    ))
    robot = LiveKitRobot(
        LiveKitRobotConfig(
            url=url,
            token=mint_token(IDENTITY, room),
            session=room,
            fps=fps,
            camera_names=cameras,
            camera_width=env_positive_int("XLEROBOT_CAM_WIDTH", DEFAULT_CAMERA_WIDTH),
            camera_height=env_positive_int("XLEROBOT_CAM_HEIGHT", DEFAULT_CAMERA_HEIGHT),
            reuse_stale_frames=env_bool("XLEROBOT_REUSE_STALE_FRAMES", default=True),
        ),
        teleop=_SchemaStub(),
    )
    keyboard = OperatorKeyboardTeleop(KeyboardTeleopConfig())

    left_leader.connect()
    right_leader.connect()
    robot.connect()
    keyboard.connect()

    recorder = _build_recorder(robot, fps, cameras)

    init_rerun(session_name=f"xlerobot-operator-{room}")
    rr.send_blueprint(_build_blueprint(cameras))

    base = BaseKeyboardDriver()
    head = HeadKeyboardDriver(step_per_tick=HEAD_SPEED_UPS / fps)
    edges = EdgeDetector({"r", "n", "m", "x", "["})

    print(f"[operator] '{IDENTITY}' in '{room}' @ {fps} fps")
    print("[operator] w/a/s/d/q/e=base  arrows=head  n/m=speed  r=record  [=discard  x=quit")

    running = True
    try:
        for _ in pace(fps):
            pressed = set(keyboard.get_action().keys())

            for key in edges.fire(pressed):
                if key == "r":
                    if recorder.is_recording:
                        recorder.end_episode()
                        print(f"[operator] episode {recorder.episode_count - 1} saving in background")
                    else:
                        recorder.start_episode()
                        print(f"[operator] episode {recorder.episode_count} recording")
                elif key == "[":
                    recorder.discard_episode()
                    print("[operator] episode discarded")
                elif key == "n":
                    print(f"[operator] speed -> level {base.step_speed(+1)}")
                elif key == "m":
                    print(f"[operator] speed -> level {base.step_speed(-1)}")
                elif key == "x":
                    running = False
            if not running:
                break

            action: dict[str, float] = {}
            action.update(prefix_arm(left_leader.get_action(), left_prefix))
            action.update(prefix_arm(right_leader.get_action(), right_prefix))
            action.update(base.action(pressed))
            action.update(head.action(pressed))

            robot.send_action(action)
            obs = robot.get_observation()
            head.update_from_observation(obs)
            recorder.push_frame(obs, action)
            for msg in recorder.poll_errors():
                print(f"[operator] recorder error: {msg}")

            if (ts := robot.last_observation_timestamp_us) is not None:
                rr.set_time("robot_time", timestamp=ts / 1e6)
                obs_age_ms = (time.time() * 1e6 - ts) / 1e3
                rr.log("metrics/obs_age_ms", rr.Scalars(obs_age_ms))

            _log_rerun_dict("observation", obs)
            _log_rerun_dict("action", action)
            rr.log("recording/active", rr.Scalars(float(recorder.is_recording)))

            if (m := robot.metrics()) is not None:
                _log_metrics(m)

    except KeyboardInterrupt:
        print("\n[operator] stopping ...")
    finally:
        with safe_disconnect("recorder"):
            recorder.finalize()
        with safe_disconnect("keyboard"):
            keyboard.disconnect()
        with safe_disconnect("robot"):
            robot.disconnect()
        with safe_disconnect("right_leader"):
            right_leader.disconnect()
        with safe_disconnect("left_leader"):
            left_leader.disconnect()


if __name__ == "__main__":
    main()
