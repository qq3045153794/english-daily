from __future__ import annotations

import difflib
import re
from typing import Any, List, Optional, Tuple

from .config import Settings, date_str, normalize_answer, normalize_sentence
from .db import Database
from .lesson import load_private_lesson
from .notify import Notifier
from .stats import render_index, stats_text


def levenshtein(a: str, b: str) -> int:
    a = a or ""
    b = b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def evaluate_spelling(expected: str, answer: str) -> Tuple[bool, float, str]:
    exp = normalize_answer(expected)
    got = normalize_answer(answer)
    if not got:
        return False, 0.0, "empty"
    if got == exp:
        return True, 1.0, "exact"
    if len(exp) >= 5 and levenshtein(exp, got) <= 1:
        return False, 0.5, "near"
    return False, 0.0, "wrong"


def evaluate_dictation(expected: str, answer: str) -> Tuple[bool, float, List[str], List[str]]:
    exp_tokens = normalize_sentence(expected).split()
    got_tokens = normalize_sentence(answer).split()
    if not got_tokens:
        return False, 0.0, exp_tokens, got_tokens
    ratio = difflib.SequenceMatcher(None, exp_tokens, got_tokens).ratio()
    return ratio >= 0.9, round(float(ratio), 3), exp_tokens, got_tokens


def _item_text(settings: Settings, item: Any, notifier: Notifier) -> str:
    if item is None:
        return "当前没有待答题。"
    prompt = str(item["prompt"])
    if item["kind"] == "spelling":
        return prompt
    sentence = str(item["expected"])
    audio = notifier.synthesize(sentence)
    if audio:
        if notifier.send_audio(audio, prompt):
            return prompt + "\n（语音已发送，可重复播放）"
        return prompt + "\n\n语音发送失败，请回复“跳过”或稍后重试。"
    return prompt + "\n\n语音生成失败，请回复“跳过”或稍后重试。"


def _batch_size(db: Database) -> int:
    try:
        return max(1, int(db.setting("quiz_batch_size", "5")))
    except Exception:
        return 5


def _pending_batch(db: Database, session: Any, size: int) -> List[Any]:
    """当前待答的一批题：拼写题按 size 批量，听写题单题。"""
    session_id = int(session["id"])
    idx = int(session["current_index"])
    items = db.session_items(session_id)
    remaining = items[idx:]
    if not remaining:
        return []
    if remaining[0]["kind"] == "dictation":
        return remaining[:1]
    batch = []
    for it in remaining:
        if it["kind"] != "spelling":
            break
        batch.append(it)
        if len(batch) >= size:
            break
    return batch


def _batch_text(settings: Settings, batch: List[Any], notifier: Notifier) -> str:
    """把一批题拼成一条消息。"""
    if not batch:
        return "当前没有待答题。"
    if len(batch) == 1 and batch[0]["kind"] == "dictation":
        return _item_text(settings, batch[0], notifier)
    parts = [str(it["prompt"]) for it in batch]
    return "请依次拼写下列单词（每行一个答案，或用空格分隔）：\n\n" + "\n".join(parts)


def _parse_answers(text: str, expected: Optional[int] = None) -> List[str]:
    """解析批量答案：按换行/逗号/顿号/分号拆分；单行空格分隔也识别。"""
    raw = (text or "").strip()
    parts = [p.strip() for p in re.split(r"[\n,，、;；]+", raw) if p.strip()]
    if expected and len(parts) < expected:
        spaced = [p for p in raw.split() if p]
        if len(spaced) == expected:
            return spaced
    return parts


def _start_if_needed(settings: Settings, db: Database, lesson: Any, notifier: Notifier) -> Tuple[Any, str]:
    active = db.active_session()
    if active is not None:
        batch = _pending_batch(db, active, _batch_size(db))
        return active, _batch_text(settings, batch, notifier)
    payload = load_private_lesson(settings, str(lesson["lesson_date"]))
    bp = payload.get("quiz_blueprint", {})
    items = list(bp.get("spelling", [])) + list(bp.get("dictation", []))
    if not items:
        raise RuntimeError("lesson has no quiz blueprint")
    session_id = db.create_session(int(lesson["id"]), items)
    with db.connect() as conn:
        session = conn.execute("SELECT * FROM quiz_sessions WHERE id=?", (session_id,)).fetchone()
    batch = _pending_batch(db, session, _batch_size(db))
    return session, _batch_text(settings, batch, notifier)


def start_quiz(settings: Settings, db: Database, day: Optional[str] = None) -> str:
    day = day or date_str()
    lesson = db.get_lesson(day)
    if lesson is None:
        raise RuntimeError("今日课程还没有生成")
    if not db.is_checked_in(int(lesson["id"])):
        raise RuntimeError("请先打卡")
    notifier = Notifier(settings)
    session, text = _start_if_needed(settings, db, lesson, notifier)
    return "🎯 训练开始（%d 题）\n\n%s" % (int(session["total"]), text)


def checkin(settings: Settings, db: Database, day: Optional[str] = None, start_training: bool = True) -> str:
    day = day or date_str()
    lesson = db.get_lesson(day)
    if lesson is None:
        raise RuntimeError("今日课程还没有生成，请先等待 20:30 推送或手动生成")
    lesson_id = int(lesson["id"])
    first = db.checkin(lesson_id, source="telegram")
    render_index(db, settings)

    if start_training:
        notifier = Notifier(settings)
        session, text = _start_if_needed(settings, db, lesson, notifier)
        try:
            from .github_sync import sync_repo
            sync_repo(settings, "checkin: %s" % day, push=True, db=db)
        except Exception as exc:
            db.log("sync_error", str(exc))
        prefix = "✅ 已打卡 %s" % day if first else "今天已经打过卡了，继续当前训练。"
        return prefix + "\n\n" + text

    try:
        from .github_sync import sync_repo
        sync_repo(settings, "checkin: %s" % day, push=True, db=db)
    except Exception as exc:
        db.log("sync_error", str(exc))
    return ("✅ 已打卡 %s（只打卡，未开始训练）。" % day) if first else "今天已经打过卡了。"


def answer(settings: Settings, db: Database, text: str) -> str:
    session = db.active_session()
    if session is None:
        raise RuntimeError("当前没有进行中的训练")
    if session["status"] == "paused":
        return "训练已暂停。回复“继续”后继续答题。"
    batch = _pending_batch(db, session, _batch_size(db))
    if not batch:
        return "当前没有待答题。"

    # 单题听写：整段文本即答案；批量拼写：按行/分隔符拆分。
    if len(batch) == 1 and batch[0]["kind"] == "dictation":
        answers = [text.strip()]
    else:
        answers = _parse_answers(text, len(batch))

    feedbacks: List[str] = []
    total_earned = 0.0
    for i, item in enumerate(batch):
        ans = answers[i] if i < len(answers) else ""
        if item["kind"] == "spelling":
            expected = str(item["expected"])
            correct, score, mode = evaluate_spelling(expected, ans)
            db.answer_item(int(item["id"]), ans, correct, score)
            if item["word_id"] is not None:
                db.update_word_result(int(item["word_id"]), correct)
            if mode == "exact":
                feedbacks.append("✅ 正确：%s" % expected)
            elif mode == "near":
                feedbacks.append("🟡 差一点：你的答案 `%s`，正确拼写是 `%s`。" % (ans, expected))
            else:
                feedbacks.append("❌ 正确答案：%s（你答：%s）" % (expected, ans or "空"))
        else:
            expected = str(item["expected"])
            correct, score, exp_tokens, got_tokens = evaluate_dictation(expected, ans)
            db.answer_item(int(item["id"]), ans, correct, score)
            if item["word_id"] is not None:
                db.update_word_result(int(item["word_id"]), correct)
            if score >= 0.999:
                feedbacks.append("✅ 听写完全正确。")
            else:
                feedbacks.append("📝 听写得分：%d%%\n正确句子：%s" % (round(score * 100), expected))
        total_earned += float(score)

    session = db.active_session()
    new_score = float(session["score"] if session else 0.0) + total_earned
    next_item = db.advance_session(int(session["id"]), new_score, steps=len(batch)) if session else None
    notifier = Notifier(settings)
    if next_item is None:
        _after_finish(settings, db)
        return "\n".join(feedbacks) + "\n\n" + _finish_text(db)
    next_batch = _pending_batch(db, db.active_session(), _batch_size(db))
    return "\n".join(feedbacks) + "\n\n" + _batch_text(settings, next_batch, notifier)


def _after_finish(settings: Settings, db: Database) -> None:
    render_index(db, settings)
    try:
        from .github_sync import sync_repo
        sync_repo(settings, "quiz completed", push=True, db=db)
    except Exception as exc:
        db.log("sync_error", str(exc))


def _finish_text(db: Database) -> str:
    with db.connect() as conn:
        session = conn.execute(
            "SELECT * FROM quiz_sessions WHERE status='completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if session is None:
            return "训练完成。"
        items = conn.execute(
            "SELECT kind, score, correct FROM quiz_items WHERE session_id=?", (int(session["id"]),)
        ).fetchall()
    spelling = [x for x in items if x["kind"] == "spelling"]
    dictation = [x for x in items if x["kind"] == "dictation"]
    sp_score = sum(float(x["score"] or 0) for x in spelling)
    di_score = sum(float(x["score"] or 0) for x in dictation)
    lines = [
        "🎉 训练完成",
        "拼写：%.1f/%d" % (sp_score, len(spelling)),
        "听写：%.1f/%d" % (di_score, len(dictation)),
        "综合：%.1f/%d" % (sp_score + di_score, len(spelling) + len(dictation)),
        "",
        stats_text(db),
    ]
    return "\n".join(lines)


def hint(settings: Settings, db: Database) -> str:
    session = db.active_session()
    if session is None:
        return "当前没有进行中的训练。"
    item = db.current_item(int(session["id"]))
    if item is None:
        return "当前没有待答题。"
    return "💡 " + str(item["hint"] or "暂无提示")


def skip(settings: Settings, db: Database) -> str:
    session = db.active_session()
    if session is None:
        return "当前没有进行中的训练。"
    batch = _pending_batch(db, session, _batch_size(db))
    if not batch:
        return "当前没有待答题。"
    item = batch[0]
    db.answer_item(int(item["id"]), "[skip]", False, 0.0)
    if item["word_id"] is not None:
        db.update_word_result(int(item["word_id"]), False)
    current_score = float(session["score"] or 0)
    next_item = db.advance_session(int(session["id"]), current_score, steps=1)
    prefix = "跳过。正确答案：%s" % item["expected"]
    if next_item is None:
        _after_finish(settings, db)
        return prefix + "\n\n" + _finish_text(db)
    next_batch = _pending_batch(db, db.active_session(), _batch_size(db))
    return prefix + "\n\n" + _batch_text(settings, next_batch, Notifier(settings))


def pause(settings: Settings, db: Database) -> str:
    session = db.active_session()
    if session is None:
        return "当前没有进行中的训练。"
    db.set_session_status(int(session["id"]), "paused")
    return "训练已暂停。回复“继续”恢复。"


def resume(settings: Settings, db: Database) -> str:
    session = db.active_session()
    if session is None:
        return "当前没有进行中的训练。"
    db.set_session_status(int(session["id"]), "active")
    batch = _pending_batch(db, session, _batch_size(db))
    return "训练继续。\n\n" + _batch_text(settings, batch, Notifier(settings))


def quiz_status(db: Database) -> str:
    session = db.active_session()
    if session is None:
        return "当前没有进行中的训练。"
    item = db.current_item(int(session["id"]))
    return "训练状态：%s\n进度：%d/%d" % (
        session["status"], int(session["current_index"]) + 1, int(session["total"])
    )
