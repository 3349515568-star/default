"""Elsevier 官方 API：结构化 XML 全文与授权 PDF 下载。

优先使用 Article Retrieval API 的官方 PDF 表示；若返回的不是有效全文 PDF，
再按 Object Retrieval API 的 META 附件清单选择明确标记的 PDF。所有凭据仅放在请求头。
仅对 ``10.1016/`` 前缀的 DOI 有效；其他出版商返回 None。

用法：
    from elsevier_api import fetch_pdf, fetch_fulltext
    pdf = fetch_pdf("10.1016/j.jep.2026.121350", api_key="xxx")
    xml = fetch_fulltext("10.1016/j.jep.2026.121350", api_key="xxx")
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

ELSEVIER_API = "https://api.elsevier.com/content"
_SUPPLEMENT_HINTS = ("supplement", "supplementary", "mmc", "appendix")
_MAIN_HINTS = ("main", "web-pdf", "full-text", "fulltext", "article")
_PREVIEW_HINTS = (
    "document preview", "preview only", "purchase pdf", "access through your institution",
)
_API_KEY = ""
_INST_TOKEN = ""


def configure(api_key: str = "", inst_token: str = ""):
    global _API_KEY, _INST_TOKEN
    _API_KEY = (api_key or "").strip()
    _INST_TOKEN = (inst_token or "").strip()


def configured() -> bool:
    return bool(_API_KEY)


# ── public API ──────────────────────────────────────────────

def fetch_pdf(doi: str, timeout_seconds: int = 60, *,
              api_key: str = "", inst_token: str = "") -> bytes | None:
    """下载授权 PDF：Article PDF → 附件 META → 精确 PDF EID。

    不根据字符串臆造 EID；只使用 Elsevier 元数据明确返回的 PDF 附件。
    """
    api_key = api_key or _API_KEY
    inst_token = inst_token or _INST_TOKEN
    doi = (doi or "").strip()
    if not api_key or not doi.lower().startswith("10.1016/"):
        return None
    pdf = _article_pdf(doi, api_key, inst_token, timeout_seconds)
    if pdf:
        return pdf
    metadata = _object_metadata(doi, api_key, inst_token, timeout_seconds)
    if metadata is not None and metadata.status_code == 200:
        for attachment in _pdf_attachments(metadata.text):
            pdf = _object_pdf(
                attachment["eid"], api_key, inst_token, timeout_seconds)
            if pdf:
                return pdf
    return None


def fetch_fulltext(doi: str, timeout_seconds: int = 60, *,
                   api_key: str = "", inst_token: str = "") -> dict | None:
    """获取 XML 全文，解析为结构化 dict。

    Returns: {"title","authors","abstract","full_text","figures","references","pdf_eids"}
    """
    api_key = api_key or _API_KEY
    inst_token = inst_token or _INST_TOKEN
    doi = (doi or "").strip()
    if not api_key or not doi.lower().startswith("10.1016/"):
        return None
    resp = _article_xml(doi, api_key, inst_token, timeout_seconds)
    if resp is None or resp.status_code != 200:
        return None
    parsed = _parse_xml(resp.text)
    if parsed is None:
        return None
    metadata = _object_metadata(doi, api_key, inst_token, timeout_seconds)
    parsed["pdf_eids"] = (
        [item["eid"] for item in _pdf_attachments(metadata.text)]
        if metadata is not None and metadata.status_code == 200 else [])
    return parsed


# ── HTTP layer ──────────────────────────────────────────────

def _article_xml(doi: str, api_key: str, inst_token: str,
                 timeout_seconds: int = 30) -> requests.Response | None:
    url = f"{ELSEVIER_API}/article/doi/{quote(doi, safe='')}"
    headers = _headers(api_key, inst_token, "text/xml")
    try:
        return requests.get(
            url, headers=headers,
            params={"view": "FULL", "httpAccept": "text/xml"},
            timeout=timeout_seconds)
    except requests.RequestException:
        return None


def _object_metadata(doi: str, api_key: str,
                     inst_token: str,
                     timeout_seconds: int = 30) -> requests.Response | None:
    url = f"{ELSEVIER_API}/object/doi/{quote(doi, safe='')}"
    headers = _headers(api_key, inst_token, "text/xml")
    try:
        return requests.get(
            url, headers=headers,
            params={"view": "META", "httpAccept": "text/xml"},
            timeout=timeout_seconds)
    except requests.RequestException:
        return None


def _article_pdf(doi: str, api_key: str, inst_token: str,
                 timeout_seconds: int = 45) -> bytes | None:
    url = f"{ELSEVIER_API}/article/doi/{quote(doi, safe='')}"
    headers = _headers(api_key, inst_token, "application/pdf")
    try:
        response = requests.get(
            url, headers=headers,
            params={"httpAccept": "application/pdf", "amsRedirect": "true"},
            timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException:
        return None
    return _validated_pdf(response)


def _headers(api_key: str, inst_token: str, accept: str) -> dict[str, str]:
    headers = {"X-ELS-APIKey": api_key, "Accept": accept}
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    return headers


def _object_pdf(eid: str, api_key: str, inst_token: str,
                timeout_seconds: int = 45) -> bytes | None:
    url = f"{ELSEVIER_API}/object/eid/{quote(eid, safe='')}"
    headers = _headers(api_key, inst_token, "application/pdf")
    try:
        response = requests.get(url, headers=headers, timeout=timeout_seconds)
    except requests.RequestException:
        return None
    return _validated_pdf(response)


def _validated_pdf(response: requests.Response) -> bytes | None:
    if response.status_code != 200:
        return None
    body = response.content
    if not body.startswith(b"%PDF-") or len(body) <= 10_000:
        return None
    try:
        import fitz
        with fitz.open(stream=body, filetype="pdf") as doc:
            if doc.page_count < 1:
                return None
            if doc.page_count == 1:
                text = doc[0].get_text().lower()
                if any(hint in text for hint in _PREVIEW_HINTS):
                    return None
    except Exception:
        return None
    return body


# ── PDF attachment selection ────────────────────────────────

def _pdf_attachments(xml_text: str) -> list[dict[str, str]]:
    """只选择 META 中明确标记为 PDF 的附件，主文优先、补充材料排除。"""
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, TypeError):
        return []
    candidates: list[tuple[int, dict[str, str]]] = []
    for attachment in root.iter():
        if _local_name(attachment.tag) != "attachment":
            continue
        fields = _child_fields(attachment)
        eid = fields.get("eid", "").strip()
        filename = fields.get("filename", "").strip()
        mimetype = fields.get("mimetype", "").strip().lower()
        kind = fields.get("type", "").strip()
        context = f"{eid} {filename} {mimetype} {kind}".lower()
        is_pdf = (mimetype == "application/pdf" or filename.lower().endswith(".pdf")
                  or eid.lower().endswith(".pdf"))
        if not eid or not is_pdf or any(h in context for h in _SUPPLEMENT_HINTS):
            continue
        score = 100 if eid.lower().endswith("-main.pdf") else 20
        score += sum(10 for hint in _MAIN_HINTS if hint in context)
        candidates.append((score, {
            "eid": eid, "filename": filename, "mimetype": mimetype, "type": kind,
        }))
    candidates.sort(key=lambda item: item[0], reverse=True)
    seen, result = set(), []
    for _, item in candidates:
        key = item["eid"].lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _child_fields(element) -> dict[str, str]:
    fields = {}
    for child in element.iter():
        local = _local_name(child.tag)
        if local in {"eid", "filename", "mimetype", "type", "url"}:
            value = " ".join(_collect_text(child).split())
            if value and local not in fields:
                fields[local] = value
    return fields


def _local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


# ── XML parsing ─────────────────────────────────────────────

def _parse_xml(xml_text: str) -> dict | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    return {
        "title": _find_text(root, ("title",)),
        "authors": _extract_authors(root),
        "abstract": _extract_abstract(root),
        "full_text": _extract_body(root),
        "references": _extract_references(root),
        "figures": _extract_figures(root),
    }


def _find_text(el, tags: tuple[str, ...]) -> str:
    for child in el.iter():
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local in tags:
            return (child.text or "").strip()
    return ""


def _find_collected_text(el, tags: tuple[str, ...]) -> str:
    for child in el.iter():
        if _local_name(child.tag) in tags:
            text = " ".join(_collect_text(child).split())
            if text:
                return text
    return ""


def _extract_authors(root) -> list[str]:
    # structured author
    out = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == "author":
            given = surname = ""
            for child in el:
                cl = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if "given" in cl:
                    given = (child.text or "").strip()
                elif "surname" in cl or "last" in cl:
                    surname = (child.text or "").strip()
            if given or surname:
                out.append(f"{given} {surname}".strip())
    if not out:
        # fallback: dc:creator
        for el in root.iter():
            local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if local in ("creator",):
                if el.text and el.text.strip():
                    out.append(el.text.strip())
    return out


def _extract_abstract(root) -> str:
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local in ("abstract", "description"):
            text = _collect_text(el).strip()
            if text:
                return text
    return ""


def _extract_body(root) -> str:
    # find <body> element
    body = None
    for el in root.iter():
        if el.tag.split("}")[-1] == "body":
            body = el
            break
    if body is None:
        return ""

    parts = []
    for section in _find_sections(body):
        heading, paras = "", []
        for child in section:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag in ("section-title", "sectiontitle", "heading"):
                heading = _collect_text(child).strip()
            elif tag == "para":
                text = _collect_text(child).strip()
                if text:
                    paras.append(text)
        if heading and paras:
            parts.append(f"## {heading}\n\n{' '.join(paras)}")
        elif paras:
            parts.append(" ".join(paras))

    if not parts:
        for el in body.iter():
            if el.tag.split("}")[-1] == "para":
                t = _collect_text(el).strip()
                if t:
                    parts.append(t)
    return "\n\n".join(parts)


def _find_sections(el):
    if el.tag.split("}")[-1] == "section":
        yield el
    for child in el:
        yield from _find_sections(child)


def _extract_references(root) -> list[str]:
    refs, seen = [], set()
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local in ("bib-reference", "reference"):
            text = " ".join(_collect_text(el).split())
            key = text.casefold()
            if text and len(text) > 10 and key not in seen:
                seen.add(key)
                refs.append(text)
    return refs


def _extract_figures(root) -> list[dict[str, str]]:
    figures = []
    for el in root.iter():
        if _local_name(el.tag) not in ("figure", "fig"):
            continue
        fields = _child_fields(el)
        label = fields.get("label", "") or _find_text(el, ("label",))
        caption = _find_collected_text(el, ("caption", "simple-para", "para"))
        eid = fields.get("eid", "")
        if label or caption or eid:
            figures.append({"label": label, "caption": caption, "eid": eid})
    return figures


def _collect_text(el) -> str:
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_collect_text(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join(parts)
