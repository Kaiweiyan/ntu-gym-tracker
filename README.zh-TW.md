# NTU Gym Tracker

[English](README.md) | 繁體中文

收集、呈現並預測臺灣大學**健身中心**與**室內游泳池**的即時人數。

輕量的收集器每 10 分鐘抓一次官方的人數頁面，存進 CSV；FastAPI 提供 JSON API 和由伺服器產生的網頁，
顯示即時人數、人數預測、各時段平均人數與本月休館行事曆。網頁有中文 / 英文與淺色 / 深色模式。

## 運作方式

```
收集器（常駐，24 小時）
    │
    │  每 10 分鐘抓取 + 解析
    ▼
data/occupancy.csv
    │
    │  用 pandas 讀取（有快取）
    ▼
FastAPI — JSON API + 網頁（Jinja2 · ECharts · HTMX）
```

人數直接寫在官網 HTML 裡，一般的 HTTP GET 就抓得到，不需要無頭瀏覽器。

## 功能

- **即時人數**：每館顯示目前人數與忙碌度（跟該館自己的最適人數比），透過 HTMX 自動更新。
- **人數預測**：今天 / 明天，實際人數接上預測基準線（策略可設定，見 `config.FORECAST_METHOD`）。
- **各時段平均人數**：最近 N 天每 10 分鐘時段的平均人數。
- **休館行事曆**：以月曆格子顯示本月每館登記的休館日。
- **中文 / 英文與淺色 / 深色模式**：從頁首切換，瀏覽器會記住。
- **依營業時間收集**：只在開館與閉館那一刻各記一筆 `count=0` 標記（用 `source_status` 區分，訓練時可排除）；
  抓取失敗會記成一列，絕不寫成誤導的 `0`。
- **手動登記休館**（國定假日、颱風假、整修），每館分開：手動維護的 `data/closures.csv`，收集器每次爬取前都會檢查，
  登記休館的場館當天完全不爬，另一館照常。休館原因（多天的話加日期區間）直接顯示在該館的卡片上。

## 技術

| 項目 | 工具 |
| --- | --- |
| 執行環境 | Python 3.12，用 [uv](https://docs.astral.sh/uv/) 管理 |
| 爬蟲 | `httpx`、`beautifulsoup4` |
| 資料 | `pandas`、CSV（長格式） |
| 網頁 / API | `FastAPI`、`uvicorn`、`Jinja2` |
| 圖表 | Apache ECharts + HTMX（CDN） |

## 開始使用

需要 Python 3.12 以上和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```bash
git clone https://github.com/Kaiweiyan/ntu-gym-tracker.git
cd ntu-gym-tracker
uv sync                      # 建立 venv、安裝套件和本專案的指令
```

### 執行收集器

```bash
uv run collector            # 常駐迴圈，對齊 :00/:10/:20…
uv run collector --once     # 只跑一輪（例如給 cron 每 10 分鐘呼叫）
```

要 24 小時運作可以用 cron、`systemd --user`（`scripts/ntu-gym-collector.service`）或 tmux，見 `docs/spec.md`。

### 執行網頁

```bash
uv run uvicorn ntu_gym_tracker.app:app --reload                     # 開發（http://localhost:8000）
uv run uvicorn ntu_gym_tracker.app:app --host 0.0.0.0 --port 8000   # 對外服務
```

## API

| Endpoint | 說明 |
| --- | --- |
| `GET /api/venues` | 場館列表（`id`、`name`、圖表 y 軸上限 `y_max`）。 |
| `GET /api/current` | 每館最新人數。 |
| `GET /api/forecast` | 實際 + 預測曲線；參數 `venue`、`day`。 |
| `GET /api/profile` | 每 10 分鐘時段的平均；參數 `venue`、`days`。 |

## 手動修改 CSV（`csv-tool`）

要手動修 `data/occupancy.csv`——更正錯誤的讀數、補上記得的數字、刪掉一批壞資料——`csv-tool` 會把 CSV 讀進
記憶體內的 SQLite 表，讓你用真正的 SQL 查詢 / 修改，不用直接編輯檔案。看過預覽並確認之前完全不會寫入；
寫入前一定先備份原檔（`data/occupancy.csv.bak.<時間戳>`）。

```bash
uv run csv-tool schema                             # 欄位名稱與型別
uv run csv-tool query "<一句 SELECT>"               # 唯讀，印出表格
uv run csv-tool exec "<一句 UPDATE/DELETE/INSERT>"   # 先預覽、確認後才寫入
```

範例：

```bash
# 列出目前所有抓取 / 解析失敗
uv run csv-tool query "SELECT * FROM occupancy WHERE source_status LIKE '%error%'"

# 修正一筆你知道真實人數的資料
uv run csv-tool exec "UPDATE occupancy SET current_count=45, source_status='ok'
    WHERE scraped_at='2026-07-01T12:10:00+00:00' AND venue_id='_parse'"

# 刪掉某一天的所有資料（例如清掉一批壞資料）
uv run csv-tool exec "DELETE FROM occupancy WHERE scraped_at LIKE '2026-07-10%'"
```

`scraped_at` 存的是 UTC，不是台北時間——但場館最早 08:00（台北）開，等於 UTC 00:00（見 `ntu_gym_tracker/hours.py`），
所以用 `LIKE '<日期>%'` 剛好對到台北的同一天。

`exec` 一次只接受一句（不能用 `;` 串多句）；如果載入後檔案被改過（你看預覽時收集器可能加了一列）就拒絕寫入；
寫入前一定先備份——改壞了，`mv occupancy.csv.bak.<時間戳> occupancy.csv` 就能還原。

## 登記休館（`data/closures.csv`）

健身中心和游泳池是兩個獨立的場館，休館也分開登記。國定假日、颱風假或整修——不管是事先知道還是當天公告——
手動在 `data/closures.csv` 加一列：

```csv
venue_id,date,end_date,reason
gym,2026-07-10,2026-07-11,颱風假
pool,2026-09-01,,單日清潔
,2026-12-25,,館內全面消毒
```

`venue_id` 填 `gym` 或 `pool` 表示只有該館休館，留空表示**所有**場館都休（整棟休館）；除此之外兩館互不影響，
登記健身中心休館不會影響游泳池。`end_date` 留空表示單日。

收集器每次爬取前逐館檢查這個檔案：登記休館的場館（今天或事先登記的未來日期）一律不爬，即使另一館開著、照常被爬。
它會改寫一筆 `venue_closed: <原因>` 標記，當天其餘時間跳過該館。檔案一更新，網頁就會在該館卡片上顯示原因
（多天的話加日期區間），不用等收集器下一輪。

如果某天在登記休館**之前**已經被正常爬到資料，下面的自動檢查會自動清掉（7 天內）。要手動修的話用 `csv-tool`，例如：

```bash
uv run csv-tool exec "UPDATE occupancy SET current_count=NULL,
    source_status='venue_closed: 颱風假' WHERE scraped_at LIKE '2026-07-10%' AND venue_id='gym'"
```

## 自動檢查休館公告（`daily-check`）

由 cron 每天執行兩次——07:30（開館前）和 22:30（閉館後）：

```bash
scripts/run_daily_check.sh               # cron 執行的就是這個；log 寫到 logs/daily_check.log
uv run daily-check --dry-run             # 只報告會做什麼，不改任何東西
```

1. **讀公告**：讀官網首頁的「新訊與公告」。每則公告有固定編號（`/news/?K=163`），處理過的記在
   `data/announcements_seen.json`，沒有新公告的日子完全不呼叫 AI。新公告（文字**和**圖片——每月的公告通常只有一張行事曆圖）
   交給無頭的 Claude Code，它只能讀這些檔案、必須用 JSON 回答；程式驗證答案後才把休館加進 `data/closures.csv`。
2. **回填**：最近 7 天內的休館日如果還留著正常爬到的資料（也就是登記之前爬的），換成一筆 `venue_closed` 標記，
   跟收集器當時若知道會寫的一模一樣。改之前先備份 `occupancy.csv`。
3. **Commit**：有變動就把資料檔 commit 成 `[Auto] ...`（不會 push），每一筆自動修改都能檢查、撤回。

設定：crontab 的寫法在 `scripts/run_daily_check.sh` 開頭。需要 `claude` CLI——在 `PATH` 上，或 VS Code 擴充套件內附的那一個（會自動找）。

## 專案結構

```
ntu_gym_tracker/      # 全部的 Python 程式：
                      #   app（FastAPI：JSON API + 網頁）、collector、daily_check、csv_tool、
                      #   config、scraper、parser、hours、closures、storage、models、
                      #   data_access、forecast_model、i18n
templates/ static/    # Jinja2 模板、CSS、分頁圖示
scripts/              # cron wrapper、systemd unit
data/                 # occupancy.csv（資料本體）、closures.csv（休館名單）、
                      # announcements_seen.json（daily-check 讀過的公告）
docs/                 # spec.md（現在的設計 + 專案規則）、plan.md（未定案提案）、
                      # CHANGELOG.md（版本紀錄）、settings.md（設定速查）、git-style.md（commit 格式）
CLAUDE.md             # 給 Claude Code 的工作守則（指向 docs/spec.md）
```

三個命令列工具註冊在 `pyproject.toml` 的 `[project.scripts]`，`uv sync` 會把它們裝進 venv，
用 `uv run collector`、`uv run daily-check`、`uv run csv-tool` 執行。

## 未來規劃

還在討論的提案（例如結合學校行事曆與 lag 特徵的機器學習預測）在 `docs/plan.md`。
各版本紀錄見 `docs/CHANGELOG.md`，現在的設計見 `docs/spec.md`。
