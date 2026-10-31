from __future__ import annotations

import csv
import json
import math
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .config import clean_line
from .db import Database


ECDICT_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
WORD_RE = re.compile(r"^[a-z][a-z'\-]{1,29}$")
_POS_PREFIX = re.compile(r"^((?:n|v|vt|vi|adj|adv|a|prep|conj|pron|num|art|aux|int|abbr)\.)\s*", re.I)
TAG_LEVEL = {"cet4": 1, "cet6": 2, "ielts": 3}
EXCLUDE_WORDS = {
    "a", "an", "the", "and", "or", "but", "if", "as", "for", "with", "from", "by",
    "in", "on", "at", "to", "of", "off", "up", "down", "out", "over", "under",
    "this", "that", "these", "those", "there", "here", "then", "than",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had", "do", "does", "did", "not", "no", "yes",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "mine", "yours", "hers", "ours", "theirs",
}


def download_ecdict(path: Path, url: str = ECDICT_URL) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 10_000_000:
        return path
    tmp = path.with_suffix(path.suffix + ".part")
    urllib.request.urlretrieve(url, str(tmp))
    tmp.replace(path)
    return path


def _int(value: Any, default: int = 0) -> int:
    try:
        text = str(value or "").strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def _difficulty(tags: Sequence[str], frq: int, bnc: int, word: str) -> float:
    level = min([TAG_LEVEL[t] for t in tags if t in TAG_LEVEL] or [3])
    rank = min([x for x in (frq, bnc) if x > 0] or [300000])
    # Lower is easier. Frequency dominates, exam band adds a small step.
    return round((math.log10(max(rank, 1)) / 6.0) * 0.70 + ((level - 1) / 2.0) * 0.20 + min(len(word), 16) / 16.0 * 0.10, 6)


def build_word_pool(csv_path: Path, output: Path, limit: int = 20000) -> List[Dict[str, Any]]:
    csv_path = Path(csv_path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    merged: Dict[str, Dict[str, Any]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="", errors="ignore") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_word = str(row.get("word", "")).strip().lower()
            if not WORD_RE.match(raw_word) or raw_word in EXCLUDE_WORDS:
                continue
            raw_tags = str(row.get("tag", "") or "").lower().split()
            tags = [t for t in raw_tags if t in TAG_LEVEL]
            if not tags:
                continue
            translation = clean_line(str(row.get("translation", "") or ""), 220)
            if not translation:
                continue
            phonetic = clean_line(str(row.get("phonetic", "") or ""), 80)
            frq = _int(row.get("frq"))
            bnc = _int(row.get("bnc"))
            item = {
                "word": raw_word,
                "phonetic": phonetic,
                "pos": clean_line(str(row.get("pos", "") or ""), 40),
                "meaning_zh": translation,
                "definition_en": clean_line(str(row.get("definition", "") or ""), 220),
                "tags": sorted(set(tags)),
                "collins": _int(row.get("collins")),
                "oxford": _int(row.get("oxford")),
                "bnc": bnc,
                "frq": frq,
                "exam_level": min(TAG_LEVEL[t] for t in tags),
            }
            item["difficulty"] = _difficulty(tags, frq, bnc, raw_word)
            prev = merged.get(raw_word)
            if prev is None:
                merged[raw_word] = item
            else:
                prev["tags"] = sorted(set(prev["tags"]) | set(tags))
                prev["exam_level"] = min(prev["exam_level"], item["exam_level"])
                prev["difficulty"] = min(prev["difficulty"], item["difficulty"])
                if not prev.get("phonetic") and item.get("phonetic"):
                    prev["phonetic"] = item["phonetic"]
                if len(item.get("meaning_zh", "")) > len(prev.get("meaning_zh", "")):
                    prev["meaning_zh"] = item["meaning_zh"]

    items = sorted(
        merged.values(),
        key=lambda x: (
            float(x.get("difficulty", 0)),
            int(x.get("frq") or 1000000),
            int(x.get("bnc") or 1000000),
            str(x.get("word", "")),
        ),
    )[:limit]
    output.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = {
        "source": ECDICT_URL,
        "license": "MIT",
        "count": len(items),
        "description": "CET4/CET6/IELTS words derived from ECDICT",
    }
    output.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def row_dict(row: Any) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def select_lesson_words(db: Database, total: int = 15, new_count: int = 10, review_count: int = 5) -> List[Dict[str, Any]]:
    total = max(1, int(total))
    new_count = max(0, int(new_count))
    review_count = max(0, int(review_count))
    if new_count + review_count != total:
        review_count = max(0, total - new_count)
    selected: List[Dict[str, Any]] = []
    used: set = set()

    for row in db.due_words(review_count):
        d = row_dict(row)
        d["role"] = "error" if int(d.get("wrong_count") or 0) > 0 else "review"
        selected.append(d)
        used.add(int(d["id"]))
        if len(selected) >= review_count:
            break

    if len(selected) < review_count:
        for row in db.error_words(review_count - len(selected), exclude_ids=used, due_only=True):
            d = row_dict(row)
            d["role"] = "error"
            selected.append(d)
            used.add(int(d["id"]))

    for row in db.new_words(max(1, total - len(selected)), exclude_ids=used):
        d = row_dict(row)
        d["role"] = "new"
        selected.append(d)
        used.add(int(d["id"]))
        if len(selected) >= total:
            break

    if len(selected) < total:
        for row in db.due_words(total - len(selected), exclude_ids=used):
            d = row_dict(row)
            d["role"] = "review"
            selected.append(d)
            used.add(int(d["id"]))
            if len(selected) >= total:
                break
    return selected[:total]


def select_quiz_words(db: Database, lesson_words: List[Dict[str, Any]], count: int = 20) -> List[Dict[str, Any]]:
    """当天全部新词 + 到期待复习词补足（只有到期的词才复习，不再按错次天天抽同一批）。"""
    count = max(1, int(count))
    today = [dict(x) for x in lesson_words]
    selected: List[Dict[str, Any]] = list(today)
    used: set = {int(item["id"]) for item in selected}

    # 只补 next_review_at <= 今天 的词：逾期最久优先，同一天的再按错次（见 db.due_words 排序）。
    # 提前复习（未到期）会把间隔压平，等于没有遗忘曲线——宁可当天少几题。
    remaining = count - len(selected)
    if remaining > 0:
        for row in db.due_words(remaining, exclude_ids=used):
            d = row_dict(row)
            d["role"] = "error" if int(d.get("wrong_count") or 0) > 0 else "review"
            selected.append(d)
            used.add(int(d["id"]))
            if len(selected) >= count:
                break

    if len(selected) < count:
        for row in db.error_words(count - len(selected), exclude_ids=used, due_only=True):
            d = row_dict(row)
            d["role"] = "error"
            selected.append(d)
            used.add(int(d["id"]))
            if len(selected) >= count:
                break
    return selected[:count]


def quiz_clue(meaning: str, pos: str = "") -> tuple:
    """把词库释义压成一条干净的猜词提示，返回 (词性, 中文)。

    词库常把多个词性的释义用字面量 \\n 连在一起，且同义词可多达七八个。
    猜词时最多保留 3 组词性（每组前 2 个义项），并把名词组排到最后：
    只取第一组会把冷门词性当主义（might→力量而非"可能"）。方括号领域标签（[计]/[医]）丢弃。
    """
    text = clean_line(meaning, 240)
    groups: List[List[Any]] = []  # [pos, [senses...]]
    for chunk in re.split(r"[；;]", text):
        chunk = chunk.strip()
        if not chunk or chunk.startswith("["):
            continue
        head = ""
        m = _POS_PREFIX.match(chunk)
        if m:
            head = m.group(1).lower()
            if head == "a.":
                head = "adj."
            chunk = chunk[m.end():].strip()
        senses = [x.strip() for x in re.split(r"[,，]", chunk) if x.strip()]
        # 丢掉夹杂在释义里的领域噪声（如 "DOS外部命令:…"）与超长句子
        senses = [s for s in senses if ":" not in s and "：" not in s and len(s) <= 14]
        if not senses:
            continue
        for g in groups:
            if g[0] == head:
                for s in senses:
                    if s not in g[1]:
                        g[1].append(s)
                break
        else:
            groups.append([head, senses])

    if not groups:
        return (pos.strip() if pos and pos.strip() not in ("-", "None") else "-"), (text or "-")

    # 情态动词常被词库的冷门名词义盖过（might→力量）：aux. 组排到最前，其余保持词库顺序
    groups.sort(key=lambda g: 0 if g[0] == "aux." else 1)
    groups = groups[:3]
    per_group = 3 if len(groups) == 1 else 2
    groups = [[g[0], g[1][:per_group]] for g in groups]

    heads = [g[0] for g in groups if g[0]]
    pos_text = pos.strip() if pos and pos.strip() not in ("-", "None") else (" / ".join(heads) or "-")

    if len(groups) == 1:
        body = ", ".join(groups[0][1])
    else:
        # 中文行里不再重复 "aux./n." 前缀：词性行已经按同样顺序列出来了
        body = "；".join(", ".join(g[1]) for g in groups if g[1])

    return pos_text, (body[:90] or text or "-")


def spelling_item(item: Dict[str, Any], position: int, total: int = 20) -> Dict[str, Any]:
    word = str(item.get("word", "")).strip().lower()
    pos, meaning = quiz_clue(
        str(item.get("meaning_zh", "") or item.get("definition_en", "")),
        str(item.get("pos", "") or ""),
    )
    prompt = "第 %d/%d 题\n词性：%s\n中文：%s\n%d 个字母\n请拼写英文单词：" % (
        position, total, pos or "-", meaning or "-", len(word)
    )
    return {
        "kind": "spelling",
        "word_id": int(item["id"]),
        "prompt": prompt,
        "expected": word,
        "hint": "共 %d 个字母" % len(word),
        "source": str(item.get("role", "")),
    }
