"""PDF 字节校验：统一判断下载到的内容是不是真正的全文 PDF。

供 ``fulltext`` / ``elsevier_api`` / ``browser_fetch`` 共用的独立模块，
避免下载层与出版商 API 层之间互相 import。校验规则：

- 必须以 ``%PDF-`` 开头，且体积大于 10KB（更小的多半是占位或错误页）。
- 必须能被 PyMuPDF 打开且至少有 1 页。
- 单页文档命中预览关键词时拒绝；单页真论文（letter 类）不含这些
  关键词，不受影响。
"""
from __future__ import annotations

MIN_PDF_BYTES = 10_000

_PREVIEW_HINTS = (
    "document preview", "preview only", "purchase pdf", "access through your institution",
)


def validate_pdf_bytes(body: bytes) -> bytes | None:
    """校验通过时原样返回 PDF 字节，否则返回 None。"""
    if not body or not body.startswith(b"%PDF-") or len(body) <= MIN_PDF_BYTES:
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
