"""系统化文献综述模块：按七阶段 workflow 聚合已总结文献。

与 lit_agent.py 的 discover（逐篇总结）互补：
- discover   = 检索→打分→精读单篇
- review     = 跨多篇按主题聚合→写综述→配 PRISMA 流程图

用法（在 lit_agent.py 里）：
    py lit_agent.py review --query "CRISPR off-target" --out review.md
"""
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import store


def _themes_from_summaries(rows: list, llm_summarize, max_chars: int) -> list[dict]:
    """让 LLM 从一批已总结文献的摘要里归纳出 3-5 个主题。"""
    if not rows:
        return []
    # 拼接所有文献标题+摘要前 300 字给 LLM 归纳主题
    snippets = []
    for r in rows:
        abstract = (r["title"] or "") + " | " + str(r.get("query", ""))
        snippets.append(f"[{r['year']}] {abstract[:300]}")
    combined = "\n".join(snippets)[:max_chars]

    prompt = (
        "你是文献综述助手。下面是一批已检索文献的标题和年份。"
        "请从中归纳出 3-5 个核心主题，每个主题给出：\n"
        "1. 主题名（中文，简短）\n"
        "2. 该主题下相关文献的 uid 或标题片段（用于后续归类）\n"
        "3. 一句话描述该主题的研究脉络\n\n"
        "输出 JSON 数组：[{\"theme\": \"...\", \"papers\": [\"uid1\",\"uid2\"], \"narrative\": \"...\"}]\n\n"
        f"文献列表：\n{combined}"
    )
    # 复用 deepseek._chat 走 summary 步骤
    import deepseek
    raw = deepseek._chat([{"role": "user", "content": prompt}],
                         step="summary", json_mode=True, tag="review_themes")
    try:
        themes = deepseek._extract_json(raw)
        if isinstance(themes, list):
            return themes
    except Exception:
        pass
    # 失败时按年份分桶作为兜底主题
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["year"] or "未知年份"].append(r["uid"])
    return [{"theme": f"{y} 年研究", "papers": uids, "narrative": ""}
            for y, uids in by_year.items()]


def _prisma_flow(initial: int, after_dedup: int, after_title: int,
                 after_abstract: int, included: int) -> str:
    """生成 PRISMA 流程图的 markdown 文本（lit-agent 简化版，没接 schematics）。"""
    return f"""## PRISMA 流程图

```
检索阶段:  n = {initial}
   │
   ├─ 去重后:        n = {after_dedup}
   │
   ├─ 标题筛选后:    n = {after_title}
   │
   ├─ 摘要筛选后:    n = {after_abstract}
   │
   └─ 纳入综述:      n = {included}
```
"""


def generate_review(conn, query: str, out_path: Path,
                    llm_summarize_fn, max_chars: int = 120000) -> Path:
    """生成一篇系统化综述 markdown。

    参数：
        conn            : sqlite 连接
        query           : 主题/检索词（用于筛选 papers.query 匹配的文献）
        out_path        : 输出 .md 路径
        llm_summarize_fn: deepseek.summarize 函数引用（用于写主题段落）
        max_chars       : 单次给 LLM 的最大字符数
    """
    # Phase 1: 从数据库筛出与 query 相关的已总结文献
    rows = conn.execute(
        "SELECT * FROM papers WHERE status IN "
        "('summarized_full','summarized_abstract','ingested') "
        "AND (query LIKE ? OR title LIKE ?) "
        "ORDER BY score DESC",
        (f"%{query}%", f"%{query}%")
    ).fetchall()

    if not rows:
        out_path.write_text(f"# 综述：{query}\n\n无匹配的已总结文献。先跑 `discover` 检索。\n",
                            encoding="utf-8")
        return out_path

    initial = len(rows)
    # Phase 3: 简化筛选（已有 DeepSeek 相关度分，>3 的保留）
    screened = [r for r in rows if (r["relevance"] or 0) >= 3.0]
    if not screened:
        screened = rows       # 全留，别让阈值把综述筛空

    # Phase 4: 提取主题
    themes = _themes_from_summaries(screened, llm_summarize_fn, max_chars)

    # Phase 5: 按主题写综述
    out_lines = [
        f"# 文献综述：{query}",
        f"\n> 生成日期：{date.today().isoformat()} · 纳入 {len(screened)} 篇文献\n",
    ]

    # PRISMA
    out_lines.append(_prisma_flow(
        initial=initial,
        after_dedup=initial,          # lit-agent 已在 discover 阶段去重
        after_title=len(screened),
        after_abstract=len(screened),
        included=len(screened),
    ))

    # 搜索策略
    out_lines.append("## 检索策略\n")
    out_lines.append(f"- 检索词：`{query}`")
    out_lines.append(f"- 数据源：lit-agent 已总结文献库（Europe PMC + NCBI PubMed）")
    out_lines.append(f"- 筛选标准：DeepSeek 相关度 ≥ 3.0")
    out_lines.append("")

    # 主题段落
    out_lines.append("## 主题综述\n")
    for t in themes:
        theme_name = t.get("theme", "未命名主题")
        narrative = t.get("narrative", "")
        paper_uids = t.get("papers", [])

        out_lines.append(f"### {theme_name}\n")
        if narrative:
            out_lines.append(narrative + "\n")

        # 列出该主题下的文献
        matched = []
        for r in screened:
            if r["uid"] in paper_uids or any(
                frag and frag.lower() in (r["title"] or "").lower()
                for frag in paper_uids if isinstance(frag, str)
            ):
                matched.append(r)
        if not matched:
            # 匹配不上就按 uid 数量截前 5 篇相关文献
            matched = screened[:5]

        out_lines.append("**相关文献：**\n")
        for r in matched:
            doi_link = f"https://doi.org/{r['doi']}" if r["doi"] else ""
            out_lines.append(
                f"- {r['authors'][:40] if r['authors'] else ''} ({r['year'] or ''}). "
                f"*{r['title'][:80]}*. {r['journal'] or ''}. {doi_link}"
            )
        out_lines.append("")

    # 参考文献
    out_lines.append("## 参考文献\n")
    for i, r in enumerate(screened, 1):
        doi_link = f"https://doi.org/{r['doi']}" if r["doi"] else ""
        out_lines.append(
            f"{i}. {r['authors'] or ''} ({r['year'] or ''}). "
            f"{r['title']}. *{r['journal'] or ''}*. {doi_link}"
        )

    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return out_path
