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
        for row in db.error_words(review_count - len(selected), exclude_ids=used):
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
    count = max(1, int(count))
    today = [dict(x) for x in lesson_words]
    selected: List[Dict[str, Any]] = []
    used: set = set()

    # 12 today, then 4 due/review, then 4 error words. Adapt if the pool is small.
    for i, item in enumerate(today):
        if i >= min(12, count):
            break
        selected.append(item)
        used.add(int(item["id"]))

    due_quota = min(4, count - len(selected))
    for row in db.due_words(due_quota, exclude_ids=used, include_errors=False):
        if len(selected) >= count:
            break
        d = row_dict(row)
        d["role"] = "review"
        selected.append(d)
        used.add(int(d["id"]))

    error_quota = min(4, count - len(selected))
    for row in db.error_words(error_quota, exclude_ids=used):
        if len(selected) >= count:
            break
        d = row_dict(row)
        d["role"] = "error"
        selected.append(d)
        used.add(int(d["id"]))

    for item in today:
        if len(selected) >= count:
            break
        if int(item["id"]) not in used:
            selected.append(item)
            used.add(int(item["id"]))

    if len(selected) < count:
        for row in db.new_words(count - len(selected), exclude_ids=used):
            d = row_dict(row)
            d["role"] = "new"
            selected.append(d)
            used.add(int(d["id"]))
            if len(selected) >= count:
                break
    return selected[:count]


def spelling_item(item: Dict[str, Any], position: int, total: int = 20) -> Dict[str, Any]:
    word = str(item.get("word", "")).strip().lower()
    meaning = clean_line(str(item.get("meaning_zh", "") or item.get("definition_en", "")), 150)
    pos = clean_line(str(item.get("pos", "")), 40)
    first = word[:1].upper() if word else "?"
    prompt = "第 %d/%d 题\n词性：%s\n中文：%s\n首字母：%s（%d 个字母）\n请拼写英文单词：" % (
        position, total, pos or "-", meaning or "-", first, len(word)
    )
    return {
        "kind": "spelling",
        "word_id": int(item["id"]),
        "prompt": prompt,
        "expected": word,
        "hint": "首字母 %s，共 %d 个字母" % (first, len(word)),
        "source": str(item.get("role", "")),
    }
