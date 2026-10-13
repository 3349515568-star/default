import unittest
from io import BytesIO

from Bio import Entrez

import ncbi

# 走 Entrez.read 真实解析，避免手写 dict fixture 与 Biopython 实际返回
# （StringElement，属性在 .attributes）脱节。
PUBMED_XML = b"""<?xml version="1.0"?>
<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation>
    <PMID Version="1">12345678</PMID>
    <Article>
      <Journal>
        <Title>Journal of Tests</Title>
        <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>A test article.</ArticleTitle>
      <Abstract>
        <AbstractText Label="BACKGROUND">First part.</AbstractText>
        <AbstractText Label="RESULTS">Second part.</AbstractText>
      </Abstract>
      <ELocationID EIdType="doi" ValidYN="Y">10.1000/XYZ.Test</ELocationID>
      <AuthorList>
        <Author><LastName>Wang</LastName><ForeName>Yi</ForeName></Author>
      </AuthorList>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
      </PublicationTypeList>
      <DataBankList>
        <DataBank><DataBankName>GENBANK</DataBankName>
          <AccessionNumberList><AccessionNumber>ABC123</AccessionNumber></AccessionNumberList>
        </DataBank>
      </DataBankList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">12345678</ArticleId>
      <ArticleId IdType="pmc">PMC7654321</ArticleId>
      <ArticleId IdType="doi">10.1000/XYZ.Test</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
</PubmedArticleSet>
"""

PUBMED_XML_NO_PMC = b"""<?xml version="1.0"?>
<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation>
    <PMID Version="1">99999999</PMID>
    <Article>
      <Journal>
        <Title>Journal of Tests</Title>
        <JournalIssue><PubDate><MedlineDate>2023 Jan-Feb</MedlineDate></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>No identifiers here.</ArticleTitle>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">99999999</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
</PubmedArticleSet>
"""


def _parse(xml: bytes) -> dict:
    parsed = Entrez.read(BytesIO(xml))
    return ncbi._parse_pubmed_article(parsed["PubmedArticle"][0])


class PubmedParseTests(unittest.TestCase):
    def test_pmcid_from_real_entrez_string_elements(self):
        rec = _parse(PUBMED_XML)
        self.assertEqual(rec["pmcid"], "PMC7654321")
        self.assertTrue(rec["has_pdf"])
        self.assertTrue(rec["is_oa"])

    def test_doi_from_real_entrez_string_elements(self):
        rec = _parse(PUBMED_XML)
        self.assertEqual(rec["doi"], "10.1000/xyz.test")

    def test_abstract_labels_preserved(self):
        rec = _parse(PUBMED_XML)
        self.assertIn("BACKGROUND: First part.", rec["abstract"])
        self.assertIn("RESULTS: Second part.", rec["abstract"])

    def test_databank_accession_is_not_treated_as_pmcid(self):
        # DataBankList 放的是 GenBank 等登录号，不能当 PMCID。
        rec = _parse(PUBMED_XML)
        self.assertNotEqual(rec["pmcid"], "ABC123")

    def test_missing_identifiers_yield_empty_strings(self):
        rec = _parse(PUBMED_XML_NO_PMC)
        self.assertEqual(rec["pmcid"], "")
        self.assertEqual(rec["doi"], "")
        self.assertFalse(rec["has_pdf"])
        self.assertEqual(rec["year"], 2023)

    def test_doi_falls_back_to_article_id_list(self):
        # ELocationID 缺失时，从 ArticleIdList 的 IdType=doi 兜底
        xml = PUBMED_XML_NO_PMC.replace(
            b'<ArticleId IdType="pubmed">99999999</ArticleId>',
            b'<ArticleId IdType="pubmed">99999999</ArticleId>'
            b'<ArticleId IdType="doi">10.1000/Fallback.DOI</ArticleId>')
        rec = _parse(xml)
        self.assertEqual(rec["doi"], "10.1000/fallback.doi")


class HandwrittenDictCompatTests(unittest.TestCase):
    """_el_attrs/_el_text 对手写 dict（旧测试/上层构造）也要兼容。"""

    def test_plain_dict_elements_still_parse(self):
        article = {
            "MedlineCitation": {
                "Article": {
                    "ArticleTitle": "Dict fixture.",
                    "Journal": {"Title": "J",
                                "JournalIssue": {"PubDate": {"Year": "2024"}}},
                    "AuthorList": [],
                    "ELocationID": [
                        {"EIdType": "doi", "ValidYN": "Y", "#text": "10.9/DICT"},
                    ],
                    "PublicationTypeList": [],
                },
                "PMID": "42",
            },
            "PubmedData": {"ArticleIdList": [
                {"IdType": "pmc", "#text": "PMC42"},
            ]},
        }

        rec = ncbi._parse_pubmed_article(article)

        self.assertEqual(rec["doi"], "10.9/dict")
        self.assertEqual(rec["pmcid"], "PMC42")


if __name__ == "__main__":
    unittest.main()
