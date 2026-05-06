#!/usr/bin/env bash
# Rsync everything the robot PC needs (XLeRobot + livekit-portal sources)
# from this workstation to the robot, preserving the sibling layout
# assumed by xlerobot_portal/pyproject.toml's path deps.
#
# Usage:
#   ./scripts/deploy_to_robot.sh                                # reads XLEROBOT_ROBOT_REMOTE / XLEROBOT_ROBOT_REMOTE_ROOT from .env
#   REMOTE=user@robot.local ./scripts/deploy_to_robot.sh        # one-off override
#   REMOTE=pi@xlerobot REMOTE_ROOT=/home/pi/code ./scripts/deploy_to_robot.sh
#
# Put the defaults in .env so you don't retype them:
#   XLEROBOT_ROBOT_REMOTE=binh@xlerobot.local
#   XLEROBOT_ROBOT_REMOTE_ROOT=~/workspace
#
# After rsync, the script prints the follow-up commands to run on the
# robot (build native portal bindings for its arch, uv sync, calibrate).
# We intentionally don't `ssh && run` — you want eyes on the first build.
#
# What gets shipped:
#   - XLeRobot/           (everything except .git/, .venv/, __pycache__, etc.)
#   - livekit-portal/     (sources only; Rust target/ and the macOS dylib are
#                          excluded since the robot rebuilds those for its
#                          own arch in the post-rsync step)
#
# What stays local:
#   - build artifacts (target/, .venv/, __pycache__/)
#   - pytest/hf caches
#   - .git histories (if you want the robot to pull from git, do that
#                    separately; this script only mirrors working tree)

set -euo pipefail

if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync not found on this machine" >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"           # software/xlerobot_portal/
REPO_ROOT="$(cd "$HERE/../.." && pwd)"             # XLeRobot/
PORTAL_ROOT="$(cd "$REPO_ROOT/.." && pwd)/livekit-portal"

# Pull our two keys out of `.env` without `source`-ing the whole file.
# Sourcing breaks on values like `XLEROBOT_DATASET_TASK=bimanual manipulation`
# — python-dotenv tolerates unquoted spaces, bash does not. We only need two
# specific keys here, so a grep is both safer and narrower in scope. An
# explicit REMOTE=... on the command line still wins below.
_env_get() {
    local key="$1" file="$2"
    [[ -f "$file" ]] || return 0
    # Match `KEY=...`, strip any trailing newline, and drop surrounding
    # quotes if the user wrote the value quoted.
    local line
    line="$(grep -E "^${key}=" "$file" | tail -1 || true)"
    [[ -n "$line" ]] || return 0
    local value="${line#${key}=}"
    value="${value%$'\r'}"
    [[ "$value" == \"*\" && "$value" == *\" ]] && value="${value:1:-1}"
    [[ "$value" == \'*\' && "$value" == *\' ]] && value="${value:1:-1}"
    printf '%s\n' "$value"
}

if [[ -f "$HERE/.env" ]]; then
    : "${XLEROBOT_ROBOT_REMOTE:=$(_env_get XLEROBOT_ROBOT_REMOTE "$HERE/.env")}"
    : "${XLEROBOT_ROBOT_REMOTE_ROOT:=$(_env_get XLEROBOT_ROBOT_REMOTE_ROOT "$HERE/.env")}"
fi

REMOTE="${REMOTE:-${XLEROBOT_ROBOT_REMOTE:-}}"
REMOTE_ROOT="${REMOTE_ROOT:-${XLEROBOT_ROBOT_REMOTE_ROOT:-~/workspace}}"

if [[ -z "$REMOTE" ]]; then
    echo "REMOTE is empty. Set XLEROBOT_ROBOT_REMOTE in .env or pass REMOTE=user@host." >&2
    exit 1
fi

if [[ ! -d "$PORTAL_ROOT" ]]; then
    echo "expected livekit-portal at $PORTAL_ROOT (sibling of XLeRobot/)" >&2
    exit 1
fi

IGNORE_FILE="$HERE/scripts/deploy.rsyncignore"
if [[ ! -f "$IGNORE_FILE" ]]; then
    echo "missing ignore file: $IGNORE_FILE" >&2
    exit 1
fi

echo "[deploy] remote: $REMOTE:$REMOTE_ROOT"

# rsync won't create nested parents on the remote — pre-create the target
# dirs over ssh. `mkdir -p` is idempotent, so this is cheap on reruns.
# Wrapping the path in double-quotes would disable ~ expansion, so use
# single-quotes to let the remote shell resolve ~.
echo "[deploy] ensuring remote directories exist ..."
ssh "$REMOTE" "mkdir -p '$REMOTE_ROOT/XLeRobot' '$REMOTE_ROOT/livekit-portal'"

echo "[deploy] syncing XLeRobot/ ..."
rsync -azP --delete --exclude-from="$IGNORE_FILE" "$REPO_ROOT/" "$REMOTE:$REMOTE_ROOT/XLeRobot/"

echo "[deploy] syncing livekit-portal/ ..."
rsync -azP --delete --exclude-from="$IGNORE_FILE" "$PORTAL_ROOT/" "$REMOTE:$REMOTE_ROOT/livekit-portal/"

cat <<EOF

[deploy] done. On the robot:

  # 1. Install prerequisites (once)
  sudo apt install -y rustc cargo pkg-config libssl-dev    # Debian/Ubuntu
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # 2. Rebuild livekit-portal's native FFI for this machine's arch
  cd $REMOTE_ROOT/livekit-portal
  bash scripts/build_ffi_python.sh release

  # 3. Install the xlerobot_portal package + deps
  cd $REMOTE_ROOT/XLeRobot/software/xlerobot_portal
  cp .env.example .env   # fill LIVEKIT_*, XLEROBOT_PORT*, camera paths
  uv sync

  # 4. First-time calibrate (if not already done)
  uv run xlerobot-portal-calibrate-robot

  # 5. Run
  uv run xlerobot-portal-robot

EOF
