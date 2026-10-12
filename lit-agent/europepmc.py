"""Europe PMC 检索：拿到候选文献的元数据（标题/摘要/引用数/DOI/PMCID/期刊/年份）。"""
import html
import math
import re
import requests

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
SEARCH_POST_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/searchPOST"
HEADERS = {"User-Agent": "lit-agent (research tool)"}


def _plain_title(value: str | None) -> str:
    """清除 Europe PMC 标题里的实体转义和轻量 HTML 标签。"""
    return re.sub(r"<[^>]+>", "", html.unescape(value or "")).strip().rstrip(".")


def _parse(result: dict) -> dict:
    journal_data = result.get("journalInfo", {}).get("journal", {}) or {}
    book_data = result.get("bookOrReportDetails", {}) or {}
    journal = (
        journal_data.get("title")
        or book_data.get("publisher")
        or ""
    )
    publisher = (
        journal_data.get("publisher")
        or book_data.get("publisher")
        or result.get("publisher")
        or ""
    )
    try:
        year = int(result.get("pubYear")) if result.get("pubYear") else None
    except (TypeError, ValueError):
        year = None
    return {
        "source": result.get("source"),
        "id": result.get("id"),
        "pmid": result.get("pmid"),
        "pmcid": result.get("pmcid"),
        "doi": (result.get("doi") or "").lower(),
        "title": _plain_title(result.get("title")),
        "abstract": result.get("abstractText", ""),
        "journal": journal,
        "publisher": publisher,
        "year": year,
        "citations": result.get("citedByCount", 0) or 0,
        "authors": result.get("authorString", ""),
        "has_pdf": result.get("hasPDF") == "Y",
        "is_oa": result.get("isOpenAccess") == "Y",
    }


def _query(query: str, page_size: int, sort: str | None) -> list[dict]:
    params = {
        "query": query,
        "format": "json",
        "pageSize": page_size,
        "resultType": "core",
    }
    if sort:
        params["sort"] = sort
    r = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=40)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        # Europe PMC 部分边缘节点会让 GET /search 返回 404；官方
        # searchPOST 接口接受同一组表单参数，可安全回退且不改变查询语义。
        if r.status_code not in {404, 405}:
            raise
        r = requests.post(
            SEARCH_POST_URL,
            data=params,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=40,
        )
        r.raise_for_status()
    results = r.json().get("resultList", {}).get("result", [])
    return [_parse(x) for x in results]


def search(query: str, candidates: int = 30, min_year: int | None = None,
           keep_classics: bool = False) -> list[dict]:
    """两路检索合并：按相关度一批 + 按引用数一批，去重。

    这样既能拿到“相关度高”的近作，也能拿到“引用多”的经典文献。
    只保留有摘要的（没摘要没法打分/总结）。

    keep_classics=True 时，按引用数排序那一路不做年份过滤——这样近 5 年的新作走
    相关度路（受 min_year 限制），而更早的代表性高引文献仍能通过引用路保留下来。
    """
    seen, merged = set(), []
    # (排序方式, 该路是否按 min_year 过滤)
    passes = [(None, min_year),
              ("CITED desc", None if keep_classics else min_year)]
    for sort, yr in passes:
        for rec in _query(query, candidates, sort):
            if not rec["abstract"]:
                continue
            if yr and rec["year"] and rec["year"] < yr:
                continue
            key = rec["doi"] or rec["pmid"] or rec["pmcid"] or rec["title"]
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(rec)
    return merged


def fetch_record(doi: str | None = None, pmid: str | None = None,
                 pmcid: str | None = None) -> dict | None:
    """按 ID 取单篇最新元数据（含摘要），用于 re-summarize 时重新拉摘要。"""
    if doi:
        q = f'DOI:"{doi}"'
    elif pmid:
        q = f"EXT_ID:{pmid} AND SRC:MED"
    elif pmcid:
        q = f"PMCID:{pmcid}"
    else:
        return None
    res = _query(q, 1, None)
    return res[0] if res else None


def prelim_score(rec: dict, w_citation: float, w_recency: float,
                 newest_year: int, w_influential: float = 0.0) -> float:
    """不含 LLM 相关度的元数据加分（引用 + 高影响引用 + 新近度）。"""
    cite = w_citation * math.log10((rec["citations"] or 0) + 1)
    influential = w_influential * math.log10(
        (rec.get("influential_citations") or 0) + 1)
    recency = 0.0
    if rec["year"]:
        recency = w_recency * max(0, 1 - (newest_year - rec["year"]) / 15)
    return cite + influential + recency
