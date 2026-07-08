"""引用管理：DOI 验证、BibTeX 生成、引用格式化。

用法：
    from citations import verify_doi, doi_to_bibtex, validate_bibtex_file
    ok, meta = verify_doi("10.1038/s41586-021-03819-2")
    bibtex = doi_to_bibtex("10.1038/s41586-021-03819-2", key="Smith2021")
"""
import json
import re
import requests
from pathlib import Path

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
HEADERS = {
    "User-Agent": "lit-agent (mailto:researcher@example.com)",
    "Accept": "application/json",
}


# ── DOI 验证 ───────────────────────────────────────────
def verify_doi(doi: str) -> tuple[bool, dict | None]:
    """验证 DOI 是否有效并拿回 CrossRef 元数据。返回 (ok, metadata_dict)。"""
    doi = (doi or "").strip().lower()
    if not doi:
        return False, None
    try:
        r = requests.get(CROSSREF_URL.format(doi=doi), headers=HEADERS, timeout=20)
        if r.status_code == 404:
            return False, None
        r.raise_for_status()
        data = r.json()
        msg = data.get("message", {})
        return True, msg
    except Exception:
        return False, None


# ── CrossRef 元数据 → BibTeX ────────────────────────────
def _crossref_to_bibtex(msg: dict, doi: str, key: str | None = None) -> str:
    """把 CrossRef 'message' 块转成 BibTeX @article 条目。"""
    # 作者
    authors = []
    for a in msg.get("author", []):
        family = a.get("family", "")
        given = a.get("given", "")
        if family:
            authors.append(f"{family}, {given}".strip())
    author_str = " and ".join(authors) if authors else "{{{no author}}}"

    # 标题
    title = (msg.get("title") or [""])[0]
    title = title.replace("{", "").replace("}", "")

    # 期刊
    journal = ""
    short_titles = msg.get("short-container-title") or []
    if short_titles:
        journal = short_titles[0]
    elif msg.get("container-title"):
        journal = (msg["container-title"] or [""])[0]

    # 年份
    year = ""
    pub_parts = msg.get("published-print", {}) or msg.get("published-online", {})
    date_parts = pub_parts.get("date-parts", [[None]])[0]
    if date_parts and date_parts[0]:
        year = str(date_parts[0])

    # 卷 / 期 / 页
    volume = msg.get("volume", "")
    issue = msg.get("issue", "")
    page = msg.get("page", "")

    # citation key
    if not key:
        first_author = (authors[0].split(",")[0].strip()) if authors else "Unknown"
        key = f"{first_author}{year}"
    key = re.sub(r"[^a-zA-Z0-9]", "", key)

    lines = [f"@article{{{key},"]
    lines.append(f"  author = {{{author_str}}},")
    lines.append(f"  title = {{{title}}},")
    if journal:
        lines.append(f"  journal = {{{journal}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if volume:
        lines.append(f"  volume = {{{volume}}},")
    if issue:
        lines.append(f"  number = {{{issue}}},")
    if page:
        lines.append(f"  pages = {{{page}}},")
    lines.append(f"  doi = {{{doi}}},")
    lines.append("}")

    return "\n".join(lines)


def doi_to_bibtex(doi: str, key: str | None = None) -> str | None:
    """按 DOI 从 CrossRef 拿元数据，生成 BibTeX 条目。失败返回 None。"""
    ok, msg = verify_doi(doi)
    if not ok or not msg:
        return None
    return _crossref_to_bibtex(msg, doi, key)


# ── 批量验证 ───────────────────────────────────────────
def validate_dois(dois: list[str]) -> dict:
    """批量验证 DOI 列表，返回 {doi: {ok, title, journal, year}} 的结果。"""
    results = {}
    for doi in dois:
        ok, msg = verify_doi(doi)
        results[doi] = {
            "ok": ok,
            "title": (msg.get("title", [""])[0] if msg else ""),
            "journal": ((msg.get("container-title", [""])[0] if msg else "")),
            "year": _extract_year(msg) if msg else "",
        }
    return results


def _extract_year(msg: dict) -> str:
    pub_parts = msg.get("published-print", {}) or msg.get("published-online", {})
    date_parts = pub_parts.get("date-parts", [[None]])[0]
    if date_parts and date_parts[0]:
        return str(date_parts[0])
    return ""


# ── BibTeX 文件工具 ─────────────────────────────────────
def read_bibtex_entries(path: str | Path) -> list[dict]:
    """解析 BibTeX 文件，返回条目列表 [{key, type, fields: {}}]."""
    text = Path(path).read_text(encoding="utf-8")
    entries = []
    pattern = re.compile(
        r"@(\w+)\s*\{([^,]+),\s*(.+?)\}", re.DOTALL
    )
    for m in pattern.finditer(text):
        etype = m.group(1).lower()
        ekey = m.group(2).strip()
        body = m.group(3)
        fields = {}
        field_pattern = re.compile(
            r"(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}"
        )
        for fm in field_pattern.finditer(body):
            fields[fm.group(1).lower()] = fm.group(2)
        entries.append({"key": ekey, "type": etype, "fields": fields})
    return entries


def validate_bibtex_file(path: str | Path) -> list[dict]:
    """检查 BibTeX 文件里每条 DOI 是否有效。返回问题列表。"""
    entries = read_bibtex_entries(path)
    issues = []
    for e in entries:
        doi = e["fields"].get("doi", "")
        if doi:
            ok, _ = verify_doi(doi)
            if not ok:
                issues.append({
                    "key": e["key"],
                    "issue": "broken_doi",
                    "doi": doi,
                    "severity": "high",
                })
        # 检查必要字段
        required = {"article": ["author", "title", "journal", "year"]}
        for field in required.get(e["type"], []):
            if not e["fields"].get(field):
                issues.append({
                    "key": e["key"],
                    "issue": f"missing_{field}",
                    "severity": "medium",
                })
    return issues


def export_bibtex_from_records(records: list[dict], output_path: str | Path) -> int:
    """把 lit-agent 的标准 rec dict 列表导出为 .bib 文件。返回成功条数。"""
    entries = []
    for i, rec in enumerate(records):
        doi = rec.get("doi", "")
        authors = rec.get("authors", "")
        # EPMC 作者格式为 "LastName IN, ..."，取第一个姓做 citation key
        first_author = authors.split(",")[0].strip()
        first = first_author.split(" ")[0] if first_author else f"Ref{i}"
        year = str(rec.get("year", ""))
        key = re.sub(r"[^a-zA-Z0-9]", "", f"{first}{year}") or f"ref{i}"

        lines = [f"@article{{{key},"]
        if authors:
            bib_authors = " and ".join(
                f"{a.strip().rsplit(' ', 1)[0]}, {a.strip().rsplit(' ', 1)[-1]}"
                for a in authors.split(",")[:10] if a.strip()
            )
            lines.append(f"  author = {{{bib_authors}}},")
        lines.append(f"  title = {{{rec.get('title', '').rstrip('.')}}},")
        if rec.get("journal"):
            lines.append(f"  journal = {{{rec['journal']}}},")
        if year:
            lines.append(f"  year = {{{year}}},")
        if doi:
            lines.append(f"  doi = {{{doi}}},")
        if rec.get("pmid"):
            lines.append(f"  note = {{PMID: {rec['pmid']}}},")
        lines.append("}")
        entries.append("\n".join(lines))

    Path(output_path).write_text("\n\n".join(entries) + "\n", encoding="utf-8")
    return len(entries)


# ── 引用格式转换 ────────────────────────────────────────
def format_citation(rec: dict, style: str = "vancouver") -> str:
    """把单篇 rec 转成指定引用格式的纯文本。支持 vancouver / apa / nature。"""
    authors = (rec.get("authors") or "").split(",")
    if style == "vancouver":
        # 作者名缩写 + 标题 + 期刊缩写 + 年;卷(期):页
        names = ", ".join(
            f"{a.strip().rsplit(' ', 1)[0]} {''.join(c for c in a.strip().rsplit(' ', 1)[-1] if c.isupper())}"
            for a in authors[:6] if a.strip()
        )
        if len(authors) > 6:
            names += ", et al"
        parts = [names + "."]
        title = rec.get("title", "").rstrip(".")
        parts.append(f" {title}.")
        journal = rec.get("journal", "")
        if journal:
            parts.append(f" {journal}.")
        year = rec.get("year", "")
        if year:
            parts.append(f" {year}")
        doi = rec.get("doi", "")
        if doi:
            parts.append(f". DOI: {doi}")
        return "".join(parts)

    elif style == "apa":
        names = ", ".join(
            f"{a.strip().rsplit(' ', 1)[0]}, {''.join(c+'. ' for c in a.strip().rsplit(' ', 1)[-1] if c).strip()}"
            for a in authors[:7] if a.strip()
        )
        if len(authors) > 7:
            names += ", ..."
        year = f"({rec.get('year', '')})"
        title = rec.get("title", "").rstrip(".")
        journal = rec.get("journal", "")
        doi = rec.get("doi", "")
        doi_str = f" https://doi.org/{doi}" if doi else ""
        return f"{names} {year}. {title}. {journal}.{doi_str}"

    else:  # nature
        names = ", ".join(
            f"{a.strip().rsplit(' ', 1)[0]}, {''.join(c+'.' for c in a.strip().rsplit(' ', 1)[-1] if c.isupper())}"
            for a in authors[:5] if a.strip()
        )
        if len(authors) > 5:
            names += " et al."
        year = rec.get("year", "")
        title = rec.get("title", "").rstrip(".")
        journal = rec.get("journal", "")
        doi = rec.get("doi", "")
        doi_str = f" https://doi.org/{doi}" if doi else ""
        return f"{names} {title}. {journal} ({year}).{doi_str}"
