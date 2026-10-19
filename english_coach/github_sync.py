from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from .config import Settings


def _run(cmd, cwd: Path, check: bool = False, env: Optional[Dict[str, str]] = None):
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        env=env,
    )
    if check and proc.returncode != 0:
        raise RuntimeError("command failed: %s\n%s\n%s" % (" ".join(cmd), proc.stdout[-1000:], proc.stderr[-2000:]))
    return proc


def _ensure_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _run(["git", "init"], repo, check=True)
        # Git 2.25 (Ubuntu 20.04) does not support `git init -b`.
        _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], repo, check=True)
        _run(["git", "config", "user.name", "English Daily"], repo, check=True)
        _run(["git", "config", "user.email", "english-daily@localhost"], repo, check=True)


def _remote(settings: Settings, db: Any = None) -> str:
    if db is not None:
        value = db.setting("github_remote", "") or ""
        if value:
            return value.strip()
    return os.environ.get("ENGLISH_GITHUB_REMOTE", "").strip()


def _askpass(settings: Settings) -> Path:
    path = settings.home / ".git-askpass.sh"
    if not path.exists():
        path.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' x-access-token ;;\n"
            "  *) printf '%s\\n' \"$GITHUB_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(0o700)
    return path


def sync_repo(settings: Settings, message: str, push: bool = True, db: Any = None) -> Dict[str, Any]:
    repo = settings.repo
    _ensure_repo(repo)
    add_paths = [p.name for p in [repo / "docs", repo / "english_coach", repo / "scripts", repo / "tests", repo / "deploy", repo / ".github", repo / "data", repo / "README.md", repo / "requirements.txt", repo / ".gitignore"] if p.exists()]
    if not add_paths:
        return {"ok": False, "reason": "nothing to sync"}
    _run(["git", "add", "-A"] + add_paths, repo, check=True)
    status = _run(["git", "status", "--porcelain"], repo)
    committed = False
    if status.stdout.strip():
        _run(["git", "commit", "-m", message], repo, check=True)
        committed = True
    remote = _remote(settings, db)
    pushed = False
    push_error = ""
    if push and remote:
        env = os.environ.copy()
        env["GIT_ASKPASS"] = str(_askpass(settings))
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GITHUB_TOKEN"] = settings.github_token
        proc = _run(["git", "push", remote, "HEAD:main"], repo, env=env)
        if proc.returncode != 0:
            push_error = (proc.stderr or proc.stdout)[-2000:]
        else:
            pushed = True
    return {"ok": not bool(push_error), "committed": committed, "pushed": pushed, "remote": remote, "error": push_error}


def github_api(token: str, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not configured")
    url = "https://api.github.com" + path
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "english-daily",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError("GitHub API %s %s: %s %s" % (method, path, exc.code, body[:1000]))


def get_github_user(token: str) -> Dict[str, Any]:
    return github_api(token, "GET", "/user")


def create_repo(token: str, name: str, private: bool = False, description: str = "Daily English learning archive") -> Dict[str, Any]:
    return github_api(token, "POST", "/user/repos", {
        "name": name,
        "private": private,
        "description": description,
        "auto_init": False,
    })


def enable_pages(token: str, owner: str, repo: str, branch: str = "main", path: str = "/docs") -> Dict[str, Any]:
    return github_api(token, "POST", "/repos/%s/%s/pages" % (owner, repo), {
        "source": {"branch": branch, "path": path}
    })


def pages_url(owner: str, repo: str) -> str:
    return "https://%s.github.io/%s/" % (owner, repo)
