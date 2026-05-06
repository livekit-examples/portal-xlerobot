"""Map SO-101 leader action keys onto XLeRobot follower action keys."""
from __future__ import annotations

from typing import Mapping


def prefix_arm(action: Mapping[str, float], side: str) -> dict[str, float]:
    """Rewrite every `<joint>.pos` key to `<side>_arm_<joint>.pos`."""
    if side not in ("left", "right"):
        raise ValueError(f"side must be 'left' or 'right', got {side!r}")
    out: dict[str, float] = {}
    for key, value in action.items():
        if not key.endswith(".pos"):
            continue
        joint = key[: -len(".pos")]
        out[f"{side}_arm_{joint}.pos"] = float(value)
    return out
