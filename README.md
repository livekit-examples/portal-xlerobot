# xlerobot-portal

Bimanual SO-101 leader teleop of XLeRobot over **LiveKit Portal**, with live **rerun** visualization and **LeRobotDataset** recording on the operator side.

```
[robot PC]  XLeRobot + 3 cameras  ──LiveKit──▶  [operator PC]  SO-101 leaders + rerun viewer
```

Two processes, two machines, one `.env` file on each.

---

## Table of contents

1. [Hardware you need](#1-hardware-you-need)
2. [Get LiveKit credentials](#2-get-livekit-credentials)
3. [Clone and bootstrap](#3-clone-and-bootstrap)
4. [Configure `.env`](#4-configure-env)
5. [Find device paths](#5-find-device-paths)
6. [Calibrate (one-time)](#6-calibrate-one-time)
7. [Run](#7-run)
8. [Record an episode](#8-record-an-episode)
9. [Keybindings](#9-keybindings)
10. [Deploy to a remote robot PC](#10-deploy-to-a-remote-robot-pc)
11. [Troubleshooting](#11-troubleshooting)
12. [Reference](#12-reference)

---

## 1. Hardware you need

**Robot PC** (e.g. Raspberry Pi 5, Intel NUC, any Linux box):
- XLeRobot assembled and motor IDs flashed per the [XLeRobot QUICKSTART](https://github.com/Vector-Wangel/XLeRobot)
- 2× Feetech USB serial boards connected
- 3× USB cameras (left wrist, right wrist, head)
- Ethernet or WiFi to reach the internet (for LiveKit Cloud) or your self-hosted server

**Operator PC** (your laptop or desktop):
- 2× SO-101 leader arms connected via USB
- A display (rerun opens a viewer window)
- Same network access to LiveKit

Both machines need **Python 3.12+** and **git**.

---

## 2. Get LiveKit credentials

You need a LiveKit server that both machines can reach. Two options:

### Option A — LiveKit Cloud (recommended, free tier is enough)

1. Sign up at <https://cloud.livekit.io>
2. Create a project → **Settings → Keys → Add New Key**
3. Note down three values:
   - **URL**: `wss://your-project.livekit.cloud`
   - **API Key**: `APIxxxxxxxxxxxx`
   - **API Secret**: `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

### Option B — self-hosted dev server (same machine / LAN only)

```bash
# Install the livekit-server binary (macOS example)
brew install livekit

# Start a no-auth dev server
livekit-server --dev
```

Use `ws://localhost:7880` as the URL, `devkey` / `secret` as key / secret. Both machines must be able to reach this host.

---

## 3. Clone and bootstrap

Do this on **each machine** (robot PC and operator PC).

```bash
git clone https://github.com/pham-tuan-binh/xlerobot-portal.git
cd xlerobot-portal
bash scripts/bootstrap.sh
```

The bootstrap script:
1. Installs **uv** (fast Python package manager) if not present
2. Clones `huggingface/lerobot` into `external/lerobot/`
3. Copies the XLeRobot robot driver into that clone
4. Runs `uv sync` to create `.venv` and install all dependencies

First run takes 1–3 minutes (downloads packages, compiles a small Rust extension). Subsequent runs are fast.

> **No Rust toolchain required.** The pre-built `livekit-portal` wheel ships its Rust binary; you don't need to install Rust.

---

## 4. Configure `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in the values for **this machine**. Sections below walk through each block.

### LiveKit block (same on both machines)

```ini
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxx
LIVEKIT_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LIVEKIT_ROOM=xlerobot          # any name; both sides must match
PORTAL_FPS=30
```

### Robot PC block

```ini
XLEROBOT_PORT1=/dev/ttyACM0   # left arm + head Feetech bus
XLEROBOT_PORT2=/dev/ttyACM1   # right arm + base Feetech bus

XLEROBOT_CAM_LEFT_WRIST=/dev/video0
XLEROBOT_CAM_RIGHT_WRIST=/dev/video2
XLEROBOT_CAM_HEAD=/dev/video4

XLEROBOT_CAM_WIDTH=640         # bump to 1280 for 720p (see bandwidth note below)
XLEROBOT_CAM_HEIGHT=480
```

### Operator PC block

```ini
SO101_LEFT_LEADER_PORT=/dev/tty.usbmodem11101
SO101_RIGHT_LEADER_PORT=/dev/tty.usbmodem11201
SO101_LEFT_LEADER_ID=so101_left_leader
SO101_RIGHT_LEADER_ID=so101_right_leader

XLEROBOT_DATASET_REPO_ID=your-hf-username/xlerobot-bimanual
XLEROBOT_DATASET_TASK="bimanual manipulation"
```

The `*_LEADER_ID` values are arbitrary names used as calibration file keys. Use distinct names if you have multiple leaders.

### Optional: limit cameras

**Recommended: use 2 cameras.** Streaming 3 cameras simultaneously is known to be unstable — one stream will occasionally stall and corrupt the sync buffer. Use `left_wrist` + `right_wrist` for reliable teleop and only add `head` if you have a solid USB bus and good network.

```ini
XLEROBOT_CAMERAS=left_wrist,right_wrist   # omit or leave empty = all three
```

Valid names: `left_wrist`, `right_wrist`, `head`. Set **identically on both machines**.

---

## 5. Find device paths

### Serial ports (robot PC)

```bash
# Grant access (required once per boot, or add your user to the dialout group)
sudo chmod 666 /dev/ttyACM0 /dev/ttyACM1

# Identify which port is which bus
uv run lerobot-find-port
```

The tool will ask you to unplug/replug each board and tells you the path. Put them in `.env` as `XLEROBOT_PORT1` / `XLEROBOT_PORT2`.

### Camera paths (robot PC)

```bash
uv run lerobot-find-cameras opencv
```

This lists every OpenCV-visible camera with a test image. Note the paths.

> **Use stable camera paths.** `/dev/videoN` indices can change on reboot. Use the by-id symlinks for reliability:
>
> ```bash
> ls -l /dev/v4l/by-id/
> # e.g. usb-Generic_FHD_Camera_SN12345-video-index0 -> ../../video2
> ```
>
> Then set `XLEROBOT_CAM_LEFT_WRIST=/dev/v4l/by-id/usb-Generic_FHD_Camera_SN12345-video-index0`.
> If two cameras share the same vendor/product and no serial, use `/dev/v4l/by-path/...` which encodes the USB port instead.

### Leader arm ports (operator PC, macOS)

On macOS the ports appear as `/dev/tty.usbmodemXXXXX`. Run:

```bash
ls /dev/tty.usbmodem*
```

Unplug one arm to see which path disappears — that's its port.

---

## 6. Calibrate (one-time)

Calibration stores joint ranges to disk (`~/.cache/huggingface/lerobot/calibration/`). The scripts load it silently on every subsequent run. **Do this before the first teleop session** so the interactive prompts don't interrupt a live Portal session.

### 6a. Robot follower (robot PC)

```bash
uv run xlerobot-portal-calibrate-robot
```

Four prompts walk you through:
1. Move the **left arm + head** to the **middle of their range** → ENTER
2. **Sweep** every left-arm and head joint slowly through its full range → ENTER
3. Move the **right arm** to the **middle of its range** → ENTER
4. **Sweep** every right-arm joint through its full range → ENTER

Wheels are skipped automatically (continuous rotation, no range to calibrate).

To redo a single motor without a full sweep:

```bash
uv run xlerobot-portal-calibrate-robot --motor left_arm_wrist_flex
```

To wipe everything and start over:

```bash
uv run xlerobot-portal-calibrate-robot --force
```

### 6b. SO-101 leaders (operator PC)

```bash
uv run xlerobot-portal-calibrate-leaders
```

Walks through both leaders in sequence. Each leader gets two prompts: mid-range → ENTER, then full-range sweep → ENTER.

To redo one leader:

```bash
uv run xlerobot-portal-calibrate-leaders --side left --motor shoulder_pan
```

---

## 7. Run

Start the robot side **first**, then the operator side.

### Terminal on the robot PC

```bash
uv run xlerobot-portal-robot
```

Expected output:

```
[robot] 'xlerobot-robot' in 'xlerobot' @ 30 fps; ctrl-c to stop
```

### Terminal on the operator PC

```bash
uv run xlerobot-portal-operator
```

The **rerun viewer** opens automatically with three camera panels on top and time-series plots (joint state, action, Portal metrics) on the bottom. Pick up the leader arms — the follower arms should track immediately.

Both sides connect to the LiveKit room and stream in ~1 second. If no camera panels appear within 5 seconds, check the [troubleshooting table](#11-troubleshooting).

---

## 8. Record an episode

1. Move the robot and leaders to the task start pose.
2. Press **`r`** — the terminal prints `episode N recording`.
3. Perform the task.
4. Press **`r`** again — terminal prints `episode N saving in background`. The 30 Hz control loop keeps running without interruption; saving happens on a background thread.
5. Press **`[`** instead of `r` to **discard** a bad take (no file written).
6. Repeat for as many episodes as you need.
7. Press **`x`** to quit cleanly (waits for any in-flight save to finish).

### Where episodes land

On the operator PC:

```
~/.cache/huggingface/lerobot/<XLEROBOT_DATASET_REPO_ID>/
```

Override with `XLEROBOT_DATASET_ROOT` in `.env` to put the dataset elsewhere.

Inspect with the lerobot visualizer:

```bash
uv run python -m lerobot.scripts.visualize_dataset \
    --repo-id $XLEROBOT_DATASET_REPO_ID \
    --episode-index 0
```

---

## 9. Keybindings

| Key | Action |
|---|---|
| `w` / `s` | Base forward / backward |
| `a` / `d` | Base strafe left / right |
| `q` / `e` | Base rotate left / right |
| `←` / `→` | Head pan left / right |
| `↑` / `↓` | Head tilt up / down |
| `n` / `m` | Speed up / down (0.1 → 0.2 → 0.3 m/s; 30 → 60 → 90 °/s) |
| `r` | Toggle episode recording |
| `[` | Discard the in-flight episode |
| `x` | Clean quit |

If the robot's forward axis doesn't match your `w`/`s` keys, set `XLEROBOT_BASE_ROTATION=90` (or `180`, `270`) in `.env` to rotate the keyboard frame.

---

## 10. Deploy to a remote robot PC

If you develop on a laptop and want to sync files to the robot PC without re-cloning:

```ini
# In .env on the laptop
XLEROBOT_ROBOT_REMOTE=pi@xlerobot.local
XLEROBOT_ROBOT_REMOTE_ROOT=~/workspace
```

```bash
./scripts/deploy_to_robot.sh
```

This rsyncs the repo to the remote (excluding `.venv/`, `.git/`, `external/lerobot/`). After the first sync, run `bash scripts/bootstrap.sh` on the robot PC to set up its own virtualenv.

The script prints follow-up commands to run on the robot. `.env` is included in the transfer so the robot picks up the shared LiveKit credentials; put machine-specific overrides (port paths, camera paths) in `.env.local` on each machine — it's excluded from rsync.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: LIVEKIT_* must be set` | `.env` not found in working directory | Run from the `xlerobot-portal/` root, or `cd` there first |
| `find_port` says wrong number of motors detected | `XLEROBOT_PORT1` / `PORT2` swapped | Swap the two values in `.env` |
| Camera panels black or missing | Camera path wrong, or 720p unsupported | Run `uv run lerobot-find-cameras opencv` on the robot to verify paths; lower resolution if needed |
| Growing `obs_age_ms` / `states_dropped` non-zero | Bandwidth-starved link | Reduce `PORTAL_FPS` or lower `XLEROBOT_CAM_WIDTH`/`HEIGHT` |
| `stale_observations_emitted` climbing | One camera consistently slower than others | Typically harmless at a few per minute; investigate only if it reaches thousands per episode |
| `r` key does nothing | pynput didn't get keyboard access (SSH without a tty, or macOS accessibility permission not granted) | Run operator script in a local terminal; on macOS, grant Terminal / your IDE accessibility permission in System Settings |
| Leader calibration prompt fires every run | Colliding `SO101_*_LEADER_ID` values, or unwritable calibration directory | Use distinct IDs; check `~/.cache/huggingface/lerobot/calibration/` permissions |
| `ImportError` on any lerobot module | `external/lerobot` overlay missing | Re-run `bash scripts/bootstrap.sh` |

Set `LOG_LEVEL=DEBUG` in `.env` for verbose output on either side.

### Diagnose without running teleop

```bash
# Shows live joint state in the terminal, torque off, so you can hand-move the arms.
# If LIVEKIT_URL + LIVEKIT_ROOM are set, also publishes observations so an
# operator-side viewer can watch without sending commands.
uv run xlerobot-portal-diagnose
uv run xlerobot-portal-diagnose --fps 10   # faster terminal refresh
uv run xlerobot-portal-diagnose --once     # single snapshot then exit
```

---

## 12. Reference

### Commands

| Command | Machine | Purpose |
|---|---|---|
| `xlerobot-portal-robot` | robot PC | Main robot process — publishes cameras + state, applies incoming actions |
| `xlerobot-portal-operator` | operator PC | Main operator process — reads leaders, sends actions, shows rerun, records |
| `xlerobot-portal-calibrate-robot` | robot PC | Interactive follower calibration (`--motor NAME` to patch one joint, `--force` to redo all) |
| `xlerobot-portal-calibrate-leaders` | operator PC | Interactive leader calibration (`--side left\|right`, `--force`) |
| `xlerobot-portal-diagnose` | robot PC | Read-only joint state viewer; optionally publishes over Portal |

### Action format on the wire

17 scalar keys sent each tick (`XLerobot.action_features`):

```
left_arm_{shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper}.pos
right_arm_{shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper}.pos
head_motor_1.pos, head_motor_2.pos
x.vel, y.vel, theta.vel
```

SO-101 leaders produce plain joint names (`shoulder_pan.pos`, …). The operator script prefixes them with `left_arm_` / `right_arm_` before sending.

### Bandwidth

Default 640×480 @ 30 fps ≈ 2–4 Mbps total (3 cams, H.264). Works over any reasonable link.

720p: set `XLEROBOT_CAM_WIDTH=1280` / `XLEROBOT_CAM_HEIGHT=720` in `.env` on **both machines**. Expect ≈3× operator-side decode cost.

If you see latency spikes: look for non-zero `states_dropped` or a growing `buf_vid_max` in the operator stats line, and reduce `PORTAL_FPS` or resolution accordingly.

### Dataset recording internals

Recording never blocks the 30 Hz loop:
- `add_frame()` enqueues into `LeRobotDataset`'s internal image-writer thread pool (returns in µs)
- `save_episode()` runs on a dedicated thread spawned when you press `r` to stop

If you press `r` to start a new episode while the prior save is still running, the start briefly joins the save thread — that's the only potential stall point. `ctrl-c` or `x` calls `finalize()` which joins cleanly.

Video frames in the dataset are encoded after a round-trip through Portal's H.264 encode + decode, so they're one generation lossier than camera capture. If raw-frame fidelity matters, record on the robot side instead.

### Rerun layout

Two rows: three `Spatial2DView`s (one per camera) on top, three `TimeSeriesView`s (follower state, commanded action, Portal metrics) on the bottom. `row_shares=[2, 1]` gives cameras 2/3 of the vertical space. The timeline is anchored to sender-side wall clock so scrubbing reflects physical robot time, not receive time.
