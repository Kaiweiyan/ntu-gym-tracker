#!/usr/bin/env bash
#
# Cron wrapper: run ONE scrape cycle. main.py skips closed hours by itself, so
# you can schedule this every 10 min around the clock. Add to your crontab with
# `crontab -e`:
#
#     */10 * * * * /ABSOLUTE/PATH/TO/ntu-gym-tracker/scripts/run_collector.sh
#
# (cron runs with a minimal PATH, so we set it explicitly and use absolute dirs.)
set -euo pipefail

# Repo root = parent of this script's directory.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# Make uv discoverable in cron's non-interactive shell (adjust if installed elsewhere).
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

mkdir -p logs
uv run main.py >> logs/collector.log 2>&1
