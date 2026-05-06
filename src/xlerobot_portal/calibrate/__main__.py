"""One-shot calibration commands.

Two entry points — run one per machine:

    uv run xlerobot-portal-calibrate-robot      # XLeRobot follower (robot PC)
    uv run xlerobot-portal-calibrate-leaders    # both SO-101 leaders (operator PC)

Pass ``--force`` to discard the saved calibration JSON first.
Pass ``--motor NAME`` (repeatable) to redo individual motors without a full
re-sweep.
"""
from __future__ import annotations

import argparse
import logging
import os

from ..utils.common import env_str, load_env, require_path, required_env, safe_disconnect

logger = logging.getLogger(__name__)


def _bus_for_motor(bot, motor: str):
    if motor in bot.bus1.motors:
        return bot.bus1
    if motor in bot.bus2.motors:
        return bot.bus2
    raise ValueError(f"motor {motor!r} is not on bus1 or bus2")


def _calibrate_one_motor(bot, motor: str):
    from lerobot.motors import MotorCalibration
    from lerobot.motors.feetech import OperatingMode

    bus = _bus_for_motor(bot, motor)
    motor_obj = bus.motors[motor]

    if "wheel" in motor:
        print(f"[calibrate-robot] {motor}: continuous rotation, writing standard full-range entry")
        cal = MotorCalibration(
            id=motor_obj.id, drive_mode=0, homing_offset=0, range_min=0, range_max=4095
        )
        bus.write_calibration({motor: cal})
        return cal

    is_wrist_roll = "wrist_roll" in motor

    print(f"[calibrate-robot] calibrating {motor} only")
    bus.disable_torque([motor])
    bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)

    input(f"  1. Move {motor} to the MIDDLE of its physical range, then press ENTER ...")
    homing_offsets = bus.set_half_turn_homings([motor])

    if is_wrist_roll:
        print(f"[calibrate-robot] {motor}: treated as full-turn (range hard-coded to [0, 4095])")
        range_mins = {motor: 0}
        range_maxes = {motor: 4095}
    else:
        print(f"  2. Sweep {motor} slowly from one physical limit to the other, then press ENTER.")
        range_mins, range_maxes = bus.record_ranges_of_motion([motor])

    cal = MotorCalibration(
        id=motor_obj.id,
        drive_mode=0,
        homing_offset=homing_offsets[motor],
        range_min=range_mins[motor],
        range_max=range_maxes[motor],
    )
    bus.write_calibration({motor: cal})
    span = cal.range_max - cal.range_min
    print(
        f"[calibrate-robot] {motor} saved: "
        f"homing_offset={cal.homing_offset:+5d} range={cal.range_min}..{cal.range_max} (span={span})"
    )
    if span > 3500:
        print(
            f"[calibrate-robot]   WARN: span {span} is near full encoder range — "
            "the sweep likely wrapped. Try again starting nearer to geometric middle."
        )
    return cal


def robot() -> None:
    """Calibrate the XLeRobot follower (arms + head + base)."""
    parser = argparse.ArgumentParser(description="Calibrate the XLeRobot follower.")
    parser.add_argument("--force", action="store_true",
                        help="Discard the saved calibration JSON and run a fresh sweep.")
    parser.add_argument("--motor", action="append", default=[], metavar="NAME",
                        help="Calibrate just this motor and merge into the existing JSON.")
    args = parser.parse_args()

    if args.motor and args.force:
        parser.error("--motor and --force are mutually exclusive.")

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    load_env()

    from lerobot.robots.xlerobot import XLerobot, XLerobotConfig

    cfg = XLerobotConfig(
        id=env_str("XLEROBOT_ID", "xlerobot"),
        port1=require_path("XLEROBOT_PORT1", required_env("XLEROBOT_PORT1")),
        port2=require_path("XLEROBOT_PORT2", required_env("XLEROBOT_PORT2")),
        cameras={},
    )
    bot = XLerobot(cfg)

    if args.force and bot.calibration_fpath.exists():
        print(f"[calibrate-robot] --force: removing {bot.calibration_fpath}")
        bot.calibration_fpath.unlink()
        bot.calibration = {}

    if args.motor:
        known = set(bot.bus1.motors) | set(bot.bus2.motors)
        unknown = [m for m in args.motor if m not in known]
        if unknown:
            parser.error(f"unknown motor name(s): {unknown}. Valid names: {sorted(known)}")

    print(f"[calibrate-robot] connecting to {cfg.port1} + {cfg.port2} ...")
    try:
        bot.connect()
        if args.motor:
            new_entries = {m: _calibrate_one_motor(bot, m) for m in args.motor}
            merged = dict(bot.calibration)
            merged.update(new_entries)
            bot.calibration = merged
            bot._save_calibration()  # noqa: SLF001
            print(f"[calibrate-robot] patched {len(new_entries)} motor(s); saved to {bot.calibration_fpath}")
    finally:
        with safe_disconnect("robot"):
            bot.disconnect()

    if not args.motor:
        print(f"[calibrate-robot] saved to {bot.calibration_fpath}")


def _calibrate_one_leader_motor(leader, motor: str):
    from lerobot.motors import MotorCalibration
    from lerobot.motors.feetech import OperatingMode

    bus = leader.bus
    if motor not in bus.motors:
        raise ValueError(f"motor {motor!r} not on this leader (have {sorted(bus.motors)})")
    motor_obj = bus.motors[motor]
    is_wrist_roll = "wrist_roll" in motor

    print(f"[calibrate-leaders] calibrating {motor} only")
    bus.disable_torque([motor])
    bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)

    input(f"  1. Move {motor} to the MIDDLE of its physical range, then press ENTER ...")
    homing_offsets = bus.set_half_turn_homings([motor])

    if is_wrist_roll:
        print(f"[calibrate-leaders] {motor}: treated as full-turn (range hard-coded to [0, 4095])")
        range_mins = {motor: 0}
        range_maxes = {motor: 4095}
    else:
        print(f"  2. Sweep {motor} slowly from one physical limit to the other, then press ENTER.")
        range_mins, range_maxes = bus.record_ranges_of_motion([motor])

    cal = MotorCalibration(
        id=motor_obj.id,
        drive_mode=0,
        homing_offset=homing_offsets[motor],
        range_min=range_mins[motor],
        range_max=range_maxes[motor],
    )
    bus.write_calibration({motor: cal})
    span = cal.range_max - cal.range_min
    print(
        f"[calibrate-leaders] {motor} saved: "
        f"homing_offset={cal.homing_offset:+5d} range={cal.range_min}..{cal.range_max} (span={span})"
    )
    if span > 3500 and not is_wrist_roll:
        print(
            f"[calibrate-leaders]   WARN: span {span} is near full encoder range — "
            "the sweep likely wrapped. Try again starting nearer to geometric middle."
        )
    return cal


def leaders() -> None:
    """Calibrate the SO-101 leader arms."""
    parser = argparse.ArgumentParser(description="Calibrate the SO-101 leader arms.")
    parser.add_argument("--force", action="store_true",
                        help="Discard saved calibration JSON(s) and run a fresh sweep.")
    parser.add_argument("--side", choices=("left", "right"),
                        help="Which leader to operate on (required with --motor).")
    parser.add_argument("--motor", action="append", default=[], metavar="NAME",
                        help="Calibrate just this motor on the --side leader.")
    args = parser.parse_args()

    if args.motor and args.force:
        parser.error("--motor and --force are mutually exclusive.")
    if args.motor and not args.side:
        parser.error("--motor requires --side.")

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    load_env()

    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

    sides = (args.side,) if args.side else ("left", "right")

    for side in sides:
        port_var = f"SO101_{side.upper()}_LEADER_PORT"
        port = require_path(port_var, required_env(port_var))
        ident = env_str(f"SO101_{side.upper()}_LEADER_ID", f"so101_{side}_leader")
        leader = SO101Leader(SO101LeaderConfig(id=ident, port=port))

        if args.motor:
            known = set(leader.bus.motors)
            unknown = [m for m in args.motor if m not in known]
            if unknown:
                parser.error(f"unknown motor name(s) on {side} leader: {unknown}. Valid names: {sorted(known)}")
            if not leader.calibration_fpath.exists():
                parser.error(
                    f"--motor needs an existing calibration to patch, but "
                    f"{leader.calibration_fpath} does not exist. Run the full calibration first."
                )

        if args.force and leader.calibration_fpath.exists():
            print(f"[calibrate-leaders] ({side}) --force: removing {leader.calibration_fpath}")
            leader.calibration_fpath.unlink()
            leader.calibration = {}

        print(f"[calibrate-leaders] ({side}) connecting to {port} (id={ident}) ...")
        try:
            if args.motor:
                leader.connect(calibrate=False)
                if leader.calibration:
                    leader.bus.write_calibration(leader.calibration)
                new_entries = {m: _calibrate_one_leader_motor(leader, m) for m in args.motor}
                merged = dict(leader.calibration)
                merged.update(new_entries)
                leader.calibration = merged
                leader._save_calibration()  # noqa: SLF001
                print(
                    f"[calibrate-leaders] ({side}) patched {len(new_entries)} motor(s); "
                    f"saved to {leader.calibration_fpath}"
                )
            else:
                leader.connect()
                print(f"[calibrate-leaders] ({side}) saved id={ident}")
        finally:
            with safe_disconnect(f"{side} leader"):
                leader.disconnect()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "leaders":
        leaders()
    else:
        robot()
