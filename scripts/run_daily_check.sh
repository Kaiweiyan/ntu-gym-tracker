#!/usr/bin/env bash
#
# Cron wrapper for `daily-check` (ntu_gym_tracker/daily_check.py): read new announcements on the official
# site, register any closures, clean up data scraped on closure days, and
# commit the result. Twice a day — before opening and after closing:
#
#     30 7  * * * /ABSOLUTE/PATH/TO/ntu-gym-tracker/scripts/run_daily_check.sh
#     30 22 * * * /ABSOLUTE/PATH/TO/ntu-gym-tracker/scripts/run_daily_check.sh
#
# (cron runs with a minimal environment, so PATH and a UTF-8 locale are set
# explicitly — the log is mostly Chinese.)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONIOENCODING=utf-8

mkdir -p logs
.venv/bin/daily-check >> logs/daily_check.log 2>&1
