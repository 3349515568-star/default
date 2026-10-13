import unittest

import ncbi


def _article(pubmed_data=None, databank_list=None):
    return {
        "MedlineCitation": {
            "Article": {
                "ArticleTitle": "A test article.",
                "Journal": {
                    "Title": "Journal of Tests",
                    "JournalIssue": {"PubDate": {"Year": "2024"}},
                },
                "AuthorList": [],
                "ELocationID": [],
                "PublicationTypeList": [],
                "DataBankList": databank_list or [],
            },
            "PMID": "12345678",
        },
        "PubmedData": pubmed_data or {},
    }


class PubmedParseTests(unittest.TestCase):
    def test_pmcid_comes_from_pubmed_data_article_id_list(self):
        article = _article(pubmed_data={"ArticleIdList": [
            {"IdType": "pubmed", "#text": "12345678"},
            {"IdType": "pmc", "#text": "PMC7654321"},
            {"IdType": "doi", "#text": "10.1000/test"},
        ]})

        rec = ncbi._parse_pubmed_article(article)

        self.assertEqual(rec["pmcid"], "PMC7654321")
        self.assertTrue(rec["has_pdf"])
        self.assertTrue(rec["is_oa"])

    def test_databank_list_is_not_used_for_pmcid(self):
        # DataBankList 放的是 GenBank 等登录号；老版本误在这里找 PMC，
        # 导致 PubMed 记录的 pmcid 恒空、EPMC 全文通路静默失效。
        article = _article(databank_list=[
            {"DataBankName": "GENBANK", "AccessionNumberList": ["ABC123"]},
        ])

        rec = ncbi._parse_pubmed_article(article)

        self.assertEqual(rec["pmcid"], "")
        self.assertFalse(rec["has_pdf"])

    def test_missing_pubmed_data_yields_empty_pmcid(self):
        rec = ncbi._parse_pubmed_article(_article())
        self.assertEqual(rec["pmcid"], "")


if __name__ == "__main__":
    unittest.main()
