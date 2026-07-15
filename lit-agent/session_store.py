"""Publisher-scoped browser cookie persistence for ``requests.Session``.

Cookie values are intentionally never logged.  Files are plaintext browser
exports and therefore belong in the git-ignored local ``sessions/`` directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Iterable

import requests


def _domain_matches(domain: str, allowed: str) -> bool:
    domain = domain.lower().lstrip(".").strip()
    allowed = allowed.lower().lstrip(".").strip()
    return bool(domain and allowed) and (
        domain == allowed or domain.endswith("." + allowed)
    )


def _read_netscape(text: str) -> list[dict]:
    cookies = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        fields = line.split("\t", 6)
        if len(fields) != 7:
            continue
        domain, _, path, secure, expires, name, value = fields
        cookies.append({
            "domain": domain,
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
            "expires": expires,
            "name": name,
            "value": value,
        })
    return cookies


def read_cookie_file(path: str | Path) -> list[dict]:
    """Read common browser JSON exports or Netscape cookies.txt files."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _read_netscape(text)
    if isinstance(data, dict):
        data = data.get("cookies", [])
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


class CookieStore:
    def __init__(self, path: str | Path, allowed_domains: Iterable[str] = ()):
        self.path = Path(path)
        self.allowed_domains = tuple(d.lower().lstrip(".") for d in allowed_domains if d)

    def _normalize(self, raw: dict, now: float | None = None) -> dict | None:
        name = str(raw.get("name") or "").strip()
        value = raw.get("value")
        domain = str(raw.get("domain") or raw.get("host") or "").lower().strip()
        if not name or value is None or not domain:
            # A domainless requests cookie is a supercookie, so never import one.
            return None
        if self.allowed_domains and not any(
                _domain_matches(domain, allowed) for allowed in self.allowed_domains):
            return None
        expiry = raw.get("expires", raw.get("expirationDate", raw.get("expiration")))
        try:
            expiry = int(float(expiry)) if expiry not in (None, "") else 0
        except (TypeError, ValueError, OverflowError):
            expiry = 0
        if expiry > 0 and expiry <= int(now if now is not None else time.time()):
            return None
        return {
            "name": name,
            "value": str(value),
            "domain": domain,
            "path": str(raw.get("path") or "/"),
            "secure": bool(raw.get("secure", False)),
            "expires": max(0, expiry),
        }

    def load(self, now: float | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        return [cookie for raw in read_cookie_file(self.path)
                if (cookie := self._normalize(raw, now=now)) is not None]

    def apply(self, session: requests.Session, now: float | None = None) -> int:
        cookies = self.load(now=now)
        for cookie in cookies:
            kwargs = {
                "domain": cookie["domain"], "path": cookie["path"],
                "secure": cookie["secure"],
            }
            if cookie["expires"] > 0:
                kwargs["expires"] = cookie["expires"]
            session.cookies.set(cookie["name"], cookie["value"], **kwargs)
        return len(cookies)

    def save(self, cookies: Iterable[dict]) -> int:
        normalized = [cookie for raw in cookies
                      if (cookie := self._normalize(raw)) is not None]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return len(normalized)


class SessionRegistry:
    """Create isolated sessions from ``sessions/<publisher>.*`` cookie files."""

    def __init__(self, cookie_dir: str | Path, extra_domains: dict | None = None):
        self.cookie_dir = Path(cookie_dir)
        self.extra_domains = {
            str(key): tuple(value if isinstance(value, list) else [])
            for key, value in (extra_domains or {}).items()
        }

    def cookie_path(self, publisher_key: str) -> Path:
        for suffix in (".json", ".txt", ".cookies"):
            path = self.cookie_dir / f"{publisher_key}{suffix}"
            if path.exists():
                return path
        return self.cookie_dir / f"{publisher_key}.json"

    def allowed_domains(self, publisher_key: str, publisher_domains: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*publisher_domains, *self.extra_domains.get(publisher_key, ()))))

    def session_for(self, publisher_key: str, publisher_domains: Iterable[str]
                    ) -> tuple[requests.Session, int]:
        session = requests.Session()
        allowed = self.allowed_domains(publisher_key, publisher_domains)
        count = CookieStore(self.cookie_path(publisher_key), allowed).apply(session)
        return session, count

    def status(self, publisher_key: str, publisher_domains: Iterable[str]) -> dict:
        path = self.cookie_path(publisher_key)
        allowed = self.allowed_domains(publisher_key, publisher_domains)
        count = len(CookieStore(path, allowed).load())
        return {"publisher": publisher_key, "path": str(path), "exists": path.exists(),
                "valid_cookies": count, "allowed_domains": list(allowed)}


def import_cookie_file(source: str | Path, destination: str | Path,
                       allowed_domains: Iterable[str]) -> int:
    """Normalize an export into the local store, returning accepted count."""
    source_store = CookieStore(source, allowed_domains)
    cookies = source_store.load()
    return CookieStore(destination, allowed_domains).save(cookies)
