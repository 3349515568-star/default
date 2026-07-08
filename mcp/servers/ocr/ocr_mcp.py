"""
本地 OCR 的最小 MCP 服务(纯离线、不烧 token)。
引擎: RapidOCR(PP-OCRv4 ONNX, 中英文印刷体), PDF 走 PyMuPDF 栅格化后再 OCR。
依赖: py -m pip install rapidocr onnxruntime pymupdf
模型随 rapidocr 包内置, 运行时零联网。
注意: 手写/数学公式/日文假名等本地 OCR 偏弱, 这类场景建议回退到 doubao-vision(云端 VLM)。
"""
import os
import logging
import pathlib
import urllib.request

# 静音 RapidOCR/onnxruntime 的 INFO 日志, 保持 stdio 协议干净
for _name in ("RapidOCR", "onnxruntime"):
    logging.getLogger(_name).setLevel(logging.ERROR)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ocr")

_engine = None


def _get_engine():
    """惰性初始化, 模型只加载一次。"""
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    return _engine


def _load_image(src: str):
    """接受本地路径或 http(s) URL, 返回 RapidOCR 能吃的输入(路径字符串或 BGR ndarray)。"""
    if src.startswith(("http://", "https://")):
        data = urllib.request.urlopen(src, timeout=30).read()
        import numpy as np
        import cv2
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"无法解码 URL 图片: {src}")
        return img
    p = pathlib.Path(os.path.expanduser(src))
    if not p.exists():
        raise FileNotFoundError(f"找不到图片: {p}")
    return str(p.resolve())


def _format(res, with_confidence: bool) -> str:
    txts = getattr(res, "txts", None) or ()
    if not txts:
        return "(未识别到文字)"
    if not with_confidence:
        return "\n".join(txts)
    scores = getattr(res, "scores", None) or ()
    lines = []
    for i, t in enumerate(txts):
        s = scores[i] if i < len(scores) else None
        lines.append(f"{t}\t[{s:.2f}]" if s is not None else t)
    return "\n".join(lines)


def _parse_pages(spec: str, total: int):
    """'1-3,5' -> [0,1,2,4](0 基, 越界丢弃, 去重保序); 留空=全部。"""
    spec = (spec or "").strip()
    if not spec:
        return list(range(total))
    out, seen = [], set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            rng = range(int(a), int(b) + 1)
        else:
            rng = [int(part)]
        for i in rng:
            j = i - 1
            if 0 <= j < total and j not in seen:
                seen.add(j)
                out.append(j)
    return out


@mcp.tool()
def ocr_image(image_path: str, with_confidence: bool = False) -> str:
    """识别单张图片里的文字(本地离线, 中英文印刷体效果最佳)。

    image_path: 本地图片路径, 或 http(s) 图片 URL。
    with_confidence: 为 True 时每行附带置信度, 方便判断可信度。
    手写 / 数学公式 / 日文假名 等识别不理想时, 改用 doubao-vision。
    """
    res = _get_engine()(_load_image(image_path))
    return _format(res, with_confidence)


@mcp.tool()
def ocr_pdf(pdf_path: str, pages: str = "", dpi: int = 200) -> str:
    """对(扫描 / 图片型)PDF 逐页 OCR。

    pages: 形如 '1-3,5'(从 1 开始, 留空=全部)。
    dpi:   栅格化分辨率, 默认 200; 字小可调到 300。
    先用 PyMuPDF 把页面渲染成图再 OCR, 适合没有文字层的扫描件。
    (有文字层的 PDF 直接复制文本即可, 不必走这里。)
    """
    import fitz  # PyMuPDF
    import numpy as np
    import cv2

    p = pathlib.Path(os.path.expanduser(pdf_path))
    if not p.exists():
        raise FileNotFoundError(f"找不到 PDF: {p}")

    doc = fitz.open(str(p))
    try:
        want = _parse_pages(pages, doc.page_count)
        engine = _get_engine()
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        out = []
        for idx in want:
            pix = doc.load_page(idx).get_pixmap(matrix=mat)
            img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
            txt = "\n".join(getattr(engine(img), "txts", None) or ()) or "(本页无文字)"
            out.append(f"===== 第 {idx + 1} 页 =====\n{txt}")
    finally:
        doc.close()
    return "\n\n".join(out) if out else "(无内容)"


if __name__ == "__main__":
    mcp.run()
