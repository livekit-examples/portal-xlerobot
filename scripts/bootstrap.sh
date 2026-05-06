#!/usr/bin/env bash
# bootstrap.sh — one-time setup for xlerobot-portal (standalone install)
#
# Run once on each machine (robot PC and operator PC):
#
#   bash scripts/bootstrap.sh
#
# What it does:
#   1. Installs uv (https://github.com/astral-sh/uv) if not already present
#   2. Clones huggingface/lerobot into external/lerobot/
#   3. Overlays the XLeRobot robot driver into external/lerobot/
#   4. Runs uv sync to install all Python dependencies
#
# After this script, copy and fill in .env, then calibrate and run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
EXTERNAL_DIR="$REPO_DIR/external"
LEROBOT_DIR="$EXTERNAL_DIR/lerobot"
OVERLAY_DIR="$EXTERNAL_DIR/lerobot_overlay"

# ── 1. uv ─────────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "[bootstrap] uv not found — installing ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add to PATH for the rest of this script
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "[bootstrap] ERROR: uv install succeeded but 'uv' still not on PATH."
        echo "            Open a new shell (or run: source ~/.bashrc / ~/.zshrc) then re-run this script."
        exit 1
    fi
    echo "[bootstrap] uv installed: $(uv --version)"
else
    echo "[bootstrap] uv found: $(uv --version)"
fi

# ── 2. Clone lerobot ──────────────────────────────────────────────────────────
if [ -d "$LEROBOT_DIR/.git" ]; then
    echo "[bootstrap] external/lerobot already cloned — skipping."
    echo "            To re-clone: rm -rf external/lerobot && bash scripts/bootstrap.sh"
else
    echo "[bootstrap] Cloning huggingface/lerobot (shallow) ..."
    git clone --depth 1 https://github.com/huggingface/lerobot.git "$LEROBOT_DIR"
    echo "[bootstrap] lerobot cloned."
fi

# ── 3. Overlay XLeRobot robot driver ──────────────────────────────────────────
TARGET_ROBOT_DIR="$LEROBOT_DIR/src/lerobot/robots/xlerobot"
if [ -d "$TARGET_ROBOT_DIR" ]; then
    echo "[bootstrap] XLeRobot overlay already in place — refreshing ..."
fi
cp -r "$OVERLAY_DIR/src/lerobot/robots/xlerobot" "$LEROBOT_DIR/src/lerobot/robots/"
echo "[bootstrap] XLeRobot robot driver overlaid."

# ── 4. Install Python dependencies ────────────────────────────────────────────
echo "[bootstrap] Running uv sync (this may take a minute on first run) ..."
cd "$REPO_DIR"
uv sync

echo ""
echo "────────────────────────────────────────────────────────"
echo " Bootstrap complete. Next steps:"
echo ""
echo "  1. cp .env.example .env"
echo "     # Fill in LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET"
echo "     # and the device paths for your machine (ports + cameras)."
echo ""
echo "  On the ROBOT PC:"
echo "  2. uv run xlerobot-portal-calibrate-robot   # one-time"
echo "  3. uv run xlerobot-portal-robot"
echo ""
echo "  On the OPERATOR PC:"
echo "  2. uv run xlerobot-portal-calibrate-leaders # one-time"
echo "  3. uv run xlerobot-portal-operator"
echo ""
echo "  See README.md for the full setup walkthrough."
echo "────────────────────────────────────────────────────────"
