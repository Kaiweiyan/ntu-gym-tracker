# CLAUDE.md

## 開始任何工作之前

1. **先讀 `docs/spec.md`**，遵守最上面的「專案規則」。重點：
   - 任何檔案更動前都要先跟使用者達成明確共識。
   - 先把設計寫進 `docs/spec.md`，才開始寫程式。
   - 實作前先 `git pull`。
2. 文件分工見 `docs/spec.md` 的「文件分工」：現在的設計 → `docs/spec.md`，未定案提案 → `docs/plan.md`，
   過程與版本紀錄 → `docs/CHANGELOG.md`，設定速查 → `docs/settings.md`。
3. Commit 訊息依照 `docs/git-style.md`。只有使用者要求時才 commit / push / 打 tag，
   發布版本時先把 commit 訊息與 tag 給使用者確認。

## 溝通與程式碼

- 回覆使用者用**繁體中文**；程式碼註解用**英文**。
- 檔名與資料夾名稱一律英文。

## 執行環境（工作站 ws3）

- 收集器：tmux session `gym`，`uv run collector`。重啟前先問使用者。
- 網頁：tmux session `web`（pane `web:0.0`），`uv run uvicorn ntu_gym_tracker.app:app --host 0.0.0.0 --port 10307`，沒有 `--reload`。
  **改了網頁相關程式（`ntu_gym_tracker/`、`templates/`、`static/`）後要重啟它。**
- 測試用伺服器不要用 port 10307（正式網站）。
- 自動檢查：crontab 每天 07:30、22:30 跑 `scripts/run_daily_check.sh`，會自己 commit `[Auto] ...`。

## 資料安全

- 修改 `data/occupancy.csv` 前先備份（`uv run csv-tool` 會自動做）；測試時用複本，不要寫到真的資料檔。
- 不要 commit 備份檔（`*.csv.bak.*`）或 `logs/`。
- `ntu_gym_tracker/config.py` 的 `USER_AGENT` 含有私人信箱，不要送到任何外部服務。
