# 更新紀錄

這裡記錄每個版本改了什麼，以及當時的過程與原因。
格式參考 [Keep a Changelog](https://keepachangelog.com/)，版本號採用 [Semantic Versioning](https://semver.org/)。
現在的設計寫在 `docs/spec.md`。

## [未發布]

### 新增
- **休館行事曆**，放在頁面最下方（原本熱力圖的位置）：以月曆格子顯示本月 `closures.csv` 登記的休館，
  每館分開標示（健身 / 泳池；兩館同一原因休館時合併成「全館」）。休館日是紅色——所有場館都休館是實心，
  只休一館是淡色——附圖例，格子下方再列一次本月休館清單（連續日期合併成區間）。
  由伺服器產生的 HTMX 片段（`/partials/calendar`），所以會跟著語言設定。
- **英文模式與深色模式**，在頁首各自切換（中文/EN、☀️/🌙），瀏覽器會記住。語言存在 `lang` cookie
  （文字由伺服器產生，包含 HTMX 的卡片片段），字串集中在 `ntu_gym_tracker/i18n.py`。主題存在
  `localStorage`，在第一次繪製前就套用，深色頁面不會先閃一下淺色；沒選過就跟系統設定。切換時圖表改用
  ECharts 的深色主題重畫。`closures.csv` 的休館原因照原樣顯示，不翻譯。
- **自動檢查休館公告**（`daily_check.py`，cron 每天 07:30 與 22:30 經 `scripts/run_daily_check.sh` 執行）：
  讀官網的新公告（以編號記在 `data/announcements_seen.json`，沒有新公告就不呼叫 AI），交給無頭的
  Claude Code——只能讀公告的文字和圖片——以 JSON 回答整天的休館，程式驗證後寫進 `data/closures.csv`。
  接著把最近 7 天內休館日卻已正常爬到的資料（登記太晚）換成一筆 `venue_closed` 標記，改之前先備份
  `occupancy.csv`，有資料變動就 commit 成 `[Auto] ...`。
- **`sensor_error: <原因>`**：新的 `source_status` 值，寫在 `models.py` 和 `csv_tool.py` 的 schema 說明裡。
  用於爬取本身成功（`ok`）但現場計數器數字明顯錯誤的情況——跟 `fetch_error` / `parse_error`（爬取成功了）
  和 `venue_closed`（場館其實有開）都不同。跟其他人數為空的狀態一樣，自動排除在歷史平均與預測之外。
  程式不會產生這個狀態，只在事後發現時用 `csv_tool.py` 手動標記。

### 移除
- **星期 × 時段熱力圖**，連同 `GET /api/heatmap` 與 `data_access.get_heatmap()`（對 API 使用者是破壞性變更）。
  原位置改放休館行事曆。

### 變更
- 預測圖與平均人數圖改成每館固定的 y 軸，不再自動縮放（健身中心 0–161 = 最大容納人數；游泳池用新的
  `config.CHART_Y_MAX` 設成 0–80，因為它從來不會接近 130）。切換今天 / 明天或天數範圍時刻度不再變動，
  曲線一樣高就代表人數一樣多。只有某天資料真的超過上限才會拉高。`/api/venues` 多了各館的 `y_max`。
- 預測圖與平均人數圖變高（480px，手機 400px），固定上限後曲線不會太扁。
- 瀏覽器分頁圖示改成藍色啞鈴（`static/favicon.svg`），不再是 emoji。
- 文件：`docs/spec.md` 改用中文重寫，只描述現在的設計，最上面加上專案規則（每次實作前必讀）；
  `docs/plan.md` 從原本的規劃書改成記錄未定案提案；`CHANGELOG.md` 改用中文並移到 `docs/`；
  新增 `docs/git-style.md`（commit 格式）與 `CLAUDE.md`。README 分成英文 `README.md` 與中文 `README.zh-TW.md`，
  開頭互相連結。
- **root 的四個 `.py` 搬進 `ntu_gym_tracker/` package**，命令列工具改用 `pyproject.toml` 的 `[project.scripts]`
  註冊（加上 hatchling build-system，讓 uv 以 editable 模式安裝本專案）。指令改為：
  `uv run collector.py` → `uv run collector`、`uv run daily_check.py` → `uv run daily-check`、
  `uv run csv_tool.py` → `uv run csv-tool`、`uvicorn app:app` → `uvicorn ntu_gym_tracker.app:app`。
  `app` 的 `templates/` `static/` 與 `daily_check` 的 repo 路徑改成由檔案位置推算，跟工作目錄無關。
  cron wrapper 與 systemd unit 已同步更新；升級後要先 `uv sync` 才會有新指令。

### 修正
- 資料清理：健身中心的現場計數器在 2026-09-30～10-05 這 6 天回報平平的 0 或明顯偏低的數字，但場館其實有開，
  改標成 `sensor_error`（474 列）；游泳池兩個孤立的單日 0（09-25、09-28，跟休館無關）直接刪除。
  兩者都用 `csv_tool.py` 修改，寫入前已自動備份 `occupancy.csv`。

## [0.5.0] - 2026-08-07 — 手動休館、移除天氣

### 新增
- **手動登記休館，每館獨立**：`data/closures.csv`（`venue_id, date, end_date, reason`，手動編輯）取代下面移除的
  臨時休館推測。健身中心與游泳池各有各的休館行程——一列的 `venue_id` 指定一個場館，留空則所有場館一起休館
  （例如整棟休館）。收集器每次爬取前逐館檢查：登記休館的場館（今天或事先登記的未來日期）一律不爬，
  其他場館照常；改為寫一筆 `current_count=NULL, source_status="venue_closed: <原因>"` 的標記，當天其餘時間跳過。
  用 `NULL` 而不是 `0`，這些列就跟抓取 / 解析失敗一樣自動排除在所有歷史平均與預測之外，不用加新的過濾邏輯。
  各館卡片在「休館中」下方顯示自己的原因（多天的話加日期區間）；共用的非營業時間橫幅（🌙 目前非開放時間）
  不分場館，維持一條。已經正常爬到資料、事後才登記休館的日子，當時要用 `csv_tool.py` 手動修
  （之後由自動檢查處理 7 天內的部分，見「未發布」）。

### 移除
- **臨時休館推測**（0.4.1 加的「所有場館連續 3 格以上都是 0」規則）：一般冷清的時段太容易被誤判成休館，
  也不如直接登記已知的休館日。`data_access._suspected_closure_dates()`、`_historical_ok()`、`get_closure_notice()`
  移除；`get_profile()` / `get_heatmap()` / `get_forecast()` 改回直接讀 `_occupancy_ok()`。
  `forecast_model.forecast()` 拿掉 `hist` 參數（只剩 `df`）。共用的非營業時間橫幅（`app._scheduled_closure()`）
  仍處理一般非營業時間（這部分跟推測無關），但不再有臨時休館 / 颱風的版本，改由上面的手動休館處理。
  移除 `config.SUSPECTED_CLOSURE_MIN_SLOTS`。
- **天氣**：整個移除。`ntu_gym_tracker/weather.py`、`models.WeatherObservation`、
  `data_access.get_current_weather()` 及其約 10 分鐘的快取、`config.NTU_LATITUDE` / `NTU_LONGITUDE` /
  `OPEN_METEO_URL` / `OPEN_METEO_CURRENT_FIELDS` 都刪除。`/api/current` 和 `/partials/current` 不再回傳 / 顯示
  `weather`；頁面上的即時天氣列與 `.weather` CSS 也移除。

### 修正
- `closures.csv` 的 `reason` 留空時，讀取時補成「未說明原因」，不再原樣傳下去——以前會在 CSV 產生只有
  `venue_closed: ` 的狀態，頁面上也會出現空白的原因列。
- `csv_tool.py` 的 `schema` 輸出漏了 `venue_closed: <原因>`（休館功能比 `csv_tool.py` 晚加，說明沒跟上）。
- `ntu_gym_tracker/__version__` 從第一版起一直是 `"0.1.0"`，跟 `pyproject.toml` 的版本無關，現在保持同步。

## [0.4.2] - 2026-08-02 — csv_tool.py

### 新增
- **`csv_tool.py`**：用 SQL 查看、手動修改 `data/occupancy.csv` 的命令列工具——`schema`、`query`（唯讀 `SELECT`）、
  `exec`（一句 `UPDATE` / `DELETE` / `INSERT`，寫入前先預覽並確認）。把 CSV 讀進記憶體內的 SQLite 表，
  讓 `exec` 有完整的 `WHERE` 語意；寫入前一定先備份原檔；如果在你考慮的期間檔案被改過就拒絕寫入
  （檢查放在確認提示**之後**，因為真正的競爭窗口在那裡——你看差異時收集器可能剛好加了一列）。
  參數缺少 / 不合法以及 `--help` 都會印出完整說明，不只一行用法。範例見 README 新增的「Manual CSV editing」一節。

### 修正
- `forecast_model._fill_short_gaps` 的 pyright 錯誤（對 `float | None` 做運算）：迴圈的邊界已經保證
  `left` / `right` 不是 `None`，但經過 list 索引後型別檢查器看不出來，加上明確的 `assert` 縮小型別。

## [0.4.1] - 2026-07-12 — 臨時休館推測、重試修正、拆出 forecast_model

### 新增
- **臨時休館推測**：所有場館連續 3 格以上（每格 10 分鐘）都是真實的 0 時（例如颱風天），頁面顯示「可能臨時休館」
  橫幅，該日也從 `/api/profile`、`/api/heatmap` 和預測基準線的歷史平均中排除，避免拉低平均。
  這些日子不在固定的每週營業時間表（`hours._HOURS`）裡，是從資料推測出來的，不是維護的行事曆。
  （0.5.0 移除。）
- 休館橫幅也涵蓋一般非營業時間（每天晚上、週日傍晚……），不只臨時休館 / 颱風——`app._closure_notice()`
  先檢查確定的 `hours.is_open()`，再退回臨時休館推測。
- 有休館提示時，即時人數卡片不再顯示誤導的「0 人」——人數 / 百分比 / 忙碌度改成單純的「休館中」
  （場館名稱和最後更新時間照常顯示）。

### 變更
- **預測邏輯移到 `ntu_gym_tracker/forecast_model.py`**：基準線、策略表、補缺口、四捨五入都從 `data_access.py`
  搬到獨立模組，它只吃 DataFrame、不負責讀 CSV——之後放機器學習模型的地方。`data_access.get_forecast()`
  變成薄薄一層，取出原始與排除休館後的 DataFrame 再交給 `forecast_model.forecast()`。行為不變。

### 修正
- **重試沒有涵蓋解析失敗**：`fetch_html()` 的重試只包住 HTTP GET，所以抓取成功（200 OK）但解析失敗
  （例如頁面一時沒載完）會直接記成 `parse_error`，完全沒重試。現在 `scrape()` 把抓取 + 解析當成一組重試，
  最多 `FETCH_RETRIES` 次並退避，每次都沿用本輪原本的 `scraped_at`。
- **`_suspected_closure_dates` 的 pyright 錯誤**：pandas `Series.items()` 的索引型別是 `Hashable`，
  `slot - prev_slot` 和 `slot.date()` 過不了型別檢查。使用前明確轉成 `pd.Timestamp`。

## [0.4.0] - 2026-07-09 — 預測策略、熱力圖修正、頁面整理

### 新增
- **可設定的預測基準線**（`config.FORECAST_METHOD`）：`same_weekday_mean`（預設——過去所有同星期幾的日子，
  每個時段的平均）或 `recent_mean`（最近 N 天的平均）。以策略表實作（`data_access._FORECAST_STRATEGIES`），
  之後加模型不用改 `get_forecast()`。
- 預測圖的基準線畫**整天**，不只畫「現在之後」，讓實際值與預測重疊，一眼看出準不準。
- 靜態檔加版本號（`/static/style.css?v=<mtime>`），改 CSS / JS 後一般重新整理就會生效，不用強制重新整理。

### 變更
- **忙碌度**（`get_current`）改成跟該場館自己的 `optimal_count` 比的絕對等級（`quiet` < 50%、`normal` 50–100%、
  `busy` > 100%），不再跟該時段的歷史平均比。
- 預測與平均人數的數字四捨五入成整數顯示。
- 熱力圖的小時標籤改成區間（`"08-09"`），拿掉多餘的「時」軸標題。
- 頁面區塊順序：現在人數 → 場館選擇 → 人數預測 → 各時段平均人數 → 熱力圖。場館選擇改成獨立、外觀不同的全域控制
  （較大、形狀不同），不再放在熱力圖區塊裡。
- `data_access.py` 內部整理（函式順序對應路由順序、私有 helper 集中）——除了下面的修正外行為不變。
- `spec.md` 改寫成依架構組織的參考文件，不再是依時間排列的變更紀錄。

### 移除
- **破壞性變更：** `GET /api/history` 與「人數趨勢」圖。

### 修正
- 熱力圖的格子畫在錯的小時標籤下（索引錯誤——把原始小時數字當成座標軸的位置），最後幾個營業小時的格子也沒畫出來。
- 熱力圖的平均不再包含開 / 閉館的 `count=0` 標記，它們會造出一欄永遠是空的「22:00」，也稀釋開館那一小時的平均。

## [0.3.1] - 2026-07-02 — 穩定性與 schema 整理

### 新增
- **抓取重試與退避**：`scraper.fetch_html()` 遇到暫時性的 HTTP 失敗（逾時 / 連線錯誤 / 5xx）會以指數退避重試最多 3 次，
  之後才記成 `fetch_error`。這只能救同一輪內的暫時失敗——從沒取到的讀數補不回來。
- **預測補缺口**：`get_forecast()` 的「實際」數列，對兩側都有真實讀數的短缺口（最多連續 2 格 10 分鐘）做線性內插；
  更長的中斷留空，不假裝有資料。

### 變更
- **optimal_count / max_capacity 移到設定檔**：每館固定（從第一天起都沒變），改放 `config.VENUE_CAPACITY`，
  不再每列都抓、都存。`data/occupancy.csv` 的 schema 改為
  `venue_id, venue_name, scraped_at, current_count, source_status`；既有檔案原地遷移（刪欄位，資料不變）。
- **scraped_at 精度改到整秒**（原本到微秒）——10 分鐘一次的收集不需要那麼細。

## [0.3.0] - 2026-07-01 — 人數預測

### 新增
- 即時人數下方加上**人數預測**圖（`GET /api/forecast`）：今天 / 明天切換（附日期與星期），實線的實際值（開館 → 現在）
  接上虛線的預測（現在 → 閉館），並標出「現在」。基準線 = 所有日子每 10 分鐘時段的歷史平均；之後換成模型不用改前端。
- 每張跟場館有關的圖表標題都顯示目前的場館名稱，清楚知道每張圖是哪一館。
- 圖表每 5 分鐘自動更新（預測的「現在」會隨時間前進）。

### 變更
- 頁面順序：現在人數 → 熱力圖 → 預測 → 平均人數 → 趨勢（場館選擇移到它控制的圖表上方）。

### 移除
- 沒用到的 `db.py` / `DB_PATH`（資料只存 CSV）。

## [0.2.1] - 2026-07-01 — 手機版圖表

### 新增
- Emoji 分頁圖示（內嵌 SVG；解決 `/favicon.ico` 404）。

### 變更
- 手機版：折線圖在窄螢幕上旋轉 / 減少座標軸標籤，熱力圖可以左右捲動，格子保持可讀。

## [0.2.0] - 2026-07-01 — 網頁與 API

### 新增
- **FastAPI 網頁**（`app.py`）：JSON API（`/api/venues`、`/api/current`、`/api/history`、`/api/heatmap`、`/api/profile`）
  加上伺服器產生的頁面（Jinja2 + ECharts + HTMX）。
- **頁面圖表**：即時人數卡片附「跟平常比」的忙碌度（HTMX 自動更新）、星期 × 小時熱力圖、平均一天的人數曲線、
  1 / 3 / 7 / 30 天的人數趨勢（日期標籤附星期）。
- **資料存取層**（`data_access.py`）：pandas 聚合加上以 mtime 為 key 的快取；趨勢圖拿掉非營業時間的空白；日期標籤附星期。
- **開 / 閉館 0 標記**：收集器在開館與閉館那一刻各寫一筆 `count=0`（`source_status` 為 `open` / `closed`），
  讓曲線能回到 0；訓練時用 `ok` 過濾排除。
- 頁面上的**即時天氣**（Open-Meteo，伺服器端快取約 10 分鐘）。
- 編輯器 / 型別檢查設定：`pyrightconfig.json`、`.vscode/settings.json`。

### 變更
- `collector_loop.py` 改名為 `collector.py`，並把 `main.py` 併進去：`uv run collector.py` 是常駐迴圈，`--once` 只跑一輪。
- **天氣不再存進 `data/`**——改成即時顯示；訓練用的歷史天氣之後從 Open-Meteo 的歷史資料補。
- 頁面區塊順序：現在人數 → 熱力圖 → 平均人數 → 趨勢。

### 移除
- `data/weather.csv`、`storage.append_weather` 與天氣 CSV 的設定路徑。

### 修正
- 折線圖的 y 軸標題被切掉。
- pandas / pyright 型別警告；Starlette `TemplateResponse` 的參數順序；混合精度的 ISO-8601 解析。

## [0.1.1] - 2026-06-30 — 收集器移到工作站

### 新增
- `collector.py` 常駐迴圈（對齊時鐘），支援 cron / systemd / tmux 部署。

### 移除
- GitHub Actions 收集器 / git-scraping workflow（收集移到有永久硬碟、24 小時開機的學校工作站）。

## [0.1.0] - 2026-06-29 — 定時收集器

### 新增
- 爬取臺大健身中心與游泳池的即時人數（`rent.pe.ntu.edu.tw`）與校園天氣（Open-Meteo），寫入 CSV。
- 營業時間判斷、失敗寫成一列、SQLite schema。
- GitHub Actions cron（git-scraping），營業時間內每 10 分鐘執行。
