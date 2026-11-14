"""Dashboard UI strings in Traditional Chinese and English.

The language comes from a `lang` cookie the page's 中文/EN toggle sets, read
on every request so the server-rendered page *and* the HTMX cards fragment
both come out in the chosen language (localStorage would never reach the
server). Default zh. Closure reasons in `closures.csv` are free text and are
shown as written; the JSON API is language-neutral.
"""

from __future__ import annotations

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

# Mon=0 .. Sun=6 (datetime.weekday()) — the "7/1 (三)" day labels, and the
# calendar's header row (which starts on Sunday, see `calendar_weekdays`).
_WEEKDAYS = {
    "zh": ["一", "二", "三", "四", "五", "六", "日"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]

# Short venue labels for the closure calendar's narrow day cells; "all" is a
# closure covering every venue with the same reason.
_VENUE_SHORT = {
    "zh": {"gym": "健身", "pool": "泳池", "all": "全館"},
    "en": {"gym": "Gym", "pool": "Pool", "all": "All"},
}

# Venue names come from the scraped page in Chinese; English mode maps them
# by venue_id and falls back to the scraped name for an unknown venue.
_VENUE_NAMES_EN = {"gym": "Gym", "pool": "Indoor Pool"}

STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        "html_lang": "zh-Hant",
        "subtitle": "台大健身中心・游泳池 即時人數與預測",
        "footer": "資料每 10 分鐘更新・僅顯示開放時段",
        "light": "淺色",
        "dark": "深色",
        "now_heading": "現在人數",
        "loading": "載入中…",
        "venue_heading": "場館選擇",
        "forecast_heading": "人數預測",
        "today": "今天",
        "tomorrow": "明天",
        "profile_heading": "各時段平均人數（最近 N 天）",
        "days_1": "一天",
        "days_3": "三天",
        "days_7": "七天",
        "days_30": "一個月",
        "calendar_heading": "休館行事曆",
        "legend_full": "全館休館",
        "legend_part": "部分場館休館",
        "list_sep": "、",
        "no_closures": "這個月沒有休館日。",
        "after_hours": "🌙 目前非開放時間（今日開放時間 {open}–{close}）。",
        "closed": "休館中",
        "closed_range": "（{range}）",
        "unit": " 人",
        "pct_full": "{pct}% 滿載 (上限 {max})",
        "quiet": "🟢 空",
        "normal": "🟡 普通",
        "busy": "🔴 擁擠",
        "typical": "(此時段平均 {n})",
        "updated": "更新於 {t}",
        "no_data": "目前沒有資料（可能尚未開始收集，或現在休館）。",
    },
    "en": {
        "html_lang": "en",
        "subtitle": "Live occupancy and forecast for NTU's gym and pool",
        "footer": "Updated every 10 minutes · opening hours only",
        "light": "Light",
        "dark": "Dark",
        "now_heading": "Right now",
        "loading": "Loading…",
        "venue_heading": "Venue",
        "forecast_heading": "Forecast",
        "today": "Today",
        "tomorrow": "Tomorrow",
        "profile_heading": "Average by time of day (last N days)",
        "days_1": "1 day",
        "days_3": "3 days",
        "days_7": "7 days",
        "days_30": "1 month",
        "calendar_heading": "Closure calendar",
        "legend_full": "Both venues closed",
        "legend_part": "One venue closed",
        "list_sep": "; ",
        "no_closures": "No closures this month.",
        "after_hours": "🌙 Closed now (today's hours: {open}–{close}).",
        "closed": "Closed",
        "closed_range": " ({range})",
        "unit": " people",
        "pct_full": "{pct}% full (capacity {max})",
        "quiet": "🟢 Quiet",
        "normal": "🟡 Moderate",
        "busy": "🔴 Busy",
        "typical": "(usually {n} at this hour)",
        "updated": "Updated {t}",
        "no_data": "No data yet (collection may not have started, or the venues are closed).",
    },
}

# Strings the page's chart JavaScript needs (passed in as JSON).
_JS_STRINGS = {
    "zh": {"actual": "實際", "forecast": "預測", "count_axis": "人數",
           "avg_axis": "平均人數", "now": "現在"},
    "en": {"actual": "Actual", "forecast": "Forecast", "count_axis": "People",
           "avg_axis": "Avg. people", "now": "Now"},
}


def get_lang(cookies: dict[str, str]) -> str:
    lang = cookies.get("lang")
    return lang if lang in LANGS else DEFAULT_LANG


def venue_name(venue_id: str, scraped_name: str, lang: str) -> str:
    return _VENUE_NAMES_EN.get(venue_id, scraped_name) if lang == "en" else scraped_name


def day_label(dt, lang: str) -> str:
    """e.g. '7/1 (三)' / '7/1 (Wed)' — for the forecast day toggle."""
    return f"{dt.month}/{dt.day} ({_WEEKDAYS[lang][dt.weekday()]})"


def js_strings(lang: str) -> dict:
    return _JS_STRINGS[lang]


def month_title(d, lang: str) -> str:
    """e.g. '2026 年 11 月' / 'November 2026'."""
    return f"{d.year} 年 {d.month} 月" if lang == "zh" else f"{_MONTHS_EN[d.month - 1]} {d.year}"


def calendar_weekdays(lang: str) -> list[str]:
    """Header row for a Sunday-first month grid."""
    names = _WEEKDAYS[lang]
    return names[6:] + names[:6]


def venue_short(key: str, lang: str) -> str:
    return _VENUE_SHORT[lang].get(key, key)
