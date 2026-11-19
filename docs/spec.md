# NTU Gym Tracker Spec

## 專案規則

> **請先閱讀這份檔案。**

1. 所有檔案要更動前，必須先達成明確的共識。
2. 必須先在這份檔案完成說明，才可開始對應撰寫程式碼。
3. 每次實作前先 `git pull` 確認更新後再開始。
4. 這份檔案只寫現在的設計。過程和已完成的步驟放 `docs/CHANGELOG.md`；還沒定案的提案放 `docs/plan.md`。
5. 所有檔名與資料夾名稱一律使用英文。
6. 改設計時，要搜尋整份檔案裡提到同一件事的地方，一起改掉或刪掉，不可以只在新位置加一段。
7. 功能做完並試過之後，回頭清理這份檔案：刪掉實作順序和「尚未實作」的狀態，更新「先不做的」清單。
8. Commit 訊息一律依照 `docs/git-style.md` 的格式。
9. `README.md`（英文）和 `README.zh-TW.md`（中文）內容要一致，改其中一份時，另一份在同一個 commit 一起改。

### 文件分工

| 檔案 | 內容 |
| --- | --- |
| `docs/spec.md` | 現在的設計，以及設計背後的理由（本檔） |
| `docs/plan.md` | 還沒定案的提案；定案並實作後，內容搬進本檔、從 plan.md 刪掉 |
| `docs/CHANGELOG.md` | 每個版本改了什麼、過程與已完成的步驟 |
| `docs/settings.md` | 可調整的設定、目前的數值、要改哪個檔案（速查表） |
| `docs/git-style.md` | commit 訊息格式 |
| `README.md` / `README.zh-TW.md` | 給外部讀者的使用說明（英文 / 中文，兩份開頭互相連結） |
| `CLAUDE.md` | 給 Claude Code 的工作守則，每次對話自動載入（指向本檔） |

---

## 1. 總覽

收集臺大綜合體育館**健身中心**（`gym`）與**室內游泳池**（`pool`）的即時人數，存成 CSV，
再用網頁顯示即時人數、人數預測、各時段平均人數與本月休館行事曆。

```
collector（24/7，每 10 分鐘）──寫入──▶ data/occupancy.csv ──讀取──▶ app（FastAPI 網頁 + JSON API）
                 ▲ 先查                                ▲ 回填休館日
           data/closures.csv ◀──新增休館日── daily-check（cron，每天 07:30、22:30）
```

- 環境：Python 3.12，用 `uv` 管理。
- 套件：`httpx`、`beautifulsoup4`（爬蟲），`pandas`（資料），`FastAPI`、`uvicorn`、`Jinja2`（網頁），
  ECharts、HTMX（前端，走 CDN）。

### 1.1 專案結構與指令

所有 Python 程式都在 `ntu_gym_tracker/` 這個 package 裡，root 只放設定檔和資料夾。
三個命令列工具在 `pyproject.toml` 的 `[project.scripts]` 註冊成指令（`uv sync` 時安裝到 `.venv/bin/`），
所以要有 `[build-system]`（hatchling），讓 uv 把專案當成 package 以 editable 模式安裝。

| 指令 | 進入點 | 用途 |
| --- | --- | --- |
| `uv run collector` / `uv run collector --once` | `ntu_gym_tracker.collector:main` | 收集器（常駐 / 只跑一輪） |
| `uv run daily-check` / `uv run daily-check --dry-run` | `ntu_gym_tracker.daily_check:main` | 自動檢查休館公告 |
| `uv run csv-tool <schema\|query\|exec> ...` | `ntu_gym_tracker.csv_tool:main` | 用 SQL 手動修改 CSV |
| `uv run uvicorn ntu_gym_tracker.app:app` | `ntu_gym_tracker.app:app` | 網頁（ASGI app，由 uvicorn 啟動） |

```
ntu_gym_tracker/   # 全部 Python：app, collector, daily_check, csv_tool + config, hours, closures,
                   #   scraper, parser, storage, models, data_access, forecast_model, i18n
templates/ static/ # 網頁模板與 CSS、圖示（app.py 以絕對路徑找到它們，跟啟動時的工作目錄無關）
scripts/           # cron wrapper、systemd unit
data/ docs/ logs/  # 資料、文件、執行紀錄（logs 不進 git）
```

- 指令名稱不加專案前綴：它們只存在於本專案的 `.venv`，透過 `uv run` 執行，不會跟系統其他指令衝突。
- 不把指令放進子資料夾直接執行：從子資料夾跑 `python xxx.py` 時 Python 找不到 `ntu_gym_tracker`，得加路徑 hack。
- 需要 repo 根目錄的地方（`daily_check` 的 git commit、`config` 的 `data/`、`app` 的 `templates/` `static/`）
  一律由 `Path(__file__)` 往上推，不依賴工作目錄。

---

## 2. 資料來源

- 來源是 `https://rent.pe.ntu.edu.tw/`。人數**直接寫在伺服器產生的 HTML 裡**，不用執行 JS，
  一般的 `httpx` GET 就夠了。
- 頁面沒有 AJAX 或定時更新，數字只在整頁重新載入時改變，所以每次都重抓首頁。
- `robots.txt` 不存在（404），沒有宣告限制；仍然以低頻率（10 分鐘）和可識別的
  `User-Agent`（`config.USER_AGENT`，含聯絡方式）保持禮貌。
- 頁面結構：`.CMCList` 裡每個場館一個 `.CMCItem`，各有一個 `.IT`（名稱）和三個
  `.ICI > span`（依序為目前人數、最適人數、最大容納人數）。`.CMCList` 也被拿來當頁面其他地方的
  **空白區塊**，所以解析器以「有內容的 `.CMCItem`」為準，不依賴 `.CMCList`。
- 場館名稱對應 `venue_id`：`config.VENUE_ID_BY_NAME`（健身中心 → `gym`，室內游泳池 → `pool`）。
- 最適人數與最大容納人數是**固定值**（每次抓到都一樣），放在 `config.VENUE_CAPACITY`
  （gym 80 / 161，pool 50 / 130），不每次存。

---

## 3. 收集器（`ntu_gym_tracker/{collector,scraper,parser,storage,hours,closures}.py`）

### 3.1 執行方式

- `uv run collector`：常駐迴圈，對齊時鐘的 `:00/:10/:20…` 執行（`seconds_until_next_slot`，`INTERVAL_MIN = 10`）。
- 也可以只跑一輪（`uv run collector --once`），給 cron（`scripts/run_collector.sh`）或 `systemd --user`
  （`scripts/ntu-gym-collector.service`，搭配 `loginctl enable-linger` 登出後繼續跑）使用。

### 3.2 每一輪的判斷順序

1. **手動休館**（第 5 節）：當天有登記休館的場館，只在原本的開館時刻寫一筆休館標記，其餘時間跳過。
2. **營業時間**（`hours.py`，`Asia/Taipei`，兩館共用）：

   | 星期 | 開放時間 |
   | --- | --- |
   | 一～五 | 08:00–22:00 |
   | 六 | 09:00–22:00 |
   | 日 | 09:00–18:00 |

   非營業時間完全不爬，**只有開館與閉館那一刻**各寫一筆 `current_count=0` 的標記
   （`source_status` 為 `open` / `closed`）。這樣圖表曲線在閉館時能回到 0、開館時不會殘留前一天的數字，
   又不必把每個非營業時段都補 0（那會拉低平均）。
3. **爬取**：一次抓首頁就能拿到所有場館，所以只要有任何一館開著就會抓；休館中的場館即使頁面有數字也會被濾掉。
   所有場館都休館時才完全不抓。

### 3.3 失敗處理

- **失敗寫成一列，不留空洞，也不補 0。** 抓取或解析失敗時寫一列 `current_count` 為空、
  `source_status` 為 `fetch_error: ...` 或 `parse_error: ...`。
- `scrape()` 把「抓取 + 解析」當成一組重試，最多 `config.FETCH_RETRIES`（3）次，
  指數退避（`config.FETCH_RETRY_BACKOFF_SECONDS × 2^attempt`）。暫時性的 HTTP 錯誤
  （逾時、連線失敗、5xx）和暫時性的解析失敗（頁面一時沒載完）都無法跟真正的故障區分，
  所以一律再試一次。
- 重試**沿用本輪原本的 `scraped_at`**：重試是重新取樣同一個時間點，不是把時間點往後挪。
  人數是即時值，錯過的那一刻事後補不回來，重試只能救本輪。
- `scraped_at` 存 UTC，精度到整秒。

---

## 4. 資料儲存（`data/`）

| 檔案 | 內容 | 進 git |
| --- | --- | --- |
| `occupancy.csv` | 人數紀錄，只會往後加。長格式：一列 = 一個場館的一個時間點 | 是（當作異地備份） |
| `closures.csv` | 手動休館名單（第 5 節） | 是 |
| `announcements_seen.json` | `daily-check` 已處理過的公告編號 | 是 |
| `*.csv.bak.<時間戳>` | 修改 `occupancy.csv` 前的自動備份 | 否（`.gitignore`） |

不使用資料庫，CSV 就是資料來源。

`occupancy.csv` 欄位：`venue_id, venue_name, scraped_at, current_count, source_status`。

`source_status` 的值：

| 值 | 意思 | `current_count` | 計入平均與預測 |
| --- | --- | --- | --- |
| `ok` | 正常抓到的人數 | 數字 | 是 |
| `open` / `closed` | 開館、閉館那一刻的標記 | 0 | 是（見第 11 節已知限制） |
| `fetch_error: ...` | 網站連不上 | 空 | 否 |
| `parse_error: ...` | 抓到頁面但解析失敗（可能是網站改版） | 空 | 否 |
| `venue_closed: <原因>` | 登記休館的那一天 | 空 | 否 |
| `sensor_error: <原因>` | 場館有開但現場計數器故障（只由人工標記，程式不會產生） | 空 | 否 |

`current_count` 為空的列一律在讀取時被 `data_access._occupancy_ok()` 排除，
所以新增一種「這筆不算」的狀態時，下游不必再加過濾邏輯。

---

## 5. 休館管理

### 5.1 手動休館名單（`data/closures.csv`、`ntu_gym_tracker/closures.py`）

國定假日、颱風假、整修都不在固定的每週營業時間表裡，也無法從人數資料可靠地推測，所以用一份手動維護的名單：

```csv
venue_id,date,end_date,reason
gym,2026-08-17,2026-09-29,整修
,2026-10-23,2026-10-24,颱風假
```

- **兩館各自獨立**：`venue_id` 填 `gym` 或 `pool` 只影響該館；**留空 = 所有場館**（例如整棟休館），
  不用每館各寫一列。
- `end_date` 留空 = 單日；有填 = 區間（含頭尾）。
- `reason` 留空時，讀取時（`closures._load_rows()`）補成「未說明原因」，避免出現空白的狀態或卡片。
- `closures_on(d)` 回傳 `{venue_id: Closure}`，一次查完每個場館自己的列和留空的列；
  `closures_between(start, end)` 給行事曆用，CSV 只讀一次。
- 名單一改，網頁馬上反映（每次請求都重讀），不用等收集器。

### 5.2 收集器的處理

休館當天，每個休館的場館在原本的開館時刻（`is_opening_tick()`）寫**一筆**
`current_count` 為空、`source_status="venue_closed: <原因>"` 的標記，當天其餘時間都不爬該館。
今天或事先登記的未來日期都一樣；沒休館的場館照常每 10 分鐘爬取。

### 5.3 自動檢查（`ntu_gym_tracker/daily_check.py`、`scripts/run_daily_check.sh`）

ws3 上的 cron 每天 07:30（開館前）和 22:30（閉館後）各跑一次。隔天休館的公告通常前一晚才貼。

1. **讀公告**：首頁「新訊與公告」每則都有固定編號（`/news/?K=<id>`）。處理過的編號記在
   `announcements_seen.json`；沒有新公告就**完全不呼叫 AI**。首頁只列最近約 7 則，一天兩次夠用。
   - Python 負責抓公告內文（搜尋表單的「清除」按鈕到「相關連結」之間）和所有 `/edimg/` 圖片——
     每月的營業公告常常只有一張行事曆圖，沒有文字。
   - 交給無頭的 `claude -p --model sonnet --restricted --tools Read`，工作目錄是只放那些圖片的暫存資料夾，
     所以它只能讀圖、不能改任何東西，輸出只能是 JSON `{"closures": [...], "notes": [...]}`。
   - 只算**健身中心、室內游泳池或整棟綜合體育館**的**整天**休館；半天的異動寫進 `notes`，只記 log、不套用。
   - `validate()` 檢查後才寫入 `closures.csv`：`venue_id` 是已知場館或留空、日期能解析、`end >= start`、
     區間 ≤ 60 天、不早於回填範圍、不超過一年後；已經完全涵蓋的休館不重複加。
     這麼嚴格是因為風險不對稱：誤加一天休館會讓收集器整天不爬，那天的資料永遠補不回來；
     漏加只會留下事後可以清掉的資料。
   - 解析成功後才把編號標成已讀，所以呼叫失敗下一輪會重試。
2. **回填**（固定規則，不用 AI）：最近 `RECONCILE_DAYS`（7）天內的休館日，若該館還有不是 `venue_closed`
   的列（代表登記前就被正常爬了），整天的列換成一筆開館時刻的休館標記，放在原本第一筆的位置（保持時間順序）。
   - 完全沒有資料的日子不動——07:30 那輪今天還沒有資料，收集器到開館時刻會自己寫標記。
   - 先備份，寫到暫存檔再 `os.replace`；取代前再檢查一次 mtime，若收集器剛好寫入就跳過，下一輪再做。
     重跑結果相同。
3. **Commit**：休館名單或人數資料有變時，`git commit -- <三個資料檔>`，訊息為 `[Auto] ...`。
   用 pathspec 所以只會 commit 這三個檔，即使有其他已 stage 的變更。**不會 push。**

- `claude` CLI：先找 `PATH`，找不到就用 VS Code 擴充套件內附的
  `~/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude`，取版本最新的
  （路徑每次更新都會變，所以每次執行時重新搜尋）。用的是使用者的訂閱額度，不需要 API key。
- cron 的環境很精簡，wrapper 會設定 `PATH` 和 UTF-8 locale；log 寫到 `logs/daily_check.log`（不進 git）。
- `uv run daily-check --dry-run` 只報告會做什麼，不改任何東西。

### 5.4 事後補登

- 7 天內：登記後下一次自動檢查會回填。
- 超過 7 天：用 `uv run csv-tool exec` 手動改，例如
  `UPDATE occupancy SET current_count=NULL, source_status='venue_closed: 颱風假' WHERE scraped_at LIKE '2026-07-10%' AND venue_id='gym'`。

---

## 6. 手動編輯 CSV（`uv run csv-tool`，`ntu_gym_tracker/csv_tool.py`）

用 SQL 手動修 `occupancy.csv`（補一筆已知的數字、刪掉一批壞資料），不用直接改檔案。

- 指令：`schema`、`query`（唯讀 `SELECT`）、`exec`（一句 `UPDATE` / `DELETE` / `INSERT`）。`--help` 或格式錯誤時印出說明。
- 刻意**不是**通用的 SQL shell：`exec` 拒絕 `;` 分隔的多句，讓差異預覽可信；`query` 的連線不會傳給寫檔函式，所以不可能寫入。
- 整份 CSV 讀進**記憶體內的 SQLite 表**（只有幾千到幾萬列），直接得到完整的 `WHERE` 語意，不用自己寫過濾語法。
- `exec` 流程：讀檔 → 以 SQLite `rowid` 拍快照 → 執行 → 再拍快照 → 比對（只在前 = 刪除、只在後 = 新增、
  兩邊都有但不同 = 修改）→ 印出差異 → 問 `y/N`。**回答 `y` 之前完全不碰檔案**，中斷或當掉都不影響原檔。
- 檔案 mtime 檢查放在 `y/N` **之後**：真正的競爭窗口是人盯著提示猶豫的那段時間，收集器可能剛好寫入。
- 確認後先用 `shutil.copy2` 備份成 `<檔名>.bak.<UTC 時間戳>`，再覆寫。
- 寫回時沿用 `storage.CSV_FIELDS` 和 `storage._blank()`，跟收集器寫的格式完全相同。
- 寫回依 `rowid` 排序，保留原本順序；`INSERT` 的列一律排在最後，不會依 `scraped_at` 插到中間。
- `scraped_at` 是 UTC，但場館最早 08:00（台北）開，等於 UTC 00:00，所以用 `LIKE '<日期>%'` 剛好對到台北的同一天。

---

## 7. 網頁後端（`ntu_gym_tracker/{app,data_access,forecast_model,i18n}.py`）

FastAPI + Jinja2。

| 路徑 | 內容 |
| --- | --- |
| `GET /api/venues` | 場館列表（`id`、`name`、圖表 y 軸上限 `y_max`） |
| `GET /api/current` | 各場館最新人數、今天的休館資訊、是否非營業時間 |
| `GET /api/forecast?venue=&day=today\|tomorrow` | 實際人數 + 預測曲線 |
| `GET /api/profile?venue=&days=` | 最近 N 天各 10 分鐘時段的平均人數 |
| `GET /` | 主頁 |
| `GET /partials/current` | 即時人數卡片（HTMX 片段，每 60 秒重抓） |
| `GET /partials/calendar` | 本月休館行事曆（HTMX 片段） |

JSON API 不分語言；HTML 頁面依 `lang` cookie 輸出中文或英文（第 8.4 節）。

- **快取**：`_load_csv()` 以檔案 mtime 為 key 快取解析好的 DataFrame（讀檔、解析、UTC 轉台北、算星期與小時），
  收集器寫入後才重算。之後的聚合（dropna / groupby）不快取，以目前資料量都在毫秒以下。
- **`_occupancy_ok()`**：保留有人數的列（`ok` 和開/閉館的 0 標記），去掉人數為空的列。
- **忙碌度**（`get_current`）：跟該場館**自己的最適人數**比——`quiet`（< 50%）、`normal`（50–100%）、
  `busy`（> 100%）。回答的是「現在擠不擠」，不是「比平常這個時段擠嗎」。
- **預測**（`forecast_model.py`）：
  - 所有預測邏輯都在這裡；`data_access.get_forecast()` 只負責取出該場館的資料再呼叫
    `forecast_model.forecast(df, day)`。`forecast_model` 不讀檔，方便替換與測試。
  - 基準線由 `config.FORECAST_METHOD` 選 `forecast_model._STRATEGIES` 裡的一個：
    - `same_weekday_mean`（預設）：過去所有同星期幾的日子，每個時段的平均。
    - `recent_mean`：最近 `FORECAST_RECENT_DAYS`（30）天每個時段的平均，不分星期。
  - 要加新方法：寫一個 `(df, target_date) -> {時段: 平均}` 的函式並註冊進 `_STRATEGIES`，`forecast()` 本身不用改。
  - 預測線畫**整天**（不只畫「現在之後」），讓實際值和預測重疊，使用者能直接看出準不準。
  - 實際值中 ≤ 2 格（20 分鐘）的缺口線性內插（`_fill_short_gaps`，`MAX_INTERP_GAP_SLOTS`）；更長的缺口是真的故障，留空。
  - 顯示的數字一律四捨五入成整數（`_round_ints`）。
  - `slot_means()`（以 10 分鐘時段平均）也被 `data_access.get_profile()` 拿來算平均人數圖。
- **休館顯示**：手動休館是每館各自的，營業時間是兩館共用的，所以分兩塊：
  - `app._venue_closures()`：今天的 `{venue_id: {reason, range, multi_day}}`，顯示在**該場館的卡片上**
    （「休館中」+ 原因，多天時再加日期區間）。即使在營業時間內也照樣顯示——國定假日下午兩點應該說明原因。
  - `app._scheduled_closure()`：一般非營業時間，頁面上方一條「🌙 目前非開放時間」橫幅，兩館共用。
  - 卡片沒有手動休館時：非營業時間顯示通用的「休館中」（沒有原因與日期），營業時間顯示人數與忙碌度。
    休館中的卡片不顯示「0 人」。

---

## 8. 前端（`templates/`、`static/`）

### 8.1 版面

`index.html` 由上到下：現在人數（卡片，HTMX 每 60 秒更新）→ 場館選擇（一組全域按鈕，控制下面所有圖表）
→ 人數預測（今天 / 明天）→ 各時段平均人數（1 / 3 / 7 / 30 天）→ 休館行事曆。

- 全域場館選擇（`.venue-select`）刻意做得比圖表內的切換鈕（`.venue-toggle`）大、形狀不同（圓角矩形 + 陰影、粗體），
  避免看起來只是某張圖的選項。兩者共用 `wireToggle()`。
- 手機版（≤ 600px）圖表與行事曆會縮小，左右留 16px。

### 8.2 圖表

- 預測圖與平均人數圖的 **y 軸固定**為 0 到 `config.CHART_Y_MAX[venue]`，沒設定就用 `max_capacity`
  （目前 gym 161、pool 80），經由 `list_venues()` 放在場館按鈕的 `data-y-max`。
  不固定的話，切換今天 / 明天時，高峰 70 和 120 的兩天會畫得一樣高，看起來一樣擠。
  預設用最大容納人數，因為它不會變，也能看出離滿還有多遠；游泳池實際高峰約 80 / 130，所以上限設 80，免得曲線只用到下半部。
- `max` 是函式 `Math.max(上限, dataMax)`：某天真的超過上限時拉高 y 軸，而不是把曲線切掉。
- 兩張折線圖較高（`.chart-tall`，480px，手機 400px），讓曲線不會太扁。

### 8.3 休館行事曆（`partials/calendar.html`、`app.partial_calendar`）

- **只顯示本月**，不能切換月份——用途是「這個月哪天沒開」，不是查歷史。
- 由伺服器產生（跟卡片一樣，因為要依語言 cookie 輸出）。週日開頭的完整週
  （`calendar.Calendar(firstweekday=6)`），前後月份的補位日變淡且不標休館。
- 不受全域場館選擇影響，**兩館都顯示**，用短標籤（健身 / 泳池）；兩館同一原因休館時合併成一個「全館」。
- 休館日是紅色（`--closed`，跟台灣日曆的假日一樣），用深淺表示休了多少：**所有場館都休**是實心紅底
  （原因不同也算），**只休一館**是淡紅底，文字用較深的 `--closed-ink` 確保對比。
  配色刻意降低彩度，跟整體風格一致。
- 格子下方有圖例，以及本月休館清單（相同原因的連續日期合併，如「9/1–9/29 健身 整修」），因為手機格子約 45px，放不下多少字。
- 今天用 `outline` 標示（不是 border），所以休館日的邊框蓋不掉它。

### 8.4 語言與深色模式

右上角兩組互相獨立的切換鈕，可組合成四種。兩者都記在瀏覽器，但刻意存在不同地方：

- **語言 → `lang` cookie**（一年，`SameSite=Lax`），JS 設定後重新載入。文字由伺服器產生，連每 60 秒重抓的
  `/partials/current` 也是，只有 cookie 會隨這些請求送到伺服器，`localStorage` 不會。
  - `app._ui()` 讀 cookie（缺少或不合法 → 中文），把 `t = i18n.STRINGS[lang]` 傳給模板；圖表標籤用
    `i18n.js_strings(lang)` 傳給頁面 JS。
  - 場館名稱抓下來是中文，英文模式依 `venue_id` 對應（Gym / Indoor Pool）。
  - 休館原因是 `closures.csv` 的自由文字，**不翻譯**，照原樣顯示。
- **主題 → `localStorage`**，純前端。`<head>` 的內嵌 script 在樣式繪製前設定 `<html data-theme>`，
  深色頁面不會先閃一下淺色；沒選過就跟系統的 `prefers-color-scheme`。
  - CSS 在 `:root[data-theme="dark"]` 換掉顏色變數（藍色也是變數 `--accent`）。
  - ECharts 的主題在 `init()` 時就固定，所以切換時發出 `themechange` 事件，`index.html` 銷毀並以內建的
    `dark` 主題重建圖表，`backgroundColor: "transparent"` 讓卡片底色透出。

### 8.5 靜態檔版本號

`base.html` 引用 `/static/style.css?v={{ static_version }}` 和 `/static/favicon.svg?v=...`，
`app._static_version()` 回傳 style.css 的 mtime。網址不變的話，瀏覽器或中間的代理（Cloudflare Tunnel）
會一直用舊的快取；每次修改都換網址，就不用手動強制重新整理。分頁圖示是 `static/favicon.svg`（藍底白色啞鈴）。

---

## 9. 部署

- 全部跑在一台常駐的學校工作站（ws3）。不用 GitHub Actions：臨時的 CI runner 存不了本地檔案。
- 收集器：tmux session `gym`，`uv run collector`。網頁：tmux session `web`，
  `uv run uvicorn ntu_gym_tracker.app:app --host 0.0.0.0 --port 10307`
  （沒有 `--reload`，改了網頁程式要重啟），經 Cloudflare Tunnel 對外公開。
- 自動檢查：同一台的 crontab，07:30 與 22:30 執行 `scripts/run_daily_check.sh`（設定方式在該檔開頭）。
- 收集器也支援 cron 與 `systemd --user`（第 3.1 節）。

---

## 10. 注意事項

- Starlette 的 `TemplateResponse` 參數順序是 `(request, name, context)`；把 `name` 放第一個，
  它會默默把 context dict 當成模板名稱。
- pandas + pyright：用 `df.loc[mask, col]` 或 `.query(...)`，不要用 `df[mask]`（型別是 `DataFrame | Series`，
  型別檢查很吵）；pandas 純量經過小 helper（如 `_mean`）再運算；需要時用 `typing.cast`。
- 歷史資料的 `scraped_at` 精度不一致（有的有微秒、有的沒有），一律用
  `pd.to_datetime(..., format="ISO8601")` 解析，固定格式字串會在其中一種上失敗。
- `occupancy.csv` 的換行是 CRLF（Python `csv` 模組預設），`closures.csv` 是 LF；用 shell 工具處理時要注意。
- 狀態是 `ok` 的資料也可能是錯的：健身中心的現場計數器曾連續數天回報 0，但場館其實有開。
  這不是爬蟲問題，也不是休館，所以標成 `sensor_error: ...`（人數清空）。目前只能從儀表板上看到
  一整段平平的 0 才發現，程式不會自動偵測。
- `config.py` 的 `USER_AGENT` 含有私人信箱，不要把它貼到外部服務。

---

## 11. 已知限制

- `get_profile()` / `get_forecast()` 的平均也算進了開/閉館的 0 標記，所以每天第一個和最後一個時段的平均永遠是 0。
  （修正方式的提案見 `docs/plan.md`。）
- `same_weekday_mean` 在歷史資料少的星期，某些時段可能沒有值，會隨資料累積補上。
- 沒有自動化測試。
- 計數器故障不會被自動偵測（第 10 節）。
- 自動檢查會 commit 但不 push，GitHub 上的資料會落後工作站，需要手動 push。

---

## 12. 先不做的

| 項目 | 原因 |
| --- | --- |
| 帳號系統、個人化 | 只是查人數，不需要 |
| 手機 App | 網頁已支援手機版 |
| 資料庫（SQLite / Postgres） | 資料量小，CSV 夠用、好備份、好手動修 |
| 無頭瀏覽器（playwright） | 人數在 HTML 裡，一般 GET 就夠 |
| 用「連續多格 0 人」推測休館 | 不可靠，已改為手動名單 + 自動讀公告 |
| 半天休館 | `closures.csv` 只描述整天；半天的異動只記 log |
| 翻譯休館原因 | 原因是自由文字 |
| 行事曆切換月份 | 只回答「這個月哪天沒開」 |
| 星期 × 時段熱力圖、歷史趨勢圖 | 資訊跟平均人數圖重複，已移除 |
| 天氣資料 | 已移除 |
| 預先聚合（每小時 bucket） | 目前查詢在毫秒以下；資料量大到變慢時再考慮 |
| 營業時間每館各自設定 | 兩館目前時間相同；各自的例外用休館名單處理 |
