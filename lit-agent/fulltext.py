"""全文获取：用 Unpaywall 按 DOI 定位合法 OA 副本，下载 PDF，PyMuPDF 解析为文本。

下载级联：Unpaywall → 金色 OA 出版商直连(Frontiers/PLOS) → Europe PMC render。
拿不到就返回 None，交由上层走"待取清单 + 机构权限手动下载"的 ingest 流程。
"""
import re
import fitz  # PyMuPDF
import requests

UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"
EPMC_PDF = "https://europepmc.org/articles/{pmcid}?pdf=render"
PDF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/pdf,*/*",
}


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


def _download_pdf(url: str) -> bytes | None:
    """下载 url，确认是真 PDF 字节才返回。"""
    try:
        r = requests.get(url, headers=PDF_HEADERS, timeout=90, allow_redirects=True)
    except Exception:
        return None
    if r.status_code == 200 and r.content[:4] == b"%PDF":
        return r.content
    return None


# 金色 OA 出版商：按 DOI 直接拼 PDF 链接，兜 Unpaywall 还没收录的新文。
# 仅收录「裸 requests 就能下到真 PDF」的（已实测）。MDPI 用 Akamai 机器人质询、
# 需无头浏览器，不收录——其 PMC 版进库后由下面的 EPMC render 兜底。
_PLOS_JOURNALS = {
    "pone": "plosone", "pbio": "plosbiology", "pmed": "plosmedicine",
    "pgen": "plosgenetics", "pcbi": "ploscompbiol", "ppat": "plospathogens",
    "pntd": "plosntds",
}


def _publisher_pdf_urls(rec: dict) -> list[str]:
    doi = (rec.get("doi") or "").lower()
    if not doi:
        return []
    if doi.startswith("10.3389/"):            # Frontiers
        return [f"https://www.frontiersin.org/articles/{doi}/pdf"]
    if doi.startswith("10.1371/"):            # PLOS
        m = re.search(r"journal\.([a-z]+)\.", doi)
        if m and m.group(1) in _PLOS_JOURNALS:
            return [f"https://journals.plos.org/{_PLOS_JOURNALS[m.group(1)]}"
                    f"/article/file?id={doi}&type=printable"]
    return []


def fetch_pdf(rec: dict, email: str, prefer_repository: bool = True) -> bytes | None:
    """按级联策略尝试拿到这篇文献的 PDF 字节，全失败返回 None。"""
    # 1) Unpaywall 的所有 OA 位置
    if rec.get("doi"):
        for url in _oa_pdf_urls(rec["doi"], email, prefer_repository):
            pdf = _download_pdf(url)
            if pdf:
                return pdf
    # 2) 金色 OA 出版商直连（Frontiers/PLOS），兜 Unpaywall 未收录的新文
    for url in _publisher_pdf_urls(rec):
        pdf = _download_pdf(url)
        if pdf:
            return pdf
    # 3) Europe PMC 的 PDF render（PMC 版进库后可用，兜 MDPI 等）
    if rec.get("pmcid"):
        pdf = _download_pdf(EPMC_PDF.format(pmcid=rec["pmcid"]))
        if pdf:
            return pdf
    return None


def pdf_to_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "".join(page.get_text() for page in doc)


def pdf_file_to_text(path) -> str:
    doc = fitz.open(path)
    return "".join(page.get_text() for page in doc)


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.I)


def guess_doi(text: str) -> str | None:
    """从 PDF 正文（前部）里抠出 DOI，用于把 ingest 的手动 PDF 匹配回待取清单。"""
    m = DOI_RE.search(text[:4000])
    if m:
        return m.group(0).rstrip(".").lower()
    return None
