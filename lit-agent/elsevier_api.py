"""Server-side Elsevier Article Retrieval API client.

Institution tokens are deliberately kept out of browser contexts.  Elsevier's
official authentication guidance requires X-ELS-Insttoken to remain server-side.
"""
from __future__ import annotations

from urllib.parse import quote

import requests


ARTICLE_BY_DOI = "https://api.elsevier.com/content/article/doi/{doi}"
_api_key = ""
_inst_token = ""


def configure(api_key: str = "", inst_token: str = ""):
    global _api_key, _inst_token
    _api_key = (api_key or "").strip()
    _inst_token = (inst_token or "").strip()


def configured() -> bool:
    return bool(_api_key)


def fetch_pdf(doi: str, timeout_seconds: int = 60) -> bytes | None:
    """Retrieve an entitled article PDF without exposing API credentials."""
    doi = (doi or "").strip()
    if not doi or not _api_key:
        return None
    headers = {
        "Accept": "application/pdf",
        "X-ELS-APIKey": _api_key,
    }
    if _inst_token:
        headers["X-ELS-Insttoken"] = _inst_token
    try:
        response = requests.get(
            ARTICLE_BY_DOI.format(doi=quote(doi, safe="")),
            headers=headers,
            params={"httpAccept": "application/pdf", "amsRedirect": "true"},
            timeout=timeout_seconds,
            allow_redirects=True,
        )
    except Exception:
        return None
    if response.status_code == 200 and response.content[:4] == b"%PDF":
        return response.content
    return None
