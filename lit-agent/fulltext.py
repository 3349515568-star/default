"""全文获取：用 Unpaywall 按 DOI 定位合法 OA 副本，下载 PDF，PyMuPDF 解析为文本。

下载级联：Unpaywall → Semantic Scholar OA 直链 → Europe PMC render
→ 官方出版商路由（可带机构 cookie）。
拿不到就返回 None，交由上层走"待取清单 + 机构权限手动下载"的 ingest 流程。
"""
import re
import fitz  # PyMuPDF
import requests

import browser_fetch
import elsevier_api
import pdf_utils
import publisher_routes
from session_store import SessionRegistry

UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"
EPMC_PDF = "https://europepmc.org/articles/{pmcid}?pdf=render"
PDF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/pdf,*/*",
}
LANDING_HEADERS = {
    "User-Agent": PDF_HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
}

_SESSION_REGISTRY: SessionRegistry | None = None


def _oa_pdf_urls(doi: str, email: str, prefer_repository: bool) -> list[str]:
    """问 Unpaywall 要这篇 DOI 的所有 OA 全文位置，返回候选 PDF 直链。"""
    try:
        r = requests.get(UNPAYWALL.format(doi=doi), params={"email": email}, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    if not data.get("is_oa"):
        return []
    locs = data.get("oa_locations") or []
    # repository 源（PMC/机构库/预印本）一般不反爬，优先；publisher 源命中率参半
    if prefer_repository:
        locs.sort(key=lambda x: 0 if x.get("host_type") == "repository" else 1)
    urls = []
    for loc in locs:
        for key in ("url_for_pdf", "url"):
            u = loc.get(key)
            if u and u not in urls:
                urls.append(u)
    return urls


MAX_PDF_BYTES = 100 * 1024 * 1024  # 超过 100MB 的下载直接放弃


def _download_pdf(url: str, session: "requests.Session | None" = None) -> bytes | None:
    """下载 url，确认是真 PDF 字节才返回。可选传入带 cookie 的 session。"""
    try:
        fetcher = session.get if session else requests.get
        r = fetcher(url, headers=PDF_HEADERS, timeout=(10, 60),
                    allow_redirects=True, stream=True)
    except Exception:
        return None
    try:
        if r.status_code != 200:
            return None
        chunks: list[bytes] = []
        total = 0
        for chunk in r.iter_content(chunk_size=1 << 16):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                return None
            chunks.append(chunk)
    except Exception:
        return None
    finally:
        r.close()
    return pdf_utils.validate_pdf_bytes(b"".join(chunks))


def configure_sessions(cookie_dir, extra_domains: dict | None = None):
    """Configure publisher-scoped cookie sessions from local browser exports."""
    global _SESSION_REGISTRY
    _SESSION_REGISTRY = SessionRegistry(cookie_dir, extra_domains)


def configure_publisher_browser(config: dict | None, root,
                                elsevier_api_key: str = "", elsevier_inst_token: str = ""):
    browser_fetch.configure(config, root)
    elsevier_api.configure(elsevier_api_key, elsevier_inst_token)


def close_publisher_browsers():
    browser_fetch.close_all()


def _official_publisher_pdf(rec: dict) -> bytes | None:
    """Try declarative/special official routes, then inspect the DOI landing page."""
    route = publisher_routes.route_for_record(rec, str(rec.get("url") or ""))
    if route is None:
        return None
    if route.key == "elsevier" and elsevier_api.configured():
        pdf = elsevier_api.fetch_pdf(str(rec.get("doi") or ""))
        if pdf:
            return pdf
    if browser_fetch.is_enabled(route.key):
        cookies = (_SESSION_REGISTRY.cookies_for(route.key, route.domains)
                   if _SESSION_REGISTRY is not None else [])
        # MDPI/Elsevier are intentionally browser-only after the Elsevier API attempt.
        return browser_fetch.fetch_pdf(rec, route, cookies)
    if _SESSION_REGISTRY is not None:
        session, _ = _SESSION_REGISTRY.session_for(route.key, route.domains)
    else:
        session = requests.Session()

    attempted = set()
    for url in publisher_routes.build_pdf_candidates(rec, str(rec.get("url") or "")):
        attempted.add(url)
        pdf = _download_pdf(url, session=session)
        if pdf:
            return pdf

    doi = str(rec.get("doi") or "").strip()
    if not doi:
        return None
    try:
        landing = session.get(
            f"https://doi.org/{doi}", headers=LANDING_HEADERS,
            timeout=60, allow_redirects=True,
        )
    except Exception:
        return None
    if landing.status_code == 200:
        body = pdf_utils.validate_pdf_bytes(landing.content)
        if body:
            return body
    if landing.status_code >= 400:
        return None
    for url in publisher_routes.build_pdf_candidates(rec, landing.url, landing.text):
        if url in attempted:
            continue
        attempted.add(url)
        pdf = _download_pdf(url, session=session)
        if pdf:
            return pdf
    return None


def fetch_pdf(rec: dict, email: str, prefer_repository: bool = True) -> bytes | None:
    """按级联策略尝试拿到这篇文献的 PDF 字节，全失败返回 None。"""
    # 1) Unpaywall 的所有 OA 位置
    if rec.get("doi"):
        for url in _oa_pdf_urls(rec["doi"], email, prefer_repository):
            pdf = _download_pdf(url)
            if pdf:
                return pdf
    # 2) Semantic Scholar 返回的合法 OA PDF 直链
    if rec.get("s2_pdf_url"):
        pdf = _download_pdf(rec["s2_pdf_url"])
        if pdf:
            return pdf
    # 3) Europe PMC 的 PDF render（合法仓储副本优先于付费墙站点）
    if rec.get("pmcid"):
        pdf = _download_pdf(EPMC_PDF.format(pmcid=rec["pmcid"]))
        if pdf:
            return pdf
    # 4) 官方出版商路由；若 sessions/<publisher> 有 cookie，会按域名隔离加载
    return _official_publisher_pdf(rec)


def pdf_to_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return _document_text(doc)


def pdf_file_to_text(path) -> str:
    doc = fitz.open(path)
    return _document_text(doc)


def _document_text(doc) -> str:
    """保留 PDF 页码边界，供总结阶段定位证据并按页安全分块。"""
    parts = []
    for page_no, page in enumerate(doc, 1):
        parts.append(f"===== PDF PAGE {page_no} =====\n{page.get_text()}")
    return "\n\n".join(parts)


def pdf_file_metadata(path) -> dict:
    """读取 PDF 自带的书目信息；字段缺失时返回空字符串。"""
    doc = fitz.open(path)
    metadata = doc.metadata or {}
    return {
        key: (metadata.get(key) or "").strip()
        for key in ("title", "author", "subject", "keywords")
    }


def metadata_hints(metadata: dict) -> dict:
    """把 PDF metadata 转成 ingest 可复用的题目、作者、期刊和年份提示。"""
    title = (metadata.get("title") or "").strip()
    author = (metadata.get("author") or "").strip()
    subject = (metadata.get("subject") or "").strip()
    # 常见 Elsevier subject: "Journal, volume (year) article. doi:..."
    journal = subject.split(",", 1)[0].strip() if "," in subject else ""
    year_match = re.search(r"\b(?:19|20)\d{2}\b", subject)
    return {
        "title": title,
        "authors": author,
        "journal": journal,
        "year": int(year_match.group(0)) if year_match else None,
    }


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.I)


def guess_doi(text: str, metadata: dict | None = None) -> str | None:
    """从 PDF metadata 或前部正文提取本文 DOI，避免误命中参考文献 DOI。"""
    sources = []
    if metadata:
        sources.append("\n".join(str(metadata.get(k) or "")
                                 for k in ("subject", "title", "keywords")))
    # 4000 字符会漏掉部分 Elsevier 首页 DOI；20000 仍通常处于正文前部。
    sources.append(text[:20000])
    for source in sources:
        m = DOI_RE.search(source)
        if m:
            return m.group(0).rstrip(".").lower()
    return None
