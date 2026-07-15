"""NCBI Entrez 检索：PubMed、Gene、Protein 等多数据库统一入口。

依赖 biopython（Bio.Entrez），输出与 europepmc.py 相同的 dict 格式，
方便在 lit_agent 里无缝切换/合并数据源。

用法：
    ncbi.init(email="you@example.com", api_key="...")
    recs = ncbi.search_pubmed("cancer immunotherapy", max_results=30)
    gene = ncbi.fetch_gene("7157")       # TP53
    prot = ncbi.fetch_protein("NP_000537")
"""
import os
import time
from Bio import Entrez

# ── 全局状态 ──────────────────────────────────────────
_initialized = False

# 各 NCBI 数据库的检索字段映射（用于构造查询时按需加 field tag）
_PUBMED_FIELDS = {
    "title": "[Title]",
    "abstract": "[Title/Abstract]",
    "author": "[Author]",
    "journal": "[Journal]",
    "mesh": "[MeSH Terms]",
    "doi": "[DOI]",
    "pmid": "[PMID]",
}


def init(email: str | None = None, api_key: str | None = None):
    """初始化 Entrez：设 email（NCBI 强制要求）和可选 API key（提速率）。"""
    global _initialized
    Entrez.email = email or os.environ.get("NCBI_EMAIL", "")
    if not Entrez.email:
        # 从 lit_agent 的 .env 回退
        Entrez.email = os.environ.get("CONTACT_EMAIL", "researcher@example.com")
    if api_key:
        Entrez.api_key = api_key
    elif os.environ.get("NCBI_API_KEY"):
        Entrez.api_key = os.environ["NCBI_API_KEY"]
    _initialized = True


# ── 结果标准化 ─────────────────────────────────────────
def _parse_pubmed_article(article: dict) -> dict | None:
    """将 Entrez efetch (xml→dict) 的单篇 PubMed article 转成统一 dict。"""
    ma = article.get("MedlineCitation", {})
    art = ma.get("Article", {})
    if not art:
        return None

    # 标题
    title = art.get("ArticleTitle", "").rstrip(".")

    # 摘要
    abstract_parts = art.get("Abstract", {}).get("AbstractText", [])
    if isinstance(abstract_parts, str):
        abstract = abstract_parts
    else:
        abstract = " ".join(
            (p.get("Label", "") + ": " + p.get("#text", "") if isinstance(p, dict) and p.get("Label") else p.get("#text", p) if isinstance(p, dict) else str(p))
            for p in abstract_parts
        )

    # 期刊
    journal_info = art.get("Journal", {})
    journal = journal_info.get("Title", "")
    iso = journal_info.get("ISOAbbreviation", "")

    # 年份
    pub_date = journal_info.get("JournalIssue", {}).get("PubDate", {})
    year = None
    for k in ("Year", "MedlineDate"):
        v = pub_date.get(k, "")
        if v:
            try:
                year = int(str(v)[:4])
            except (ValueError, TypeError):
                pass
            break

    # DOI
    doi = ""
    for eid in art.get("ELocationID", []):
        if isinstance(eid, dict) and eid.get("ValidYN") == "Y" and eid.get("EIdType") == "doi":
            doi = str(eid.get("#text", "")).lower()

    # 作者
    authors = []
    for a in art.get("AuthorList", []):
        if isinstance(a, dict):
            ln = a.get("LastName", "")
            fn = a.get("ForeName", "") or a.get("Initials", "")
            authors.append(f"{ln} {fn}".strip())
    author_str = ", ".join(authors[:20])

    # PMID
    pmid = str(ma.get("PMID", ""))

    # PMC ID
    pmcid = ""
    for other_id in art.get("DataBankList", []):
        if isinstance(other_id, dict) and other_id.get("DataBankName") == "PMC":
            for acc in other_id.get("AccessionNumberList", []):
                pmcid = str(acc)
                break

    # 出版类型
    pub_types = []
    for pt in art.get("PublicationTypeList", []):
        if isinstance(pt, dict):
            pub_types.append(str(pt))

    return {
        "source": "pubmed",
        "id": pmid,
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "journal": journal or iso,
        "publisher": journal_info.get("Publisher", ""),
        "year": year,
        "citations": 0,          # PubMed 不直接给引用数
        "authors": author_str,
        "has_pdf": bool(pmcid),
        "is_oa": bool(pmcid),
    }


# ── PubMed 检索 ────────────────────────────────────────
def search_pubmed(query: str, max_results: int = 30,
                  min_year: int | None = None,
                  max_year: int | None = None) -> list[dict]:
    """用 Entrez 检索 PubMed，返回与 europepmc.search() 相同格式的列表。"""
    if not _initialized:
        init()

    # 拼搜索串：自动加年份过滤
    built_query = query
    if min_year and max_year:
        built_query += f" AND {min_year}:{max_year}[Publication Date]"
    elif min_year:
        built_query += f" AND {min_year}:3000[Publication Date]"

    try:
        handle = Entrez.esearch(db="pubmed", term=built_query,
                                retmax=max_results, sort="relevance")
        result = Entrez.read(handle)
        handle.close()
    except Exception as e:
        print(f"  ⚠ Entrez 搜索失败：{e}")
        return []

    id_list = result.get("IdList", [])
    if not id_list:
        return []

    # 批量 fetch（每次最多 50 篇，NCBI 建议）
    records = []
    for i in range(0, len(id_list), 50):
        batch = id_list[i:i + 50]
        try:
            handle = Entrez.efetch(db="pubmed", id=",".join(batch),
                                   rettype="xml", retmode="xml")
            fetched = Entrez.read(handle)
            handle.close()
        except Exception as e:
            print(f"  ⚠ Entrez fetch 失败：{e}")
            continue
        for article in fetched.get("PubmedArticle", []):
            rec = _parse_pubmed_article(article)
            if rec and rec["abstract"]:
                records.append(rec)
        if i + 50 < len(id_list):
            time.sleep(0.35)      # NCBI 速率礼貌

    return records


def search_pubmed_by_citations(query: str, max_results: int = 30) -> list[dict]:
    """PubMed 'most cited' 路 —— 用 Entrez 但是 PubMed 不直接支持按被引排序。
    折中：先用 relevance 搜更多，再在客户端按 PMID 去 Europe PMC 拉引用数会好些。
    这里只做 기본 relevance 搜索，调用方自行混合 europepmc 的引用数据。
    """
    # PubMed 不原生支持 citation sort，这里返回 relevance 结果，
    # 由上层在合并时从 europepmc 补引用数。
    return search_pubmed(query, max_results)


# ── Gene 查询 ──────────────────────────────────────────
def fetch_gene(gene_id: str | int) -> dict | None:
    """按 NCBI Gene ID 或 symbol 查基因摘要。"""
    if not _initialized:
        init()
    try:
        handle = Entrez.efetch(db="gene", id=str(gene_id),
                               rettype="xml", retmode="xml")
        records = Entrez.read(handle)
        handle.close()
    except Exception as e:
        print(f"  ⚠ Entrez Gene fetch 失败：{e}")
        return None
    entries = records.get("Entrezgene", [])
    if not entries:
        return None
    g = entries[0]
    gene_ref = g.get("Entrezgene_gene", {}).get("Gene-ref", {})
    summary = g.get("Entrezgene_summary", "")
    return {
        "gene_id": str(g.get("Entrezgene_track-info", {}).get("Gene-track", {}).get("Gene-track_geneid", "")),
        "symbol": str(gene_ref.get("Gene-ref_locus", "")),
        "name": str(gene_ref.get("Gene-ref_desc", "")),
        "summary": str(summary),
        "organism": str(gene_ref.get("Gene-ref_maploc", "")),
    }


# ── Protein 查询 ───────────────────────────────────────
def fetch_protein(accession: str) -> dict | None:
    """按 NCBI Protein accession 查蛋白序列摘要。"""
    if not _initialized:
        init()
    try:
        handle = Entrez.efetch(db="protein", id=accession,
                               rettype="gb", retmode="xml")
        records = Entrez.read(handle)
        handle.close()
    except Exception as e:
        print(f"  ⚠ Entrez Protein fetch 失败：{e}")
        return None
    entries = records.get("GBSeq", [])
    if not entries:
        return None
    s = entries[0]
    return {
        "accession": str(s.get("GBSeq_accession-version", "")),
        "length": int(s.get("GBSeq_length", 0)),
        "definition": str(s.get("GBSeq_definition", "")),
        "organism": str(s.get("GBSeq_organism", "")),
        "sequence": str(s.get("GBSeq_sequence", ""))[:200] + "...",  # 头 200bp 预览
    }


# ── 跨库关联（elink）────────────────────────────────────
def elink_pubmed_to_gene(pmid: str) -> list[str]:
    """从 PubMed ID 找出关联的 Gene ID 列表。"""
    if not _initialized:
        init()
    try:
        handle = Entrez.elink(dbfrom="pubmed", db="gene", id=pmid)
        result = Entrez.read(handle)
        handle.close()
    except Exception:
        return []
    ids = []
    for linkset in result:
        for link in linkset.get("LinkSetDb", []):
            if link.get("LinkName") == "pubmed_gene":
                ids.extend(link.get("Link", []))
    return [str(i) for i in ids]


def elink_pubmed_to_protein(pmid: str) -> list[str]:
    """从 PubMed ID 找出关联的 Protein accession 列表。"""
    if not _initialized:
        init()
    try:
        handle = Entrez.elink(dbfrom="pubmed", db="protein", id=pmid)
        result = Entrez.read(handle)
        handle.close()
    except Exception:
        return []
    ids = []
    for linkset in result:
        for link in linkset.get("LinkSetDb", []):
            if link.get("LinkName") == "pubmed_protein":
                ids.extend(link.get("Link", []))
    return [str(i) for i in ids]
