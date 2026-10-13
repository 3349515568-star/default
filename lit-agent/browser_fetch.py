"""Visible CloakBrowser fallback for protected official publisher pages."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse

import fitz  # PyMuPDF

import pdf_utils
import publisher_routes


_enabled = False
_publishers: set[str] = set()
_profile_dir = Path("browser_profiles")
_humanize = True
_headed = True
_timeout_ms = 90_000
_interactive_wait_seconds = 120
_locale = ""
_timezone = ""
_proxy_env = "CLOAKBROWSER_PROXY"
_contexts: dict[str, object] = {}
_interaction_offered: set[str] = set()


def configure(config: dict | None, root: str | Path):
    """Configure the browser route without launching Chromium."""
    global _enabled, _publishers, _profile_dir, _humanize, _headed
    global _timeout_ms, _interactive_wait_seconds, _locale, _timezone, _proxy_env
    if _contexts:
        close_all()
    _interaction_offered.clear()
    config = config or {}
    _enabled = bool(config.get("enabled", False))
    _publishers = {str(value).strip().lower()
                   for value in config.get("publishers", ["elsevier", "mdpi"])}
    profile_dir = Path(config.get("profile_dir", "browser_profiles"))
    _profile_dir = profile_dir if profile_dir.is_absolute() else Path(root) / profile_dir
    _humanize = bool(config.get("humanize", True))
    _headed = bool(config.get("headed", True))
    _timeout_ms = max(5_000, int(float(config.get("timeout_seconds", 90)) * 1000))
    _interactive_wait_seconds = max(0, int(config.get("interactive_wait_seconds", 120)))
    _locale = str(config.get("locale", "") or "").strip()
    _timezone = str(config.get("timezone", "") or "").strip()
    _proxy_env = str(config.get("proxy_env", "CLOAKBROWSER_PROXY") or "").strip()


def is_enabled(publisher_key: str) -> bool:
    return _enabled and publisher_key.lower() in _publishers


def _launch_context(publisher_key: str):
    from cloakbrowser import launch_persistent_context

    profile = _profile_dir / publisher_key
    profile.mkdir(parents=True, exist_ok=True)
    kwargs = {
        "headless": not _headed,
        "humanize": _humanize,
        "viewport": None,
        "accept_downloads": True,
    }
    if _locale:
        kwargs["locale"] = _locale
    if _timezone:
        kwargs["timezone"] = _timezone
    proxy = os.getenv(_proxy_env, "").strip() if _proxy_env else ""
    if proxy:
        kwargs["proxy"] = proxy
    return launch_persistent_context(profile, **kwargs)


def _context_for(publisher_key: str):
    context = _contexts.get(publisher_key)
    if context is None:
        context = _launch_context(publisher_key)
        _contexts[publisher_key] = context
    return context


def close_all():
    """Close contexts on the same command thread that created them."""
    for key, context in list(_contexts.items()):
        try:
            context.close()
        except Exception:
            pass
        finally:
            _contexts.pop(key, None)


def playwright_cookies(cookies: list[dict]) -> list[dict]:
    """Convert normalized CookieStore rows to Playwright cookie dictionaries."""
    result = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").strip()
        name = str(cookie.get("name") or "").strip()
        if not domain or not name:
            continue
        result.append({
            "name": name,
            "value": str(cookie.get("value") or ""),
            "domain": domain,
            "path": str(cookie.get("path") or "/"),
            "secure": bool(cookie.get("secure", False)),
            "expires": float(cookie.get("expires") or -1),
        })
    return result


def _response_pdf(response) -> bytes | None:
    if response is None:
        return None
    try:
        body = response.body()
    except Exception:
        return None
    return pdf_utils.validate_pdf_bytes(body)


def _best_captured_pdf(candidates: list[bytes], rec: dict) -> bytes:
    """从捕获的 PDF 里挑正文：优先正文前 3000 字含本文 DOI 的那个；
    都不含时取页数最多的（正文通常比预览页或附件页数多）。"""
    doi = str(rec.get("doi") or "").strip().lower()
    best, best_pages = candidates[0], -1
    for body in candidates:
        try:
            with fitz.open(stream=body, filetype="pdf") as doc:
                pages = doc.page_count
                if doi:
                    text = ""
                    for page in doc:
                        text += page.get_text()
                        if len(text) >= 3000:
                            break
                    if doi in text[:3000].lower():
                        return body
        except Exception:
            pages = 0
        if pages > best_pages:
            best, best_pages = body, pages
    return best


def _download_pdf(download) -> bytes | None:
    try:
        path = download.path()
        body = Path(path).read_bytes() if path else b""
    except Exception:
        return None
    return body if body[:4] == b"%PDF" else None


def _live_candidates(page, rec: dict) -> list[str]:
    try:
        route = publisher_routes.route_for_record(rec, page.url)
        candidates = publisher_routes.build_pdf_candidates(rec, page.url, page.content())
        if route is None:
            return []
        return [url for url in candidates if _official_candidate(route, url)]
    except Exception:
        return []


def _official_candidate(route: publisher_routes.PublisherRoute, url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain)
               for domain in route.domains)


def fetch_pdf(rec: dict, route: publisher_routes.PublisherRoute,
              cookies: list[dict] | None = None) -> bytes | None:
    """Open a headed persistent browser and retrieve an official article PDF."""
    if not is_enabled(route.key):
        return None
    try:
        context = _context_for(route.key)
        imported = playwright_cookies(cookies or [])
        if imported:
            context.add_cookies(imported)
        page = context.new_page()
    except Exception as exc:
        print(f"  · CloakBrowser 启动失败（{route.key}）：{type(exc).__name__}", file=sys.stderr)
        _contexts.pop(route.key, None)
        return None

    captured_responses: list[bytes] = []
    downloads = []

    def capture_response(response):
        try:
            url = str(response.url or "")
            if not url or not _official_candidate(route, url):
                return
            if publisher_routes.SUPPLEMENT_RE.search(url):
                return
            content_type = (response.headers.get("content-type") or "").lower()
            if "application/pdf" in content_type or "pdf" in url.lower():
                body = _response_pdf(response)
                if body:
                    captured_responses.append(body)
        except Exception:
            pass

    page.on("response", capture_response)
    page.on("download", lambda download: downloads.append(download))
    attempted: set[str] = set()
    queue = publisher_routes.build_pdf_candidates(rec, str(rec.get("url") or ""))
    doi = str(rec.get("doi") or "").strip()
    if route.key in {"elsevier", "mdpi"} and doi:
        queue.insert(0, f"https://doi.org/{doi}")
    elif not queue and doi:
        queue.append(f"https://doi.org/{doi}")

    def collected_pdf() -> bytes | None:
        if captured_responses:
            return _best_captured_pdf(captured_responses, rec)
        for download in list(downloads):
            body = _download_pdf(download)
            if body:
                return body
        return None

    def visit(url: str) -> bytes | None:
        if not url or url in attempted:
            return None
        attempted.add(url)
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=_timeout_ms)
        except Exception:
            response = None
        return _response_pdf(response) or collected_pdf()

    try:
        while queue:
            url = queue.pop(0)
            body = visit(url)
            if body:
                return body
            for candidate in _live_candidates(page, rec):
                if candidate not in attempted and candidate not in queue:
                    queue.append(candidate)

        if (_interactive_wait_seconds > 0 and route.key not in _interaction_offered):
            _interaction_offered.add(route.key)
            print(
                f"  · 已打开 {route.key} 可视化浏览器；可在 {_interactive_wait_seconds} 秒内完成登录或验证。",
                file=sys.stderr,
            )
            deadline = time.monotonic() + _interactive_wait_seconds
            while time.monotonic() < deadline:
                body = collected_pdf()
                if body:
                    return body
                candidates = [url for url in _live_candidates(page, rec) if url not in attempted]
                if candidates:
                    body = visit(candidates[0])
                    if body:
                        return body
                time.sleep(2)
        return collected_pdf()
    finally:
        try:
            page.close()
        except Exception:
            pass
