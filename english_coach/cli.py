from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings, date_str, parse_date
from .db import Database
from .github_sync import create_repo, enable_pages, get_github_user, pages_url, sync_repo
from .lesson import github_lesson_url, load_private_lesson, pages_lesson_url, prepare_lesson, push_lesson, raw_preview_index_url, raw_preview_url, rerender_lesson
from .notify import Notifier
from .quiz import answer as quiz_answer
from .quiz import checkin as quiz_checkin
from .quiz import hint as quiz_hint
from .quiz import pause as quiz_pause
from .quiz import quiz_status
from .quiz import resume as quiz_resume
from .quiz import skip as quiz_skip
from .quiz import start_quiz
from .stats import calculate_stats, render_index, stats_text, write_progress
from .words import build_word_pool, download_ecdict


def context():
    settings = Settings()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init()
    return settings, db


def _day(value):
    return date_str(parse_date(value)) if value else date_str()


def cmd_init(args):
    settings, db = context()
    if settings.word_pool_path.exists():
        n = db.import_words(settings.word_pool_path)
        print("word pool imported: %d new rows; total=%d" % (n, db.word_count()))
    print("database: %s" % settings.db_path)
    print("repo: %s" % settings.repo)


def cmd_build_pool(args):
    settings, db = context()
    cache = settings.cache_dir / "ecdict.csv"
    if args.download or not cache.exists():
        print("downloading ECDICT...")
        download_ecdict(cache)
    print("building word pool...")
    items = build_word_pool(cache, settings.word_pool_path, limit=args.limit)
    print("word pool: %d words -> %s" % (len(items), settings.word_pool_path))


def cmd_import(args):
    settings, db = context()
    path = Path(args.path) if args.path else settings.word_pool_path
    n = db.import_words(path)
    print("imported %d words; total=%d" % (n, db.word_count()))


def cmd_prepare(args):
    settings, db = context()
    payload = prepare_lesson(settings, db, day=_day(args.date), force=args.force, sync=not args.no_sync)
    print("lesson prepared: %s" % payload["date"])
    print("words: %d" % len(payload.get("words", [])))
    print("markdown: %s" % (settings.md_dir / (payload["date"] + ".md")))
    url = pages_lesson_url(db, settings, payload["date"])
    if url:
        print("pages: %s" % url)


def cmd_rerender(args):
    settings, db = context()
    payload = rerender_lesson(settings, db, day=_day(args.date), sync=not args.no_sync)
    print("rerendered: %s" % payload["date"])


def cmd_push(args):
    settings, db = context()
    message = push_lesson(settings, db, day=_day(args.date))
    # Cron uses this command directly; print only on failure/silent mode.
    if args.print:
        print(message)


def cmd_show(args):
    settings, db = context()
    day = _day(args.date)
    lesson = db.get_lesson(day)
    if lesson is None:
        raise RuntimeError("lesson not found: %s" % day)
    print("课程：%s" % day)
    print("Markdown：%s" % lesson["md_path"])
    url = pages_lesson_url(db, settings, day)
    if url:
        print("Pages：%s" % url)
    gh_url = github_lesson_url(db, day)
    if gh_url:
        print("GitHub：%s" % gh_url)
    preview = raw_preview_url(db, day)
    if preview:
        print("临时 HTML：%s" % preview)
    print("已打卡：%s" % ("是" if lesson["checked_in_at"] else "否"))
    if lesson["quiz_completed_at"]:
        print("训练：已完成 %.1f/%.1f" % (float(lesson["quiz_score"]), float(lesson["quiz_total"])))


def cmd_checkin(args):
    settings, db = context()
    print(quiz_checkin(settings, db, day=_day(args.date), start_training=not args.no_quiz))


def cmd_start(args):
    settings, db = context()
    print(start_quiz(settings, db, day=_day(args.date)))


def cmd_answer(args):
    settings, db = context()
    text = " ".join(args.text or []).strip()
    if not text:
        raise RuntimeError("answer text is required")
    print(quiz_answer(settings, db, text))


def cmd_hint(args):
    settings, db = context()
    print(quiz_hint(settings, db))


def cmd_skip(args):
    settings, db = context()
    print(quiz_skip(settings, db))


def cmd_pause(args):
    settings, db = context()
    print(quiz_pause(settings, db))


def cmd_resume(args):
    settings, db = context()
    print(quiz_resume(settings, db))


def cmd_quiz_status(args):
    settings, db = context()
    print(quiz_status(db))


def cmd_stats(args):
    settings, db = context()
    if args.json:
        print(json.dumps(calculate_stats(db), ensure_ascii=False, indent=2))
    else:
        print(stats_text(db))


def cmd_errors(args):
    settings, db = context()
    rows = db.top_errors(args.limit)
    if not rows:
        print("错词本还是空的。")
        return
    lines = ["📕 错词本（按错误次数）", ""]
    for i, row in enumerate(rows, 1):
        lines.append("%d. %s %s %s — 错 %d 次" % (
            i, row["word"], row["phonetic"] or "", row["meaning_zh"] or "", int(row["wrong_count"] or 0)
        ))
    print("\n".join(lines))


def cmd_speak(args):
    settings, db = context()
    word = args.word.strip()
    row = db.word(word)
    text = word
    caption = word
    if row is not None:
        text = str(row["word"])
        caption = "%s %s %s" % (row["word"], row["phonetic"] or "", row["meaning_zh"] or "")
    notifier = Notifier(settings)
    ok = notifier.speak(text, caption=caption)
    print("已发送发音。" if ok else "发音发送失败。")


def cmd_sync(args):
    settings, db = context()
    result = sync_repo(settings, args.message, push=not args.no_push, db=db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_render(args):
    settings, db = context()
    render_index(db, settings)
    print("rendered: %s" % settings.index_path)


def cmd_settings(args):
    settings, db = context()
    if args.set:
        key, value = args.set
        db.set_setting(key, value)
        print("%s = %s" % (key, value))
    else:
        print(json.dumps(db.settings(), ensure_ascii=False, indent=2))


def cmd_set_daily(args):
    settings, db = context()
    total = int(args.count)
    if total < 5 or total > 50:
        raise RuntimeError("daily word count must be between 5 and 50")
    new_count = max(1, round(total * 2.0 / 3.0))
    review_count = max(0, total - new_count)
    db.set_setting("daily_total_words", str(total))
    db.set_setting("daily_new_words", str(new_count))
    db.set_setting("daily_review_words", str(review_count))
    print("每天 %d 个词：%d 新词 + %d 复习" % (total, new_count, review_count))


def cmd_pause_day(args):
    settings, db = context()
    day = _day(args.date)
    db.add_pause(day, args.reason)
    render_index(db, settings)
    print("paused: %s" % day)


def cmd_status(args):
    settings, db = context()
    day = date_str()
    lesson = db.get_lesson(day)
    session = db.active_session()
    data = {
        "today": day,
        "lesson": dict(lesson) if lesson is not None else None,
        "quiz": dict(session) if session is not None else None,
        "stats": calculate_stats(db),
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print("today: %s" % day)
        print("lesson: %s" % ("ready" if lesson else "not ready"))
        print("quiz: %s" % (session["status"] if session else "none"))
        print("checked in: %s" % ("yes" if lesson and lesson["checked_in_at"] else "no"))


def cmd_github_setup(args):
    settings, db = context()
    token = settings.github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not configured")
    user = get_github_user(token)
    owner = user["login"]
    name = args.repo_name
    try:
        repo = create_repo(token, name, private=args.private, description="Daily English learning archive")
    except RuntimeError as exc:
        if "422" not in str(exc):
            raise
        repo = {"full_name": "%s/%s" % (owner, name)}
    remote = "https://github.com/%s/%s.git" % (owner, name)
    db.set_setting("github_remote", remote)
    sync_repo(settings, "initial english-daily site", push=True, db=db)
    try:
        enable_pages(token, owner, name, branch="main", path="/docs")
    except RuntimeError as exc:
        if "409" not in str(exc) and "422" not in str(exc):
            raise
    url = pages_url(owner, name)
    db.set_setting("pages_url", url)
    render_index(db, settings)
    print("repo: %s" % remote)
    print("pages: %s" % url)


def build_parser():
    p = argparse.ArgumentParser(prog="ec", description="English Daily coach")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("init")
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("build-pool")
    q.add_argument("--download", action="store_true")
    q.add_argument("--limit", type=int, default=20000)
    q.set_defaults(func=cmd_build_pool)

    q = sub.add_parser("import-words")
    q.add_argument("--path")
    q.set_defaults(func=cmd_import)

    q = sub.add_parser("prepare")
    q.add_argument("--date")
    q.add_argument("--force", action="store_true")
    q.add_argument("--no-sync", action="store_true")
    q.set_defaults(func=cmd_prepare)

    q = sub.add_parser("rerender")
    q.add_argument("--date")
    q.add_argument("--no-sync", action="store_true")
    q.set_defaults(func=cmd_rerender)

    q = sub.add_parser("push")
    q.add_argument("--date")
    q.add_argument("--print", action="store_true")
    q.set_defaults(func=cmd_push)

    q = sub.add_parser("show")
    q.add_argument("--date")
    q.set_defaults(func=cmd_show)

    q = sub.add_parser("checkin")
    q.add_argument("--date")
    q.add_argument("--no-quiz", action="store_true")
    q.set_defaults(func=cmd_checkin)

    q = sub.add_parser("start")
    q.add_argument("--date")
    q.set_defaults(func=cmd_start)

    q = sub.add_parser("answer")
    q.add_argument("text", nargs=argparse.REMAINDER)
    q.set_defaults(func=cmd_answer)

    for name, func in [("hint", cmd_hint), ("skip", cmd_skip), ("pause", cmd_pause), ("resume", cmd_resume), ("quiz-status", cmd_quiz_status)]:
        q = sub.add_parser(name)
        q.set_defaults(func=func)

    q = sub.add_parser("stats")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_stats)

    q = sub.add_parser("errors")
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(func=cmd_errors)

    q = sub.add_parser("speak")
    q.add_argument("word")
    q.set_defaults(func=cmd_speak)

    q = sub.add_parser("sync")
    q.add_argument("--message", default="sync english daily")
    q.add_argument("--no-push", action="store_true")
    q.set_defaults(func=cmd_sync)

    q = sub.add_parser("render")
    q.set_defaults(func=cmd_render)

    q = sub.add_parser("settings")
    q.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"))
    q.set_defaults(func=cmd_settings)

    q = sub.add_parser("set-daily")
    q.add_argument("count", type=int)
    q.set_defaults(func=cmd_set_daily)

    q = sub.add_parser("pause-day")
    q.add_argument("--date")
    q.add_argument("--reason", default="")
    q.set_defaults(func=cmd_pause_day)

    q = sub.add_parser("status")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_status)

    q = sub.add_parser("github-setup")
    q.add_argument("--repo-name", default="english-daily")
    q.add_argument("--private", action="store_true")
    q.set_defaults(func=cmd_github_setup)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    # `answer` accepts the remaining words as a single answer.
    try:
        args.func(args)
    except Exception as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
