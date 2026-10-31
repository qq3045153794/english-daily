from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Settings, add_days, clean_line, date_str, iso_now
from .db import Database
from .html_render import page_html
from .llm import DeepSeekClient, lesson_prompt, repair_prompt
from .notify import Notifier
from .stats import render_index
from .words import row_dict, select_lesson_words, select_quiz_words, spelling_item


def _word_map(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(x.get("word", "")).lower(): x for x in items if isinstance(x, dict)}


def validate_content(data: Dict[str, Any], selected: List[Dict[str, Any]]) -> List[str]:
    errors: List[str] = []
    words = data.get("words")
    if not isinstance(words, list):
        return ["words must be a list"]
    if len(words) != len(selected):
        errors.append("words must contain exactly %d items" % len(selected))
    got = {str(x.get("word", "")).lower() for x in words if isinstance(x, dict)}
    missing = [str(x["word"]) for x in selected if str(x["word"]).lower() not in got]
    if missing:
        errors.append("missing selected words: " + ", ".join(missing))
    for i, item in enumerate(words):
        if not isinstance(item, dict):
            errors.append("words[%d] is not an object" % i)
            continue
        for key in ("word", "meaning_zh", "example", "example_type"):
            if not str(item.get(key, "")).strip():
                errors.append("words[%d].%s is empty" % (i, key))
        ex = str(item.get("example", ""))
        if ex and not (5 <= len(ex.split()) <= 30):
            errors.append("words[%d].example length is not 5-30 words" % i)
    patterns = data.get("sentence_patterns")
    if not isinstance(patterns, list) or len(patterns) < 3:
        errors.append("sentence_patterns must contain at least 3 items")
    reading = data.get("reading")
    if not isinstance(reading, dict):
        errors.append("reading must be an object")
    else:
        text = str(reading.get("text", ""))
        if len(text.split()) < 80:
            errors.append("reading.text is too short")
        questions = reading.get("questions")
        if not isinstance(questions, list) or len(questions) < 2:
            errors.append("reading.questions must contain at least 2 items")
        used = " ".join(str(x).lower() for x in reading.get("used_words", []))
        covered = sum(1 for x in selected if str(x["word"]).lower() in used or str(x["word"]).lower() in text.lower())
        if covered < min(8, len(selected)):
            errors.append("reading must cover at least 8 selected words")
    extra = data.get("dictation_extra")
    if not isinstance(extra, list) or len(extra) < 2:
        errors.append("dictation_extra must contain at least 2 items")
    return errors


def _fallback_card(item: Dict[str, Any]) -> Dict[str, Any]:
    word = str(item.get("word", ""))
    return {
        "word": word,
        "pos": str(item.get("pos", "") or "-"),
        "meaning_zh": clean_line(str(item.get("meaning_zh", "") or item.get("definition_en", "")), 180),
        "collocation": "",
        "example": "I need to learn the word \"%s\" in context." % word,
        "example_type": "declarative",
        "example_zh": "我需要结合语境学习这个词。",
    }


def _merge_cards(selected: List[Dict[str, Any]], data: Dict[str, Any]) -> List[Dict[str, Any]]:
    cards = _word_map(data.get("words", []))
    out = []
    for item in selected:
        word = str(item["word"]).lower()
        card = dict(cards.get(word) or _fallback_card(item))
        card["word"] = word
        card["ipa"] = str(item.get("phonetic", "") or card.get("ipa", ""))
        card["pos"] = str(card.get("pos") or item.get("pos", "") or "-")
        card["meaning_zh"] = clean_line(str(card.get("meaning_zh") or item.get("meaning_zh", "")), 180)
        card["tags"] = item.get("tags", [])
        card["role"] = item.get("role", "new")
        card["word_id"] = int(item["id"])
        for key in ("example", "example_type", "collocation", "example_zh"):
            card[key] = clean_line(str(card.get(key, "") or ""), 260)
        out.append(card)
    return out


def _dictation_from_lesson(cards: List[Dict[str, Any]], extra: List[Dict[str, Any]], count: int = 1) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    used = set()
    # Prefer two lesson examples with different sentence types.
    types = set()
    for card in cards:
        ex = str(card.get("example", "")).strip()
        typ = str(card.get("example_type", "")).strip()
        if ex and (typ not in types or len(out) < 1):
            out.append({
                "kind": "dictation",
                "word_id": card.get("word_id"),
                "expected": ex,
                "hint": "首词：%s" % (ex.split()[0] if ex.split() else ""),
                "source": "lesson",
                "prompt": "",
            })
            used.add(ex.lower())
            types.add(typ)
        if len(out) >= 2:
            break
    for item in extra or []:
        ex = str(item.get("sentence", "")).strip()
        if ex and ex.lower() not in used:
            out.append({
                "kind": "dictation",
                "word_id": None,
                "expected": ex,
                "hint": "首词：%s" % (ex.split()[0] if ex.split() else ""),
                "source": "new",
                "prompt": "",
                "translation_zh": clean_line(str(item.get("translation_zh", "")), 200),
            })
            used.add(ex.lower())
        if len(out) >= count:
            break
    for card in cards:
        if len(out) >= count:
            break
        ex = str(card.get("example", "")).strip()
        if ex and ex.lower() not in used:
            out.append({
                "kind": "dictation",
                "word_id": card.get("word_id"),
                "expected": ex,
                "hint": "首词：%s" % (ex.split()[0] if ex.split() else ""),
                "source": "lesson",
                "prompt": "",
            })
            used.add(ex.lower())
    return out[:count]


def _translation_from_lesson(
    cards: List[Dict[str, Any]],
    extra: List[Dict[str, Any]],
    count: int = 3,
    exclude: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """英译中：先用户型更完整的课外语料（dictation_extra），再用课文例句。

    译文取自课文自带的 example_zh / translation_zh（LLM 已生成），
    不在这里翻译，避免二次生成出偏差。
    """
    used = {str(x).strip().lower() for x in (exclude or []) if str(x).strip()}
    pool: List[Dict[str, Any]] = []
    for item in extra or []:
        ex = clean_line(str(item.get("sentence", "")), 260)
        zh = clean_line(str(item.get("translation_zh", "")), 200)
        if ex and zh and ex.lower() not in used:
            pool.append({"en": ex, "zh": zh, "word_id": None, "source": "new"})
            used.add(ex.lower())
    for card in cards:
        ex = clean_line(str(card.get("example", "")), 260)
        zh = clean_line(str(card.get("example_zh", "")), 200)
        if ex and zh and ex.lower() not in used:
            pool.append({
                "en": ex,
                "zh": zh,
                "word_id": None,
                "source": "lesson",
                "word": str(card.get("word", "")),
            })
            used.add(ex.lower())

    out: List[Dict[str, Any]] = []
    for item in pool:
        if len(out) >= count:
            break
        out.append({
            "kind": "translation",
            "word_id": item["word_id"],
            "expected": item["zh"],
            "english": item["en"],
            "hint": "",
            "source": item["source"],
            "prompt": "",
        })
    return out[:count]


def build_blueprint(
    db: Database,
    cards: List[Dict[str, Any]],
    data: Dict[str, Any],
    spelling_count: int,
    dictation_count: int,
    translation_count: int = 3,
) -> Dict[str, Any]:
    lesson_like = []
    for card in cards:
        lesson_like.append({
            "id": card["word_id"],
            "word": card["word"],
            "pos": card.get("pos", ""),
            "meaning_zh": card.get("meaning_zh", ""),
            "phonetic": card.get("ipa", ""),
            "role": card.get("role", "new"),
            "difficulty": 0,
        })
    # 拼写题：当天全部新词 + 到期待复习词补足到 spelling_count（未到期不提前抽）。
    quiz_words = select_quiz_words(db, lesson_like, spelling_count)
    spelling = []
    for pos, item in enumerate(quiz_words, 1):
        q = spelling_item(item, pos, len(quiz_words))
        spelling.append(q)
    dictation = _dictation_from_lesson(cards, data.get("dictation_extra", []), dictation_count)
    for pos, item in enumerate(dictation, 1):
        item["prompt"] = "第 %d/%d 句：请听写下面的英文句子。" % (pos, dictation_count)
    translation = _translation_from_lesson(
        cards,
        data.get("dictation_extra", []),
        translation_count,
        exclude=[str(x.get("expected", "")) for x in dictation],
    )
    for pos, item in enumerate(translation, 1):
        item["prompt"] = "第 %d/%d 题：请把下面的英文翻译成中文。\n\n%s" % (
            pos, translation_count, item["english"]
        )
    return {"spelling": spelling, "dictation": dictation, "translation": translation}


def _bold_words(text: str, words: List[str]) -> str:
    text = str(text or "").replace("**", "")
    for word in sorted(set(w.lower() for w in words), key=len, reverse=True):
        # Avoid bolding text that is already bolded.
        pattern = re.compile(r"(?<!\*)\b" + re.escape(word) + r"\b(?!\*)", re.I)
        text = pattern.sub(lambda m: "**%s**" % m.group(0), text)
    return text


def render_markdown(lesson_date: str, cards: List[Dict[str, Any]], data: Dict[str, Any], stats: Dict[str, Any], quiz: Optional[Dict[str, Any]] = None, carried_from: str = "") -> str:
    reading = data.get("reading", {}) if isinstance(data.get("reading"), dict) else {}
    words = [c["word"] for c in cards]
    lines = [
        "# %s 英语学习" % lesson_date,
        "",
    ]
    if carried_from:
        lines.extend([
            "> ⚠️ **%s 的课程还没打卡，今天沿用同一份内容，不生成新词。** 打完卡后第二天再继续新词。" % carried_from,
            "",
        ])
    lines.extend([
        "> 今日 **%d** 词 · 阅读约 20 分钟 · 学习完成后在 Telegram 回复 **打卡**，训练会立即开始。" % len(cards),
        "",
        "## 今日单词",
        "",
    ])
    for i, card in enumerate(cards, 1):
        ipa = card.get("ipa") or ""
        ipa_text = " /%s/" % ipa.strip("/") if ipa else ""
        lines.extend([
            "### %d. %s%s %s <span class=\"speak\" data-word=\"%s\">🔊</span>" % (
                i, card["word"], ipa_text, card.get("pos", ""), card["word"]
            ),
            "",
            "- 中文：%s" % (card.get("meaning_zh") or "-"),
            "- 搭配：%s" % (card.get("collocation") or "-"),
            "- 例句：%s" % (card.get("example") or "-"),
            "- 翻译：%s" % (card.get("example_zh") or "-"),
            "- 句型：%s" % (card.get("example_type") or "-"),
            "- 类型：%s" % ("新词" if card.get("role") == "new" else ("错词" if card.get("role") == "error" else "复习")),
            "",
        ])
    patterns = data.get("sentence_patterns", [])
    if isinstance(patterns, list) and patterns:
        lines.extend(["## 今日句型", ""])
        for item in patterns:
            if not isinstance(item, dict):
                continue
            lines.append("- **%s**：%s" % (item.get("type", ""), item.get("sentence", "")))
            if item.get("note_zh"):
                lines.append("  - %s" % item.get("note_zh"))
        lines.append("")

    title = str(reading.get("title", "Reading"))
    text = _bold_words(str(reading.get("text", "")), words)
    lines.extend(["## 阅读：%s" % title, "", text, ""])
    if reading.get("translation_zh"):
        lines.extend(["<details><summary>参考翻译</summary>", "", str(reading.get("translation_zh")), "", "</details>", ""])
    questions = reading.get("questions", [])
    if isinstance(questions, list) and questions:
        lines.extend(["### 阅读理解", ""])
        for i, q in enumerate(questions, 1):
            if isinstance(q, dict):
                lines.append("%d. %s" % (i, q.get("question", "")))
        lines.append("")
        lines.append("<details><summary>参考答案</summary>")
        lines.append("")
        for i, q in enumerate(questions, 1):
            if isinstance(q, dict):
                lines.append("%d. %s" % (i, q.get("answer", "")))
        lines.extend(["", "</details>", ""])

    quiz = quiz or {}
    n_spelling = len(quiz.get("spelling", []) or []) or 20
    n_dictation = len(quiz.get("dictation", []) or []) or 1
    n_translation = len(quiz.get("translation", []) or []) or 3
    lines.extend([
        "## 打卡",
        "",
        "在 Telegram 回复 **打卡**。打卡后立即开始：",
        "",
        "- %d 题中文提示拼写" % n_spelling,
        "- %d 句英文听写" % n_dictation,
        "- %d 题英译中" % n_translation,
        "",
        "> 当前连续打卡：%d 天 · 累计：%d 天" % (stats.get("streak", 0), stats.get("total_checkins", 0)),
        "",
    ])
    return "\n".join(lines)


def carry_over_source(db: Database, day: str) -> Optional[Dict[str, Any]]:
    """最近一份课程没打卡时返回它——今天沿用它，不再生成新词。

    “缺一天打卡”就是上一份课程的 checked_in_at 为空：这时候发新课程只会把没学的词
    叠上去，用户永远追不上，所以宁可同一天内容再用一次，等打卡后再继续新词。
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM lessons WHERE lesson_date < ? ORDER BY lesson_date DESC LIMIT 1", (day,)
        ).fetchone()
    if row is None or row["checked_in_at"]:
        return None
    return row


def prepare_lesson(settings: Settings, db: Database, day: Optional[str] = None, force: bool = False, sync: bool = True) -> Dict[str, Any]:
    settings.ensure_dirs()
    day = day or date_str()
    existing = db.get_lesson(day)
    if existing and not force:
        payload = load_private_lesson(settings, day)
        render_index(db, settings)
        if sync:
            try:
                from .github_sync import sync_repo
                sync_repo(settings, "prepare: %s" % day, push=True, db=db)
            except Exception as exc:
                db.log("sync_error", str(exc))
        return payload
    if existing and existing["checked_in_at"]:
        raise RuntimeError("lesson is already checked in; refusing to overwrite")

    total = int(db.setting("daily_total_words", "15"))
    new_count = int(db.setting("daily_new_words", "10"))
    review_count = int(db.setting("daily_review_words", "5"))
    existing_payload = load_private_lesson(settings, day) if existing else None
    if existing_payload and existing_payload.get("words") and not force:
        selected = []
        for card in existing_payload.get("words", []):
            row = db.word_by_id(int(card.get("word_id")))
            if row is None:
                continue
            item = row_dict(row)
            item["role"] = str(card.get("role", "new"))
            selected.append(item)
    else:
        selected = select_lesson_words(db, total=total, new_count=new_count, review_count=review_count)
    if not selected:
        raise RuntimeError("word pool is empty; run import-words first")

    # 上一份课程还没打卡：今天沿用同一份内容（不生成新词、不调模型）。
    carried_from: Optional[str] = None
    model_name = ""
    if not force and str(db.setting("carry_over_unchecked", "1")) == "1":
        source = carry_over_source(db, day)
        if source is not None:
            prev_day = str(source["lesson_date"])
            prev = load_private_lesson(settings, prev_day)
            prev_cards = [dict(x) for x in (prev.get("words") or [])]
            if prev_cards:
                cards = prev_cards
                data = {
                    "words": prev_cards,
                    "sentence_patterns": prev.get("sentence_patterns", []),
                    "reading": prev.get("reading", {}),
                    "dictation_extra": prev.get("dictation_extra", []),
                }
                selected = []
                for card in prev_cards:
                    row = db.word_by_id(int(card.get("word_id")))
                    if row is None:
                        continue
                    item = row_dict(row)
                    item["role"] = str(card.get("role", "new"))
                    selected.append(item)
                if not selected:
                    raise RuntimeError("carry-over lesson has no usable words: %s" % prev_day)
                model_name = str(prev.get("model") or "")
                carried_from = prev_day

    if carried_from is None:
        client = DeepSeekClient(
            settings.deepseek_key,
            base_url=settings.deepseek_base_url,
            model=db.setting("model", settings.deepseek_model) or settings.deepseek_model,
        )
        prompt = lesson_prompt(selected, day)
        data = client.chat_json(prompt)
        errors = validate_content(data, selected)
        if errors:
            data2 = client.chat_json(repair_prompt(prompt, errors), temperature=0.3)
            if not validate_content(data2, selected):
                data = data2
            else:
                # Keep the first response but repair missing cards with dictionary fallbacks.
                data = data if isinstance(data.get("words"), list) else data2

        cards = _merge_cards(selected, data)
        model_name = client.model
    spelling_count = int(db.setting("quiz_spelling_count", "30"))
    dictation_count = int(db.setting("quiz_dictation_count", "1"))
    translation_count = int(db.setting("quiz_translation_count", "3"))
    blueprint = build_blueprint(db, cards, data, spelling_count, dictation_count, translation_count)
    payload = {
        "date": day,
        "generated_at": iso_now(),
        "model": model_name,
        "carried_from": carried_from,
        "words": cards,
        "sentence_patterns": data.get("sentence_patterns", []),
        "reading": data.get("reading", {}),
        "dictation_extra": data.get("dictation_extra", []),
        "quiz_blueprint": blueprint,
    }
    private_path = settings.private_lessons / ("%s.json" % day)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    from .stats import calculate_stats
    stats = calculate_stats(db)
    md = render_markdown(day, cards, data, stats, quiz=blueprint, carried_from=carried_from or "")
    md_path = settings.md_dir / ("%s.md" % day)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")
    html_path = settings.lessons_dir / ("%s.html" % day)
    html_path.write_text(page_html("%s 英语学习" % day, md, include_speak=True, home_href="../"), encoding="utf-8")

    lesson_id = db.upsert_lesson(day, str(md_path), str(private_path), status="prepared")
    db.replace_lesson_words(lesson_id, selected)
    new_ids = [int(x["id"]) for x in selected if x.get("role") == "new"]
    if carried_from is None:
        db.mark_learning(new_ids, add_days(day, 1))
    render_index(db, settings)
    if carried_from:
        db.log("prepare_carry", "date=%s from=%s words=%s" % (day, carried_from, len(cards)))
    else:
        db.log("prepare_lesson", "date=%s words=%s" % (day, len(cards)))
    if sync:
        try:
            from .github_sync import sync_repo
            sync_repo(settings, "lesson: %s" % day, push=True, db=db)
        except Exception as exc:
            db.log("sync_error", str(exc))
    return payload


def load_private_lesson(settings: Settings, day: str) -> Dict[str, Any]:
    path = settings.private_lessons / ("%s.json" % day)
    if not path.exists():
        raise RuntimeError("private lesson not found: %s" % path)
    return json.loads(path.read_text(encoding="utf-8"))


def pages_lesson_url(db: Database, settings: Settings, day: str) -> str:
    base = db.setting("pages_url", "").strip()
    if base:
        return base.rstrip("/") + "/lessons/%s.html" % day
    remote = db.setting("github_remote", "").strip()
    if remote.startswith("https://github.com/") and remote.endswith(".git"):
        repo = remote[:-4]
        return repo + "/blob/main/lessons/%s.md" % day
    return ""


def raw_preview_url(db: Database, day: str) -> str:
    remote = db.setting("github_remote", "").strip()
    if remote.startswith("https://github.com/") and remote.endswith(".git"):
        slug = remote[len("https://github.com/"):-4]
        return "https://raw.githack.com/%s/main/docs/lessons/%s.html" % (slug, day)
    return ""


def raw_preview_index_url(db: Database) -> str:
    remote = db.setting("github_remote", "").strip()
    if remote.startswith("https://github.com/") and remote.endswith(".git"):
        slug = remote[len("https://github.com/"):-4]
        return "https://raw.githack.com/%s/main/docs/index.html" % slug
    return ""


def github_lesson_url(db: Database, day: str) -> str:
    remote = db.setting("github_remote", "").strip()
    if remote.startswith("https://github.com/") and remote.endswith(".git"):
        repo = remote[:-4]
        return repo + "/blob/main/lessons/%s.md" % day
    return ""


def rerender_lesson(settings: Settings, db: Database, day: Optional[str] = None, sync: bool = True) -> Dict[str, Any]:
    day = day or date_str()
    payload = load_private_lesson(settings, day)
    cards = payload.get("words", [])
    from .stats import calculate_stats
    stats = calculate_stats(db)
    md = render_markdown(day, cards, payload, stats, quiz=payload.get("quiz_blueprint", {}), carried_from=str(payload.get("carried_from") or ""))
    md_path = settings.md_dir / ("%s.md" % day)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")
    html_path = settings.lessons_dir / ("%s.html" % day)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(page_html("%s 英语学习" % day, md, include_speak=True, home_href="../"), encoding="utf-8")
    db.upsert_lesson(day, str(md_path), str(settings.private_lessons / ("%s.json" % day)), status="prepared")
    render_index(db, settings)
    if sync:
        try:
            from .github_sync import sync_repo
            sync_repo(settings, "rerender: %s" % day, push=True, db=db)
        except Exception as exc:
            db.log("sync_error", str(exc))
    return payload


def push_message(db: Database, settings: Settings, payload: Dict[str, Any]) -> str:
    day = payload["date"]
    cards = payload.get("words", [])
    roles = {"new": 0, "review": 0, "error": 0}
    for c in cards:
        roles[str(c.get("role", "new"))] = roles.get(str(c.get("role", "new")), 0) + 1
    url = pages_lesson_url(db, settings, day)
    lines = [
        "📘 今日英语 %s" % day,
        "%d 个单词（%d 新词 · %d 复习 · %d 错词）" % (
            len(cards), roles.get("new", 0), roles.get("review", 0), roles.get("error", 0)
        ),
        "阅读：%s" % (payload.get("reading", {}) or {}).get("title", "今日阅读"),
    ]
    if payload.get("carried_from"):
        lines.insert(1, "♻️ 上次（%s）还没打卡，今天沿用同一份课程，不生成新词。" % payload["carried_from"])
    if url:
        lines.append("👉 Pages：%s" % url)
    gh_url = github_lesson_url(db, day)
    if gh_url:
        lines.append("📄 GitHub MD：%s" % gh_url)
    if db.setting("pages_fallback", "1") != "0":
        preview = raw_preview_url(db, day)
        if preview:
            lines.append("🌐 临时 HTML：%s" % preview)
    if not url and not gh_url:
        lines.append("👉 学习内容稍后同步到 GitHub")
    lines.extend(["", "学习完成后回复：打卡"])
    return "\n".join(lines)


def push_lesson(settings: Settings, db: Database, day: Optional[str] = None) -> str:
    day = day or date_str()
    existing = db.get_lesson(day)
    if not existing:
        prepare_lesson(settings, db, day=day, sync=True)
    payload = load_private_lesson(settings, day)
    message = push_message(db, settings, payload)
    notifier = Notifier(settings)
    notifier.send_text(message)
    with db.connect() as conn:
        conn.execute("UPDATE lessons SET pushed_at=?, status=CASE WHEN checked_in_at IS NULL THEN 'pushed' ELSE status END WHERE lesson_date=?", (iso_now(), day))
    render_index(db, settings)
    try:
        from .github_sync import sync_repo
        sync_repo(settings, "push: %s" % day, push=True, db=db)
    except Exception as exc:
        db.log("sync_error", str(exc))
    db.log("push_lesson", "date=%s" % day)
    return message
