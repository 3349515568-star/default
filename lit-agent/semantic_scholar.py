"""Semantic Scholar Academic Graph 检索适配器。"""
import re
import time

import requests

SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = (
    "paperId,title,abstract,year,authors,venue,externalIds,url,"
    "citationCount,influentialCitationCount,isOpenAccess,openAccessPdf"
)
_api_key = ""


def init(api_key: str = ""):
    """S2_API_KEY 可选；无 key 时使用官方的匿名共享额度。"""
    global _api_key
    _api_key = (api_key or "").strip()


def normalize_query(query: str) -> str:
    """把 Europe PMC/PubMed 布尔检索式转为 S2 relevance search 的纯文本查询。"""
    text = re.sub(r"\b(?:AND|OR|NOT)\b", " ", query, flags=re.I)
    text = re.sub(r"\[[^\]]+\]", " ", text)  # PubMed 字段限定，如 [Title/Abstract]
    text = re.sub(r"[(){}\[\]\"']", " ", text)
    text = re.sub(r"[-‐-―]", " ", text)       # S2 官方提醒连字号词会无匹配
    return re.sub(r"\s+", " ", text).strip()


def _parse(item: dict) -> dict:
    external = item.get("externalIds") or {}
    open_pdf = item.get("openAccessPdf") or {}
    pmcid = str(external.get("PubMedCentral") or "")
    if pmcid and not pmcid.upper().startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    authors = ", ".join(
        a.get("name", "") for a in (item.get("authors") or []) if a.get("name")
    )
    return {
        "source": "s2",
        "id": item.get("paperId"),
        "s2_paper_id": item.get("paperId"),
        "s2_url": item.get("url") or "",
        "s2_pdf_url": open_pdf.get("url") or "",
        "pmid": str(external.get("PubMed") or ""),
        "pmcid": pmcid,
        "doi": str(external.get("DOI") or "").lower(),
        "title": (item.get("title") or "").rstrip("."),
        "abstract": item.get("abstract") or "",
        "journal": item.get("venue") or "",
        "publisher": item.get("publisher") or "",
        "year": item.get("year"),
        "citations": item.get("citationCount") or 0,
        "influential_citations": item.get("influentialCitationCount") or 0,
        "authors": authors,
        "has_pdf": bool(open_pdf.get("url")),
        "is_oa": bool(item.get("isOpenAccess") or open_pdf.get("url")),
    }


def search(query: str, max_results: int = 30,
           min_year: int | None = None) -> list[dict]:
    """按 S2 relevance search 取一批带摘要和引用指标的候选文献。"""
    plain_query = normalize_query(query)
    if not plain_query:
        return []
    params = {
        "query": plain_query,
        "limit": max(1, min(int(max_results), 100)),
        "fields": FIELDS,
    }
    if min_year:
        params["year"] = f"{int(min_year)}-"
    headers = {"User-Agent": "lit-agent/1.0 (academic literature tool)"}
    if _api_key:
        headers["x-api-key"] = _api_key

    for attempt in range(3):
        try:
            response = requests.get(
                SEARCH_URL, params=params, headers=headers, timeout=40)
        except requests.RequestException as e:
            if attempt == 2:
                print(f"  ⚠ Semantic Scholar 检索失败：{type(e).__name__}")
                return []
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 2:
                print(f"  ⚠ Semantic Scholar 暂时不可用（HTTP {response.status_code}）")
                return []
            retry_after = response.headers.get("Retry-After", "")
            try:
                wait = float(retry_after)
            except (TypeError, ValueError):
                wait = 2 * (attempt + 1)
            if wait > 60:
                print("  ⚠ S2 限流，建议配 S2_API_KEY")
                return []
            time.sleep(min(max(wait, 0.5), 60))
            continue
        try:
            response.raise_for_status()
            data = response.json().get("data", [])
        except (requests.RequestException, ValueError) as e:
            print(f"  ⚠ Semantic Scholar 响应无效：{type(e).__name__}")
            return []
        return [rec for rec in (_parse(item) for item in data)
                if rec["title"] and rec["abstract"]]
    return []
