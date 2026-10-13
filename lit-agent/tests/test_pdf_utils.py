import unittest

import fitz

import pdf_utils


def _filler(n: int) -> str:
    """生成 n 行低重复度文本，保证生成的 PDF 明显大于 10KB 下限。"""
    return "\n".join(
        f"result {i:04d}: berberine bridge enzyme catalysis trial {i * 37 % 991} "
        f"yield {i * 13 % 997} micromolar concentration batch {i * 7 % 89}"
        for i in range(n))


def _make_pdf(page_texts, deflate: bool = False) -> bytes:
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=595, height=2000)
        y = 30
        for line in text.splitlines():
            page.insert_text((40, y), line, fontsize=10)
            y += 14
    data = doc.tobytes(deflate=deflate)
    doc.close()
    return data


class ValidatePdfBytesTests(unittest.TestCase):
    def test_normal_multi_page_pdf_passes(self):
        body = _make_pdf([_filler(120)] * 3)
        self.assertGreater(len(body), pdf_utils.MIN_PDF_BYTES)
        self.assertEqual(pdf_utils.validate_pdf_bytes(body), body)

    def test_single_page_with_preview_hint_is_rejected(self):
        body = _make_pdf([_filler(130) + "\nTo continue, purchase pdf access."])
        self.assertGreater(len(body), pdf_utils.MIN_PDF_BYTES)
        self.assertIsNone(pdf_utils.validate_pdf_bytes(body))

    def test_single_page_real_paper_is_not_rejected(self):
        # letter 类单页真论文不含预览关键词，不能被误杀
        body = _make_pdf([_filler(130)])
        self.assertGreater(len(body), pdf_utils.MIN_PDF_BYTES)
        self.assertEqual(pdf_utils.validate_pdf_bytes(body), body)

    def test_tiny_pdf_below_size_floor_is_rejected(self):
        body = _make_pdf(["hello"])
        self.assertLessEqual(len(body), pdf_utils.MIN_PDF_BYTES)
        self.assertIsNone(pdf_utils.validate_pdf_bytes(body))

    def test_html_bytes_are_rejected(self):
        body = b"<html><body>error page</body></html>" + b"x" * 20_000
        self.assertIsNone(pdf_utils.validate_pdf_bytes(body))

    def test_corrupt_pdf_bytes_are_rejected(self):
        body = b"%PDF-" + b"x" * 20_000
        self.assertIsNone(pdf_utils.validate_pdf_bytes(body))

    def test_empty_bytes_are_rejected(self):
        self.assertIsNone(pdf_utils.validate_pdf_bytes(b""))


if __name__ == "__main__":
    unittest.main()
