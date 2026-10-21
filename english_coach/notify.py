from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import requests

from .config import Settings


def split_text(text: str, limit: int = 3900) -> list:
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


class Notifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.token = settings.telegram_token
        self.chat_id = settings.telegram_chat_id
        self.voice = "en-US-AriaNeural"
        self.rate = os.environ.get("ENGLISH_TTS_RATE", "-30%")
        proxy = os.environ.get("TELEGRAM_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _api(self, method: str) -> str:
        return "https://api.telegram.org/bot%s/%s" % (self.token, method)

    def send_text(self, text: str) -> bool:
        if not self.enabled:
            print(text)
            return False
        ok = True
        for part in split_text(text):
            try:
                resp = requests.post(
                    self._api("sendMessage"),
                    data={"chat_id": self.chat_id, "text": part, "disable_web_page_preview": "true"},
                    timeout=30,
                    proxies=self.proxies,
                )
                if resp.status_code >= 400:
                    ok = False
            except Exception:
                ok = False
        return ok

    def send_audio(self, path: Path, caption: str = "") -> bool:
        path = Path(path)
        if not self.enabled:
            print("[audio] %s %s" % (path, caption))
            return False
        try:
            with path.open("rb") as fh:
                resp = requests.post(
                    self._api("sendAudio"),
                    data={"chat_id": self.chat_id, "caption": caption[:900], "title": caption[:60]},
                    files={"audio": (path.name, fh, "audio/mpeg")},
                    timeout=60,
                    proxies=self.proxies,
                )
            return resp.status_code < 400
        except Exception:
            return False

    def synthesize(self, text: str, voice: Optional[str] = None) -> Optional[Path]:
        text = (text or "").strip()
        if not text:
            return None
        voice = voice or self.voice
        digest = hashlib.sha1((voice + "\n" + self.rate + "\n" + text).encode("utf-8")).hexdigest()[:16]
        out = self.settings.tts_dir / ("%s.mp3" % digest)
        if out.exists() and out.stat().st_size > 100:
            return out
        edge = shutil.which("edge-tts") or str(Path(sys.executable).parent / "edge-tts")
        if not Path(edge).exists():
            return None
        try:
            subprocess.run(
                [edge, "--voice", voice, "--rate", self.rate, "--text", text, "--write-media", str(out)],
                check=True,
                timeout=90,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if out.exists() and out.stat().st_size > 100:
                return out
        except Exception:
            return None
        return None

    def speak(self, text: str, caption: str = "") -> bool:
        path = self.synthesize(text)
        if path:
            return self.send_audio(path, caption or text)
        return self.send_text(caption or ("发音不可用，请参考音标：%s" % text))
