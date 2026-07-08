# 数据库选择指南

> 抄自 K-Dense-AI/scientific-agent-skills 的 database-lookup skill。
> lit-agent 检索源选型参考：什么问题该查什么库。

## 检索源对照

| 你要查的是... | 主数据库 | lit-agent 里怎么用 |
|---|---|---|
| **生物医学文献** | PubMed / Europe PMC | `discover --source all`（默认走 EPMC，加 NCBI 合并） |
| **化学化合物、分子** | PubChem | `citations.verify_doi` 间接覆盖 |
| **生物活性数据（IC50、结合实验）** | ChEMBL | 待接入 |
| **药物标签、不良反应、召回** | FDA (OpenFDA) | 待接入 |
| **药物药理、适应症** | DrugBank | 待接入 |
| **蛋白质序列、功能注释** | UniProt | `ncbi.fetch_protein()` 可查 NCBI Protein |
| **基因信息、基因组位置** | NCBI Gene | `ncbi.fetch_gene()` |
| **基因表达数据集** | GEO | `ncbi` 模块可扩展接入 |
| **生物通路** | Reactome, KEGG | 待接入 |
| **蛋白质-蛋白质相互作用** | STRING | 待接入 |
| **体细胞突变（癌症）** | COSMIC | 待接入 |
| **变异临床意义** | ClinVar | `ncbi` 可扩展 |
| **GWAS SNP-性状关联** | GWAS Catalog | 待接入 |
| **临床试验** | ClinicalTrials.gov | 待接入 |
| **药物-靶点-疾病关联** | Open Targets | 待接入（GraphQL POST） |
| **专利** | USPTO (PatentsView) | 待接入 |
| **美国经济时序数据** | FRED | 待接入 |

## 标识符格式速查

不同库用不同 ID 体系。查询失败先检查 ID 格式：

| 标识符 | 格式 | 示例 | 用于 |
|---|---|---|---|
| UniProt accession | `P#####` 或 `Q#####` | `P04637` (TP53) | UniProt, STRING, AlphaFold |
| Ensembl gene ID | `ENSG###########` | `ENSG00000141510` | Ensembl, Open Targets, GTEx |
| NCBI Gene ID | 整数 | `7157` (TP53) | NCBI Gene, GEO, DisGeNET |
| PubChem CID | 整数 | `2244` (阿司匹林) | PubChem |
| ChEMBL ID | `CHEMBL####` | `CHEMBL25` | ChEMBL |
| dbSNP rsID | `rs########` | `rs334` | dbSNP, GWAS Catalog, gnomAD |
| Reactome ID | `R-HSA-######` | `R-HSA-109581` | Reactome |

## 标识符转换链

当某库不认某个 ID 时，按下面路径转：

**基因**：Symbol（如 "TP53"）→ NCBI Gene esearch 拿 Gene ID → Ensembl `/xrefs/symbol/homo_sapiens/{symbol}` 转 Ensembl ID → UniProt 搜 `gene_exact:{symbol} AND organism_id:9606` 拿 UniProt accession。

**化合物**：名称 → PubChem `/compound/name/{name}/cids/JSON` 拿 CID → UniChem 或 ChEMBL 搜转 ChEMBL ID。名称失败试 SMILES / InChIKey / CAS 号。

**变异**：rsID（如 "rs334"）直接在 dbSNP / ClinVar / GWAS Catalog / gnomAD 用。基因组坐标用 Ensembl VEP 拿后果注释和关联 rsID。

## lit-agent 当前覆盖

| 库 | 接入方式 | 函数 |
|---|---|---|
| Europe PMC | `europepmc.search()` | 主检索源（含引用数） |
| NCBI PubMed | `ncbi.search_pubmed()` | `--source ncbi/all` |
| NCBI Gene | `ncbi.fetch_gene()` | 按 ID 查基因 |
| NCBI Protein | `ncbi.fetch_protein()` | 按 accession 查蛋白 |
| NCBI elink | `ncbi.elink_pubmed_to_gene/protein()` | 跨库关联 |
| Unpaywall | `fulltext._oa_pdf_urls()` | OA 全文定位 |
| CrossRef | `citations.verify_doi()` | DOI 验证 + BibTeX 元数据 |

## 需要扩展时的接入顺序建议

1. **ChEMBL** — 药物活性数据，REST API 简单
2. **ClinicalTrials.gov** — 临床试验，REST API 无需 key
3. **UniProt** — 蛋白注释，REST API
4. **Open Targets** — GraphQL POST，要 `curl` 不能用 GET
5. **STRING** — 蛋白相互作用，REST API
