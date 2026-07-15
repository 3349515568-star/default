# lit-agent 项目交接文档

> 公开版交接说明。用户视角安装/用法见 [README.md](README.md)。

## 项目定位

lit-agent 是一个生物医学文献检索与精读工具：

1. 按 profile 或临时 query 检索 Europe PMC / Semantic Scholar / PubMed。
2. 用 LLM 对候选文献做相关度打分。
3. 优先下载开放获取全文：Unpaywall -> Semantic Scholar OA -> 出版商直连 -> Europe PMC render。
4. 拿不到 OA 全文时先基于摘要总结，并列入待取清单。
5. 用户可把自己有权限访问的 PDF 放入 `inbox/`，再用 `ingest` 补全文精读。

## 模块速查

| 文件 | 职责 |
|---|---|
| [lit_agent.py](lit_agent.py) | CLI 主程序，包含 discover / ingest / resummarize / refetch / queue / index / cost |
| [europepmc.py](europepmc.py) | Europe PMC 检索与元数据解析 |
| [ncbi.py](ncbi.py) | NCBI Entrez 检索：PubMed、Gene、Protein |
| [semantic_scholar.py](semantic_scholar.py) | Semantic Scholar 文献检索、引用指标和 OA 链接 |
| [fulltext.py](fulltext.py) | OA PDF 定位、下载、PyMuPDF 文本解析 |
| [llm.py](llm.py) | 多 provider OpenAI-compatible LLM 客户端 |
| [store.py](store.py) | SQLite 去重、配额、状态、token 用量记录 |
| [review.py](review.py) | 基于已总结文献库生成综述草稿 |
| [citations.py](citations.py) | DOI / Crossref 引文格式辅助 |
| [config.toml](config.toml) | 检索、排序、LLM provider、价格估算等配置 |
| [profiles.toml](profiles.toml) | 命名检索方案 |

## 本地状态文件

这些文件不应提交：

- `.env`
- `db.sqlite`
- `logs/`
- `pdfs/`
- `papers/`
- `summaries/`
- `inbox/`
- `__pycache__/`

## 关键设计

- `query / intent / label` 三分离：检索召回、LLM 打分和归档标签分别优化。
- 多源检索 + LLM 重排：EPMC/S2/PubMed 合并召回，S2 补引用指标，LLM 相关度压掉跑题高引文献。
- PDF-first：优先拿 OA PDF 并用 PyMuPDF 解析，拿不到全文则退回摘要总结。
- 多 provider：`config.toml` 里每个 provider 都是 OpenAI-compatible endpoint，实际启用取决于 `.env` 是否提供 key。
- 低温总结：默认温度较低，目标是忠实整理文献，不做开放式推理。

## 常用命令

```powershell
py -m pip install -r requirements.txt
Copy-Item .env.example .env

py lit_agent.py discover --profile berberine
py lit_agent.py queue
py lit_agent.py refetch
py lit_agent.py ingest
py lit_agent.py resummarize --scope abstract
py lit_agent.py index
py lit_agent.py cost
```

中文输出乱码时：

```powershell
$env:PYTHONUTF8=1
```

## 待办

- 补 `ingest` 的真实 PDF 回归测试。
- 给 `fulltext.py` 加单元测试，覆盖 OA URL 级联失败与摘要回退。
- 为 journal impact factor 或自定义期刊权重增加可选 CSV 配置。
