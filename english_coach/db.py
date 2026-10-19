from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import add_days, date_str, iso_now, now_cst, today_cst


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL UNIQUE,
    phonetic TEXT DEFAULT '',
    pos TEXT DEFAULT '',
    meaning_zh TEXT DEFAULT '',
    definition_en TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    collins INTEGER DEFAULT 0,
    oxford INTEGER DEFAULT 0,
    bnc INTEGER DEFAULT 0,
    frq INTEGER DEFAULT 0,
    difficulty REAL DEFAULT 0,
    exam_level INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS word_state (
    word_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'new',
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    ease REAL NOT NULL DEFAULT 2.5,
    interval_days INTEGER NOT NULL DEFAULT 0,
    next_review_at TEXT,
    last_seen_at TEXT,
    last_result TEXT
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_date TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'prepared',
    md_path TEXT,
    json_path TEXT,
    pushed_at TEXT,
    checked_in_at TEXT,
    quiz_started_at TEXT,
    quiz_completed_at TEXT,
    quiz_score REAL DEFAULT 0,
    quiz_total REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lesson_words (
    lesson_id INTEGER NOT NULL,
    word_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (lesson_id, word_id)
);
CREATE TABLE IF NOT EXISTS quiz_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    current_index INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS quiz_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    word_id INTEGER,
    prompt TEXT NOT NULL,
    expected TEXT NOT NULL,
    hint TEXT DEFAULT '',
    source TEXT DEFAULT '',
    user_answer TEXT,
    correct INTEGER,
    score REAL,
    answered_at TEXT,
    UNIQUE(session_id, position)
);
CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id INTEGER NOT NULL UNIQUE,
    checked_in_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'telegram'
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS pause_dates (
    date TEXT PRIMARY KEY,
    reason TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_word_state_review ON word_state(next_review_at);
CREATE INDEX IF NOT EXISTS idx_quiz_session_lesson ON quiz_sessions(lesson_id);
CREATE INDEX IF NOT EXISTS idx_quiz_items_session ON quiz_items(session_id, position);
"""

DEFAULTS = {
    "start_date": "",
    "daily_total_words": "15",
    "daily_new_words": "10",
    "daily_review_words": "5",
    "quiz_spelling_count": "20",
    "quiz_dictation_count": "4",
    "push_time": "20:30",
    "prepare_time": "19:40",
    "telegram_chat_id": "8425641024",
    "pages_url": "",
    "pages_fallback": "1",
    "github_remote": "",
    "tts_voice": "en-US-AriaNeural",
    "model": "deepseek-flash",
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            for key, value in DEFAULTS.items():
                conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))
            row = conn.execute("SELECT value FROM settings WHERE key='start_date'").fetchone()
            if row is None or not str(row["value"]).strip():
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES('start_date', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (today_cst().isoformat(),),
                )
        self.log("init", "database ready")

    def setting(self, key: str, default: Optional[str] = None) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is not None:
            return str(row["value"])
        return DEFAULTS.get(key, default if default is not None else "")

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def settings(self) -> Dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {str(r["key"]): str(r["value"]) for r in rows}

    def log(self, kind: str, detail: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events(created_at, kind, detail) VALUES(?, ?, ?)",
                (iso_now(), kind, detail[:2000]),
            )

    def import_words(self, path: Path) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("word pool must be a JSON list")
        inserted = 0
        with self.connect() as conn:
            for item in data:
                word = str(item.get("word", "")).strip().lower()
                if not word:
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO words
                    (word, phonetic, pos, meaning_zh, definition_en, tags, collins, oxford, bnc, frq, difficulty, exam_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        word,
                        str(item.get("phonetic", "")),
                        str(item.get("pos", "")),
                        str(item.get("meaning_zh", "")),
                        str(item.get("definition_en", "")),
                        " ".join(item.get("tags", [])) if isinstance(item.get("tags"), list) else str(item.get("tags", "")),
                        int(item.get("collins", 0) or 0),
                        int(item.get("oxford", 0) or 0),
                        int(item.get("bnc", 0) or 0),
                        int(item.get("frq", 0) or 0),
                        float(item.get("difficulty", 0) or 0),
                        int(item.get("exam_level", 1) or 1),
                    ),
                )
                if cur.rowcount:
                    inserted += 1
        self.log("import_words", "inserted=%s source=%s" % (inserted, path))
        return inserted

    def word_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM words").fetchone()
        return int(row["n"] if row else 0)

    def word(self, word: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM words WHERE word=?", (word.lower(),)).fetchone()

    def word_by_id(self, word_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM words WHERE id=?", (int(word_id),)).fetchone()

    def due_words(self, limit: int, exclude_ids: Optional[Iterable[int]] = None, include_errors: bool = True) -> List[sqlite3.Row]:
        exclude = list(exclude_ids or [])
        now = iso_now()
        sql = """SELECT w.*, s.status AS state_status, s.wrong_count, s.next_review_at
                 FROM words w JOIN word_state s ON s.word_id=w.id
                 WHERE s.next_review_at IS NOT NULL AND s.next_review_at <= ?
                 AND s.status != 'new'"""
        params: List[Any] = [now]
        if not include_errors:
            sql += " AND s.wrong_count = 0"
        if exclude:
            sql += " AND w.id NOT IN (%s)" % ",".join("?" for _ in exclude)
            params.extend(exclude)
        sql += " ORDER BY s.wrong_count DESC, s.next_review_at ASC, w.difficulty ASC LIMIT ?"
        params.append(int(limit))
        with self.connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    def error_words(self, limit: int, exclude_ids: Optional[Iterable[int]] = None) -> List[sqlite3.Row]:
        exclude = list(exclude_ids or [])
        sql = """SELECT w.*, s.status AS state_status, s.wrong_count, s.next_review_at
                 FROM words w JOIN word_state s ON s.word_id=w.id
                 WHERE s.wrong_count > 0"""
        params: List[Any] = []
        if exclude:
            sql += " AND w.id NOT IN (%s)" % ",".join("?" for _ in exclude)
            params.extend(exclude)
        sql += " ORDER BY s.wrong_count DESC, s.next_review_at ASC LIMIT ?"
        params.append(int(limit))
        with self.connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    def new_words(self, limit: int, exclude_ids: Optional[Iterable[int]] = None) -> List[sqlite3.Row]:
        exclude = list(exclude_ids or [])
        sql = """SELECT w.* FROM words w
                 LEFT JOIN word_state s ON s.word_id=w.id
                 WHERE s.word_id IS NULL"""
        params: List[Any] = []
        if exclude:
            sql += " AND w.id NOT IN (%s)" % ",".join("?" for _ in exclude)
            params.extend(exclude)
        sql += " ORDER BY w.difficulty ASC, CASE WHEN w.frq > 0 THEN w.frq ELSE 1000000 END ASC, w.word ASC LIMIT ?"
        params.append(int(limit))
        with self.connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    def get_lesson(self, lesson_date: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM lessons WHERE lesson_date=?", (lesson_date,)).fetchone()

    def get_lesson_by_id(self, lesson_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM lessons WHERE id=?", (int(lesson_id),)).fetchone()

    def upsert_lesson(self, lesson_date: str, md_path: str, json_path: str, status: str = "prepared") -> int:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO lessons(lesson_date, status, md_path, json_path)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(lesson_date) DO UPDATE SET
                  status=CASE WHEN lessons.checked_in_at IS NOT NULL THEN lessons.status ELSE excluded.status END,
                  md_path=excluded.md_path, json_path=excluded.json_path""",
                (lesson_date, status, md_path, json_path),
            )
            row = conn.execute("SELECT id FROM lessons WHERE lesson_date=?", (lesson_date,)).fetchone()
        return int(row["id"])

    def replace_lesson_words(self, lesson_id: int, words: List[Dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM lesson_words WHERE lesson_id=?", (int(lesson_id),))
            for i, item in enumerate(words):
                conn.execute(
                    "INSERT INTO lesson_words(lesson_id, word_id, role, position) VALUES(?, ?, ?, ?)",
                    (int(lesson_id), int(item["id"]), str(item.get("role", "new")), i),
                )

    def lesson_words(self, lesson_id: int) -> List[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT w.*, lw.role, lw.position FROM lesson_words lw
                   JOIN words w ON w.id=lw.word_id WHERE lw.lesson_id=? ORDER BY lw.position""",
                (int(lesson_id),),
            ).fetchall())

    def mark_learning(self, word_ids: Iterable[int], next_review_at: str) -> None:
        with self.connect() as conn:
            for wid in word_ids:
                conn.execute(
                    """INSERT INTO word_state(word_id, status, next_review_at, last_seen_at)
                       VALUES(?, 'learning', ?, ?)
                       ON CONFLICT(word_id) DO UPDATE SET
                         status=CASE WHEN status='new' THEN 'learning' ELSE status END,
                         next_review_at=COALESCE(next_review_at, excluded.next_review_at),
                         last_seen_at=excluded.last_seen_at""",
                    (int(wid), next_review_at, iso_now()),
                )

    def update_word_result(self, word_id: int, correct: bool) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM word_state WHERE word_id=?", (int(word_id),)).fetchone()
            if row is None:
                conn.execute("INSERT INTO word_state(word_id) VALUES(?)", (int(word_id),))
                row = conn.execute("SELECT * FROM word_state WHERE word_id=?", (int(word_id),)).fetchone()
            correct_count = int(row["correct_count"])
            wrong_count = int(row["wrong_count"])
            streak = int(row["streak"])
            ease = float(row["ease"] or 2.5)
            if correct:
                correct_count += 1
                streak += 1
                ease = min(3.0, ease + 0.05)
                intervals = [1, 3, 7, 14, 30, 60]
                interval = intervals[min(streak - 1, len(intervals) - 1)]
                next_at = add_days(date_str(), interval)
                status = "review"
                last_result = "correct"
            else:
                wrong_count += 1
                streak = 0
                ease = max(1.3, ease - 0.2)
                interval = 1
                next_at = add_days(date_str(), 1)
                status = "learning"
                last_result = "wrong"
            conn.execute(
                """UPDATE word_state SET status=?, correct_count=?, wrong_count=?, streak=?, ease=?,
                   interval_days=?, next_review_at=?, last_seen_at=?, last_result=? WHERE word_id=?""",
                (status, correct_count, wrong_count, streak, ease, interval, next_at, iso_now(), last_result, int(word_id)),
            )

    def checkin(self, lesson_id: int, source: str = "telegram") -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO checkins(lesson_id, checked_in_at, source) VALUES(?, ?, ?)",
                (int(lesson_id), iso_now(), source),
            )
            if cur.rowcount:
                conn.execute(
                    "UPDATE lessons SET status='checked_in', checked_in_at=? WHERE id=?",
                    (iso_now(), int(lesson_id)),
                )
                return True
        return False

    def is_checked_in(self, lesson_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM checkins WHERE lesson_id=?", (int(lesson_id),)).fetchone()
        return row is not None

    def checked_dates(self) -> List[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT l.lesson_date FROM checkins c JOIN lessons l ON l.id=c.lesson_id ORDER BY l.lesson_date"
            ).fetchall()
        return [str(r["lesson_date"]) for r in rows]

    def pause_dates(self) -> List[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT date FROM pause_dates ORDER BY date").fetchall()
        return [str(r["date"]) for r in rows]

    def add_pause(self, d: str, reason: str = "") -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO pause_dates(date, reason) VALUES(?, ?)", (d, reason))

    def lessons(self, limit: int = 60) -> List[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                "SELECT * FROM lessons ORDER BY lesson_date DESC LIMIT ?", (int(limit),)
            ).fetchall())

    def active_session(self) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM quiz_sessions WHERE status IN ('active','paused') ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def create_session(self, lesson_id: int, items: List[Dict[str, Any]]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO quiz_sessions(lesson_id, started_at, status, current_index, score, total) VALUES(?, ?, 'active', 0, 0, ?)",
                (int(lesson_id), iso_now(), len(items)),
            )
            session_id = int(cur.lastrowid)
            for i, item in enumerate(items):
                conn.execute(
                    """INSERT INTO quiz_items(session_id, position, kind, word_id, prompt, expected, hint, source)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        i,
                        str(item.get("kind", "spelling")),
                        item.get("word_id"),
                        str(item.get("prompt", "")),
                        str(item.get("expected", "")),
                        str(item.get("hint", "")),
                        str(item.get("source", "")),
                    ),
                )
            conn.execute("UPDATE lessons SET quiz_started_at=?, status='quiz_active' WHERE id=?", (iso_now(), int(lesson_id)))
        return session_id

    def session_items(self, session_id: int) -> List[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                "SELECT * FROM quiz_items WHERE session_id=? ORDER BY position", (int(session_id),)
            ).fetchall())

    def current_item(self, session_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            session = conn.execute("SELECT * FROM quiz_sessions WHERE id=?", (int(session_id),)).fetchone()
            if session is None:
                return None
            return conn.execute(
                "SELECT * FROM quiz_items WHERE session_id=? AND position=?",
                (int(session_id), int(session["current_index"])),
            ).fetchone()

    def answer_item(self, item_id: int, text: str, correct: bool, score: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE quiz_items SET user_answer=?, correct=?, score=?, answered_at=? WHERE id=?",
                (text, 1 if correct else 0, float(score), iso_now(), int(item_id)),
            )

    def advance_session(self, session_id: int, score: float) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            session = conn.execute("SELECT * FROM quiz_sessions WHERE id=?", (int(session_id),)).fetchone()
            if session is None:
                return None
            idx = int(session["current_index"]) + 1
            total = int(session["total"])
            if idx >= total:
                conn.execute(
                    "UPDATE quiz_sessions SET status='completed', completed_at=?, current_index=?, score=? WHERE id=?",
                    (iso_now(), idx, float(score), int(session_id)),
                )
                conn.execute(
                    "UPDATE lessons SET quiz_completed_at=?, quiz_score=?, quiz_total=?, status='completed' WHERE id=?",
                    (iso_now(), float(score), float(total), int(session["lesson_id"])),
                )
                return None
            conn.execute(
                "UPDATE quiz_sessions SET current_index=?, score=? WHERE id=?",
                (idx, float(score), int(session_id)),
            )
            return conn.execute(
                "SELECT * FROM quiz_items WHERE session_id=? AND position=?",
                (int(session_id), idx),
            ).fetchone()

    def set_session_status(self, session_id: int, status: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE quiz_sessions SET status=? WHERE id=?", (status, int(session_id)))

    def top_errors(self, limit: int = 20) -> List[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT w.word, w.phonetic, w.pos, w.meaning_zh, s.wrong_count, s.next_review_at
                   FROM words w JOIN word_state s ON s.word_id=w.id
                   WHERE s.wrong_count > 0 ORDER BY s.wrong_count DESC, s.next_review_at ASC LIMIT ?""",
                (int(limit),),
            ).fetchall())
