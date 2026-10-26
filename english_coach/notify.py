from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
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
        # 直传若走不通（当前网络上传 body 必超时），尽快失败并改走 URL 发送
        self.upload_timeout = float(os.environ.get("ENGLISH_TTS_UPLOAD_TIMEOUT", "25"))
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
                    timeout=self.upload_timeout,
                    proxies=self.proxies,
                )
            if resp.status_code < 400:
                return True
        except Exception:
            pass
        # 上传通道经常被墙成 504：退回“发布到 GitHub Pages 仓库，再让 Telegram 按 URL 抓取”。
        url = self.publish_audio(path)
        if not url:
            return False
        try:
            resp = requests.post(
                self._api("sendAudio"),
                data={"chat_id": self.chat_id, "audio": url, "caption": caption[:900], "title": caption[:60]},
                timeout=90,
                proxies=self.proxies,
            )
            return resp.status_code < 400
        except Exception:
            return False

    def publish_audio(self, path: Path) -> Optional[str]:
        """把音频复制进仓库的 docs/audio 并推送，返回 raw.githubusercontent 直链。

        Telegram 的上传接口在当前网络下会超时（上传 body 走不通），但按 URL 发音频可用。
        """
        remote = os.environ.get("ENGLISH_GITHUB_REMOTE", "").strip()
        if not (remote.startswith("https://github.com/") and remote.endswith(".git")):
            return None
        slug = remote[len("https://github.com/"):-4]
        dest = self.settings.repo / "docs" / "audio" / path.name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or dest.stat().st_size != path.stat().st_size:
                shutil.copy(path, dest)
            from .github_sync import sync_repo
            sync_repo(self.settings, "audio: %s" % path.stem, push=True)
        except Exception:
            return None
        url = "https://raw.githubusercontent.com/%s/main/docs/audio/%s" % (slug, path.name)
        for _ in range(4):
            try:
                if requests.head(url, timeout=15, proxies=self.proxies).status_code == 200:
                    return url
            except Exception:
                pass
            time.sleep(2)
        return None

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
