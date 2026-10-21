from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

CST = timezone(timedelta(hours=8))


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE lines without printing secrets."""
    try:
        if not path.exists():
            return
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


def now_cst() -> datetime:
    return datetime.now(CST)


def today_cst() -> date:
    return now_cst().date()


def iso_now() -> str:
    return now_cst().isoformat(timespec="seconds")


def date_str(d: Optional[date] = None) -> str:
    return (d or today_cst()).isoformat()


def parse_date(value: Optional[str]) -> date:
    if not value or value == "today":
        return today_cst()
    if value == "yesterday":
        return today_cst() - timedelta(days=1)
    return date.fromisoformat(value)


def add_days(value: str, days: int) -> str:
    d = date.fromisoformat(value[:10]) + timedelta(days=days)
    return d.isoformat()


def normalize_answer(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9' -]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_sentence(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_line(text: str, limit: int = 240) -> str:
    text = (text or "").replace("\r", " ").replace("\n", "；").strip()
    # 词库里存在字面量 \n（反斜杠 + n），先还原再按分隔符处理
    text = text.replace("\\r", " ").replace("\\n", "；").replace("\\t", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(；\s*)+", "；", text)
    return text.strip("； ")[:limit]


class Settings:
    def __init__(self) -> None:
        env_file = Path(os.environ.get("ENGLISH_COACH_ENV_FILE", str(Path.home() / ".hermes" / ".env")))
        load_env_file(env_file)

        self.home = Path(os.environ.get("ENGLISH_COACH_HOME", str(Path.home() / ".hermes" / "english")))
        self.repo = Path(os.environ.get("ENGLISH_COACH_REPO", str(Path.home() / "english-daily")))
        self.db_path = Path(os.environ.get("ENGLISH_COACH_DB", str(self.home / "english.db")))
        self.private_lessons = Path(os.environ.get("ENGLISH_COACH_PRIVATE_LESSONS", str(self.home / "lessons")))
        self.tts_dir = Path(os.environ.get("ENGLISH_COACH_TTS_DIR", str(self.home / "tts")))
        self.public_dir = Path(os.environ.get("ENGLISH_COACH_PUBLIC_DIR", str(self.repo / "docs")))
        self.lessons_dir = self.public_dir / "lessons"
        self.md_dir = self.repo / "lessons"
        self.progress_path = self.public_dir / "data" / "progress.json"
        self.index_path = self.public_dir / "index.html"
        self.word_pool_path = Path(os.environ.get("ENGLISH_COACH_WORD_POOL", str(self.repo / "data" / "word_pool.json")))
        self.cache_dir = Path(os.environ.get("ENGLISH_COACH_CACHE", str(self.repo / "cache")))

        self.deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.github_token = os.environ.get("GITHUB_TOKEN", "")
        token_file = self.home / ".github_token"
        if not self.github_token and token_file.exists():
            try:
                self.github_token = token_file.read_text(encoding="utf-8").strip()
            except Exception:
                self.github_token = ""
        self.telegram_chat_id = (
            os.environ.get("ENGLISH_TELEGRAM_CHAT_ID")
            or os.environ.get("BILI_TELEGRAM_CHAT_ID")
            or "8425641024"
        )
        self.deepseek_base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.deepseek_model = os.environ.get("ENGLISH_DEEPSEEK_MODEL", "deepseek-flash")

    def ensure_dirs(self) -> None:
        for path in [self.home, self.private_lessons, self.tts_dir, self.lessons_dir, self.md_dir, self.progress_path.parent, self.cache_dir]:
            path.mkdir(parents=True, exist_ok=True)
