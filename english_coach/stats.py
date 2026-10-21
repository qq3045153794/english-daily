from __future__ import annotations

import html
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import date_str, iso_now, today_cst
from .db import Database
from .html_render import page_html


def _date_range(start: date, end: date) -> List[date]:
    if end < start:
        return []
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def calculate_stats(db: Database, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or today_cst()
    start_text = db.setting("start_date", today.isoformat()) or today.isoformat()
    start = date.fromisoformat(start_text[:10])
    checked = set(db.checked_dates())
    paused = set(db.pause_dates())
    past_days = [d for d in _date_range(start, today - timedelta(days=1)) if d.isoformat() not in paused]
    # 今天也算应打卡；只有已经打卡才计入，避免当天未完成时算缺卡
    counts_today = today.isoformat() not in paused and today.isoformat() in checked
    expected = len(past_days) + (1 if counts_today else 0)
    total_checkins = len(checked)
    missed_dates = [d.isoformat() for d in past_days if d.isoformat() not in checked]
    missed = len(missed_dates)

    streak = 0
    cursor = today if today.isoformat() in checked else today - timedelta(days=1)
    while cursor >= start and cursor.isoformat() not in paused:
        if cursor.isoformat() in checked:
            streak += 1
            cursor -= timedelta(days=1)
        else:
            break

    rate = (total_checkins / expected) if expected else (1.0 if today.isoformat() in checked else 0.0)
    return {
        "start_date": start.isoformat(),
        "today": today.isoformat(),
        "streak": streak,
        "total_checkins": total_checkins,
        "expected_days": expected,
        "missed_days": missed,
        "missed_dates": missed_dates,
        "checked_in_today": today.isoformat() in checked,
        "completion_rate": round(rate * 100, 1),
        "paused_dates": sorted(paused),
    }


def calendar_rows(db: Database, days: int = 30, today: Optional[date] = None) -> List[Dict[str, str]]:
    today = today or today_cst()
    checked = set(db.checked_dates())
    paused = set(db.pause_dates())
    start_text = db.setting("start_date", today.isoformat()) or today.isoformat()
    start = date.fromisoformat(start_text[:10])
    begin = max(start, today - timedelta(days=days - 1))
    out: List[Dict[str, str]] = []
    for d in _date_range(begin, today):
        key = d.isoformat()
        if key in paused:
            status = "paused"
        elif key in checked:
            status = "checked_in"
        elif d == today:
            status = "today"
        else:
            status = "missed"
        out.append({"date": key, "status": status})
    return out


def write_progress(db: Database, settings: Any, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or today_cst()
    stats = calculate_stats(db, today)
    stats["calendar"] = calendar_rows(db, 30, today)
    stats["updated_at"] = iso_now()
    path = Path(settings.progress_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def _calendar_html(rows: List[Dict[str, str]]) -> str:
    symbols = {"checked_in": "✅", "missed": "❌", "today": "⏳", "paused": "⏸️"}
    cells = []
    for row in rows:
        d = date.fromisoformat(row["date"])
        cells.append('<span class="day %s" title="%s">%s</span>' % (
            row["status"], row["date"], symbols.get(row["status"], "·")
        ))
    return '<div class="calendar">%s</div>' % " ".join(cells)


def render_index(db: Database, settings: Any, today: Optional[date] = None) -> None:
    today = today or today_cst()
    stats = write_progress(db, settings, today)
    lessons = db.lessons(90)
    lines = [
        "---",
        "layout: default",
        "title: 英语学习",
        "---",
        "",
        "# English Daily",
        "",
        "每天 20:30 推送学习内容；在 Telegram 回复 **打卡** 后立即开始训练。",
        "",
        "## 打卡统计",
        "",
        "- 连续打卡：**%d 天**" % stats["streak"],
        "- 累计打卡：**%d 天**" % stats["total_checkins"],
        "- 应打卡：**%d 天**" % stats["expected_days"],
        "- 缺卡：**%d 天**" % stats["missed_days"],
        "- 完成率：**%.1f%%**" % stats["completion_rate"],
        "",
        "### 最近 30 天",
        "",
        _calendar_html(stats["calendar"]),
        "",
        "## 最近课程",
        "",
        "| 日期 | 状态 | 课程 |",
        "|---|---|---|",
    ]
    for row in lessons:
        status = "✅ 已打卡" if row["checked_in_at"] else "⏳ 未打卡"
        link = "lessons/%s.html" % row["lesson_date"]
        lines.append("| %s | %s | [打开](%s) |" % (row["lesson_date"], status, link))
    if not lessons:
        lines.append("| - | - | 还没有课程 |")
    lines.extend([
        "",
        "---",
        "",
        "> 统计由服务器提交更新；GitHub Pages 可能需要几分钟刷新。",
        "",
    ])
    index_md = "\n".join(lines)
    Path(settings.index_path).write_text(
        page_html("英语学习", index_md, include_speak=False), encoding="utf-8"
    )


def stats_text(db: Database) -> str:
    s = calculate_stats(db)
    lines = [
        "📊 英语打卡统计",
        "连续打卡：%d 天" % s["streak"],
        "累计打卡：%d 天" % s["total_checkins"],
        "应打卡：%d 天" % s["expected_days"],
        "缺卡：%d 天" % s["missed_days"],
        "完成率：%.1f%%" % s["completion_rate"],
    ]
    if s["missed_dates"]:
        lines.append("最近缺卡：" + "、".join(s["missed_dates"][-7:]))
    return "\n".join(lines)
