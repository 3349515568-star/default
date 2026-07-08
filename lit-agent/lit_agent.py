"""lit-agent: 生物医学文献检索 + 精读总结 agent。

子命令：
  discover --query "关键词" [--max N] [--min-year Y]   检索、排序、下载OA全文、总结
  ingest                                              处理 inbox/ 里手动下载的 PDF（机构权限拿到的付费文章）
  queue                                               打印当前“待人工下载”清单
  index                                               重建 summaries/INDEX.md

用法示例：
  py lit_agent.py discover --query "GLP-1 receptor agonist cardiovascular outcomes"
  py lit_agent.py ingest
"""
import argparse
import os
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

import citations
import deepseek
import europepmc
import fulltext
import ncbi
import review
import store

ROOT = Path(__file__).parent
SUMMARIES = ROOT / "summaries"
PDFS = ROOT / "pdfs"
INBOX = ROOT / "inbox"
TO_FETCH = SUMMARIES / "_to_fetch.md"
INDEX = SUMMARIES / "INDEX.md"


def load_config():
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def load_profiles():
    path = ROOT / "profiles.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {p["name"]: p for p in data.get("profile", [])}


def setup(args=None):
    load_dotenv(ROOT / ".env")
    email = os.getenv("CONTACT_EMAIL")
    if not email:
        sys.exit("✗ 请在 .env 里填 CONTACT_EMAIL（复制 .env.example）")
    cfg = load_config()
    # 只有用到 LLM 的子命令才初始化 provider（cost/queue/index 不需要）。新增用 LLM 的命令记得加进来。
    if args and args.cmd in {"discover", "ingest", "resummarize", "refetch", "review"}:
        providers = cfg["providers"]
        keys = {name: os.getenv(p["key_env"]) for name, p in providers.items()}
        llm = cfg["llm"]
        models = dict(cfg["models"])
        if getattr(args, "score_model", None):
            models["score"] = args.score_model
        if getattr(args, "summary_model", None):
            models["summary"] = args.summary_model
        deepseek.init(providers, models, keys, llm["max_tokens"], llm["temperature"])
    # NCBI Entrez（PubMed / Gene / Protein 多库入口）
    ncbi_api_key = os.getenv("NCBI_API_KEY", "")
    ncbi.init(email=email, api_key=ncbi_api_key)
    return cfg, email


def apply_model_overrides(args):
    """命令行 --score-model / --summary-model 临时覆盖配置里的模型。"""
    if getattr(args, "score_model", None):
        deepseek.set_model("score", args.score_model)
    if getattr(args, "summary_model", None):
        deepseek.set_model("summary", args.summary_model)


def slugify(text: str, maxlen: int = 60) -> str:
    text = re.sub(r"[^\w一-鿿 -]", "", text).strip().replace(" ", "_")
    return text[:maxlen] or "untitled"


def _summary_header(rec: dict) -> str:
    return (
        f"# {rec['title']}\n\n"
        f"> {rec.get('journal','')} · {rec.get('year','')} · "
        f"引用 {rec.get('citations',0)} · DOI: {rec.get('doi','')}\n"
        f"> 作者：{rec.get('authors','')}\n\n"
    )


def write_summary_file(rec: dict, body: str, tag: str) -> Path:
    day_dir = SUMMARIES / date.today().isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"[{tag}]{slugify(rec['title'])}.md"
    path.write_text(_summary_header(rec) + body + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- discover
def cmd_discover(args, cfg, email):
    prof = {}
    if args.profile:
        profiles = load_profiles()
        if args.profile not in profiles:
            sys.exit(f"✗ 没找到 profile «{args.profile}»。可用：{', '.join(profiles) or '(无)'}")
        prof = profiles[args.profile]
    query = args.query or prof.get("query")
    if not query:
        sys.exit("✗ 需要 --query 或 --profile 之一")
    intent = args.intent or prof.get("intent") or query   # DeepSeek 打分依据
    label = prof.get("label") or query                     # 显示/归档标签
    min_year = (args.min_year if args.min_year is not None
                else prof.get("min_year", cfg["search"]["min_year"]))
    keep_classics = bool(prof.get("keep_classics", False))
    conn = store.connect()
    apply_model_overrides(args)

    allowed = cfg["limits"]["daily_max"] - store.count_today(conn)
    requested = args.max if args.max is not None else cfg["limits"]["daily_max"]
    quota = min(requested, allowed)
    if quota <= 0:
        print(f"今天已达每日上限（{cfg['limits']['daily_max']} 篇），明天再来或调高 daily_max。")
        return

    print(f"检索{label}：")
    print(f"  query: {query[:140]}")
    print(f"  (min_year={min_year}, keep_classics={keep_classics}, source={args.source}) ...")

    # ── 按 source 选检索源 ──
    if args.source in ("ncbi", "all"):
        cands_ncbi = ncbi.search_pubmed(query, cfg["search"]["candidates"], min_year)
    else:
        cands_ncbi = []
    if args.source in ("epmc", "all"):
        cands_epmc = europepmc.search(query, cfg["search"]["candidates"], min_year, keep_classics)
    else:
        cands_epmc = []

    # 合并去重（按 DOI）
    seen_doi = set()
    cands = []
    for c in cands_epmc:
        if c["doi"] and c["doi"] not in seen_doi:
            seen_doi.add(c["doi"])
            cands.append(c)
        elif not c["doi"]:
            cands.append(c)
    for c in cands_ncbi:
        if c["doi"] and c["doi"] not in seen_doi:
            seen_doi.add(c["doi"])
            cands.append(c)
        elif not c["doi"]:
            cands.append(c)

    print(f"  EPMC={len(cands_epmc)} NCBI={len(cands_ncbi)} 合并去重→{len(cands)} 候选")
    already = store.seen_uids(conn)
    seen_titles = store.seen_title_keys(conn)
    cands = [c for c in cands
             if store.make_uid(c) not in already
             and store.norm_title(c["title"]) not in seen_titles]
    # 同一 run 内按归一化标题去重（预印本+见刊版同时出现时，留引用数高的）
    by_title = {}
    for c in cands:
        k = store.norm_title(c["title"])
        if k not in by_title or (c["citations"] or 0) > (by_title[k]["citations"] or 0):
            by_title[k] = c
    cands = list(by_title.values())
    if not cands:
        print("没有新的候选（可能都处理过了，或检索无结果）。")
        return
    print(f"  拿到 {len(cands)} 篇新候选，DeepSeek 正在打相关度分 ...")

    rel = deepseek.score_relevance(intent, cands)
    store.log_usage(conn, deepseek.drain_usage(), ref=f"[打分] {label}")
    newest = max((c["year"] for c in cands if c["year"]), default=date.today().year)
    rk = cfg["rank"]
    for i, c in enumerate(cands):
        c["relevance"] = rel.get(i, 0.0)
        c["score"] = (rk["w_relevance"] * c["relevance"]
                      + europepmc.prelim_score(c, rk["w_citation"], rk["w_recency"], newest))
    cands.sort(key=lambda c: c["score"], reverse=True)
    selected = cands[:quota]

    print(f"  选出 Top {len(selected)} 篇开始精读：\n")
    for rec in selected:
        uid = store.make_uid(rec)
        print(f"  · [{rec['relevance']:.0f}分/{rec['citations']}引] {rec['title'][:70]}")

        pdf_bytes = None
        if not args.no_fulltext:
            pdf_bytes = fulltext.fetch_pdf(
                rec, email, cfg["fulltext"]["prefer_repository"])

        if pdf_bytes:
            text = fulltext.pdf_to_text(pdf_bytes)
            is_full = len(text) > 1500
        else:
            text, is_full = rec["abstract"], False

        pdf_path = ""
        if pdf_bytes:
            PDFS.mkdir(exist_ok=True)
            pdf_path = str(PDFS / f"{slugify(rec['title'])}.pdf")
            Path(pdf_path).write_bytes(pdf_bytes)

        try:
            body = deepseek.summarize(
                rec, text, label, is_full, cfg["fulltext"]["max_chars"])
        except Exception as e:
            deepseek.drain_usage()
            print(f"      ✗ 总结失败跳过 ({type(e).__name__})")
            continue
        store.log_usage(conn, deepseek.drain_usage(), ref=rec["title"][:60])
        tag = "全文" if is_full else "摘要"
        spath = write_summary_file(rec, body, tag)
        print(f"      → {'下载全文✓' if is_full else '仅摘要(待取全文)'}  {spath.name}")

        store.upsert(conn, {
            "uid": uid, "pmid": rec["pmid"], "pmcid": rec["pmcid"], "doi": rec["doi"],
            "title": rec["title"], "journal": rec["journal"], "year": rec["year"],
            "citations": rec["citations"], "authors": rec["authors"],
            "status": "summarized_full" if is_full else "summarized_abstract",
            "has_fulltext": 1 if is_full else 0,
            "relevance": rec["relevance"], "score": rec["score"],
            "pdf_path": pdf_path, "summary_path": str(spath), "query": label,
            "first_seen": date.today().isoformat(),
            "processed_date": date.today().isoformat(),
        })
        if not is_full:  # 没拿到全文 → 进待取清单
            _queue(conn, rec, label)

    rebuild_index(conn)
    rebuild_to_fetch(conn)
    print(f"\n完成。今日已处理 {store.count_today(conn)}/{cfg['limits']['daily_max']} 篇。"
          f"\n总结见 {SUMMARIES}，待人工下载清单见 {TO_FETCH}")


def _queue(conn, rec, query):
    uid = store.make_uid(rec)
    row = store.get(conn, uid)
    # 已有摘要总结记录，补一条标记它仍缺全文（保留 status）
    store.upsert(conn, {
        "uid": uid, "pmid": rec["pmid"], "pmcid": rec["pmcid"], "doi": rec["doi"],
        "title": rec["title"], "journal": rec["journal"], "year": rec["year"],
        "citations": rec["citations"], "authors": rec["authors"],
        "status": row["status"] if row else "queued",
        "has_fulltext": 0, "relevance": rec.get("relevance"), "score": rec.get("score"),
        "pdf_path": row["pdf_path"] if row else "",
        "summary_path": row["summary_path"] if row else "",
        "query": query, "first_seen": date.today().isoformat(),
        "processed_date": row["processed_date"] if row else None,
    })


# ---------------------------------------------------------------- ingest
def cmd_ingest(args, cfg, email):
    conn = store.connect()
    apply_model_overrides(args)
    pdfs = sorted(INBOX.glob("*.pdf"))
    if not pdfs:
        print(f"inbox/ 里没有 PDF。把机构权限下载的付费文章丢进 {INBOX} 再跑。")
        return
    print(f"发现 {len(pdfs)} 个待处理 PDF。\n")
    for p in pdfs:
        print(f"  · {p.name}")
        text = fulltext.pdf_file_to_text(p)
        if len(text) < 500:
            print("      ⚠ 解析出的文字太少，可能是扫描版/加密 PDF，跳过。")
            continue
        doi = fulltext.guess_doi(text)
        row = store.find_by_doi(conn, doi) if doi else None
        # 用待取清单里的元信息（标题/期刊/引用），匹配不上就用文件名当标题
        rec = {
            "title": (row["title"] if row else p.stem),
            "journal": row["journal"] if row else "",
            "year": row["year"] if row else None,
            "citations": row["citations"] if row else 0,
            "doi": doi or (row["doi"] if row else ""),
            "pmid": row["pmid"] if row else None,
            "pmcid": row["pmcid"] if row else None,
            "authors": row["authors"] if row else "",
        }
        query = (row["query"] if row else args.query) or rec["title"]
        try:
            body = deepseek.summarize(rec, text, query, True, cfg["fulltext"]["max_chars"])
        except Exception as e:
            deepseek.drain_usage()
            print(f"      ✗ 总结失败跳过 ({type(e).__name__})")
            continue
        store.log_usage(conn, deepseek.drain_usage(), ref=rec["title"][:60])
        spath = write_summary_file(rec, body, "全文")
        # 归档 PDF
        PDFS.mkdir(exist_ok=True)
        dest = PDFS / f"{slugify(rec['title'])}.pdf"
        p.replace(dest)
        uid = store.make_uid(rec) if (rec["doi"] or rec["pmid"]) else f"ingest:{p.stem}"
        store.upsert(conn, {
            "uid": uid, "pmid": rec["pmid"], "pmcid": rec["pmcid"], "doi": rec["doi"],
            "title": rec["title"], "journal": rec["journal"], "year": rec["year"],
            "citations": rec["citations"], "authors": rec["authors"],
            "status": "ingested", "has_fulltext": 1,
            "relevance": row["relevance"] if row else None,
            "score": row["score"] if row else None,
            "pdf_path": str(dest), "summary_path": str(spath), "query": query,
            "first_seen": row["first_seen"] if row else date.today().isoformat(),
            "processed_date": date.today().isoformat(),
        })
        print(f"      → 全文精读✓ {spath.name}  (PDF 归档至 pdfs/)")
    rebuild_index(conn)
    rebuild_to_fetch(conn)
    print("\n完成。")


# ---------------------------------------------------------------- re-summarize
def cmd_resummarize(args, cfg, email):
    conn = store.connect()
    apply_model_overrides(args)
    if args.doi:
        rows = [r for r in [store.find_by_doi(conn, args.doi)] if r]
    else:
        status_map = {
            "abstract": ("summarized_abstract",),
            "full": ("summarized_full", "ingested"),
            "all": ("summarized_abstract", "summarized_full", "ingested"),
        }
        sts = status_map[args.scope]
        qmarks = ",".join("?" for _ in sts)
        rows = conn.execute(
            f"SELECT * FROM papers WHERE status IN ({qmarks})", sts).fetchall()
    if not rows:
        print("没有匹配的已总结文献。")
        return
    print(f"用当前模板重做 {len(rows)} 篇（不计入每日配额，就地覆盖原文件）：\n")
    for r in rows:
        rec = dict(r)
        label = r["query"] or rec["title"]
        # 取正文：优先已存 PDF（全文重模板），否则重新拉摘要（轻模板）
        if r["has_fulltext"] and r["pdf_path"] and Path(r["pdf_path"]).exists():
            text = fulltext.pdf_file_to_text(r["pdf_path"])
            is_full = len(text) > 1500
        else:
            meta = europepmc.fetch_record(doi=r["doi"], pmid=r["pmid"], pmcid=r["pmcid"])
            if not meta or not meta["abstract"]:
                print(f"  跳过（取不到摘要）：{r['title'][:50]}")
                continue
            text, is_full = meta["abstract"], False
        try:
            body = deepseek.summarize(rec, text, label, is_full, cfg["fulltext"]["max_chars"])
        except Exception as e:
            deepseek.drain_usage()
            print(f"  ✗ 总结失败跳过 ({type(e).__name__})")
            continue
        store.log_usage(conn, deepseek.drain_usage(), ref=rec["title"][:60])
        if r["summary_path"] and Path(r["summary_path"]).exists():
            spath = Path(r["summary_path"])
            spath.write_text(_summary_header(rec) + body + "\n", encoding="utf-8")
        else:
            spath = write_summary_file(rec, body, "全文" if is_full else "摘要")
        conn.execute("UPDATE papers SET summary_path=? WHERE uid=?", (str(spath), r["uid"]))
        conn.commit()
        print(f"  ✓ [{'全文' if is_full else '摘要'}] {r['title'][:55]}")
    rebuild_index(conn)
    rebuild_to_fetch(conn)
    print("\n完成。")


# ---------------------------------------------------------------- refetch
def cmd_refetch(args, cfg, email):
    """对「缺全文」的文献重试 OA 下载：先刷新元数据（拿当初还没有的 PMCID），
    再走 fetch_pdf 级联；下到就补全文精读、状态升级、踢出待取清单。不计每日配额。"""
    conn = store.connect()
    apply_model_overrides(args)
    seen, targets = set(), []
    for r in [x for x in store.all_summarized(conn) if not x["has_fulltext"]] + list(store.queued(conn)):
        if r["uid"] not in seen:
            seen.add(r["uid"])
            targets.append(r)
    if not targets:
        print("没有缺全文的文献需要重试。")
        return
    print(f"对 {len(targets)} 篇缺全文的文献重试 OA 下载（先刷新元数据拿最新 PMCID）...\n")
    got = 0
    for r in targets:
        rec = dict(r)
        meta = europepmc.fetch_record(doi=rec["doi"], pmid=rec["pmid"], pmcid=rec["pmcid"])
        if meta and meta.get("pmcid"):
            rec["pmcid"] = meta["pmcid"]          # 当初没进 PMC、现在有了
        pdf = fulltext.fetch_pdf(rec, email, cfg["fulltext"]["prefer_repository"])
        if not pdf:
            print(f"  · 仍无 OA：{r['title'][:55]}")
            continue
        text = fulltext.pdf_to_text(pdf)
        if len(text) < 1500:
            print(f"  · 下到但文字过短(疑扫描版)，跳过：{r['title'][:45]}")
            continue
        PDFS.mkdir(exist_ok=True)
        pdf_path = str(PDFS / f"{slugify(rec['title'])}.pdf")
        Path(pdf_path).write_bytes(pdf)
        label = r["query"] or rec["title"]
        try:
            body = deepseek.summarize(rec, text, label, True, cfg["fulltext"]["max_chars"])
        except Exception as e:
            deepseek.drain_usage()
            print(f"  · 总结失败跳过：{r['title'][:45]} ({type(e).__name__})")
            continue
        store.log_usage(conn, deepseek.drain_usage(), ref=rec["title"][:60])
        spath = write_summary_file(rec, body, "全文")
        old = r["summary_path"]                    # 删掉旧的 [摘要] 文件
        if old and Path(old).exists() and Path(old).resolve() != spath.resolve():
            try:
                Path(old).unlink()
            except OSError:
                pass
        store.upsert(conn, {
            "uid": r["uid"], "pmid": rec["pmid"], "pmcid": rec["pmcid"], "doi": rec["doi"],
            "title": rec["title"], "journal": rec["journal"], "year": rec["year"],
            "citations": rec["citations"], "authors": rec["authors"],
            "status": "summarized_full", "has_fulltext": 1,
            "relevance": r["relevance"], "score": r["score"],
            "pdf_path": pdf_path, "summary_path": str(spath), "query": label,
            "first_seen": r["first_seen"], "processed_date": date.today().isoformat(),
        })
        got += 1
        print(f"  ✓ 补到全文：{r['title'][:55]}")
    rebuild_index(conn)
    rebuild_to_fetch(conn)
    print(f"\n完成：{got}/{len(targets)} 篇补到了全文（其余仍是付费墙/未 OA）。")


# ---------------------------------------------------------------- index / queue
def rebuild_index(conn):
    rows = store.all_summarized(conn)
    lines = ["# 文献总结索引\n",
             "| 日期 | 类型 | 引用 | 年 | 标题 | 期刊 | 总结 |",
             "|---|---|---|---|---|---|---|"]
    tag = {"summarized_full": "全文", "ingested": "全文",
           "summarized_abstract": "摘要"}
    for r in rows:
        sp = r["summary_path"] or ""
        try:
            sp = Path(sp).relative_to(SUMMARIES).as_posix()
        except ValueError:
            # 路径不在此项目目录下（如旧目录），从路径里抠 summaries/ 后面的部分
            if "summaries" in sp.lower():
                idx = sp.lower().find("summaries")
                sp = sp[idx + len("summaries") + 1:].replace("\\", "/")
            else:
                sp = Path(sp).name
        link = f"[查看]({sp})" if sp else ""
        lines.append(
            f"| {r['processed_date'] or ''} | {tag.get(r['status'],'')} "
            f"| {r['citations'] or 0} | {r['year'] or ''} "
            f"| {r['title'][:60]} | {r['journal'] or ''} | {link} |")
    SUMMARIES.mkdir(exist_ok=True)
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rebuild_to_fetch(conn):
    rows = [r for r in store.all_summarized(conn) if not r["has_fulltext"]]
    rows += store.queued(conn)
    lines = ["# 待人工下载全文清单",
             "",
             "下面这些没拿到 OA 全文（目前只有摘要总结）。请用校园网/机构权限点链接下 PDF，",
             f"丢进 `inbox/` 文件夹，再运行 `py lit_agent.py ingest` 即可补全文精读。\n"]
    seen = set()
    for r in rows:
        if r["uid"] in seen:
            continue
        seen.add(r["uid"])
        doi = r["doi"] or ""
        link = f"https://doi.org/{doi}" if doi else "（无 DOI，自行检索标题）"
        lines.append(f"- **{r['title']}**  \n"
                     f"  {r['journal'] or ''} · {r['year'] or ''} · 引用 {r['citations'] or 0}  \n"
                     f"  {link}")
    TO_FETCH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_queue(args, cfg, email):
    conn = store.connect()
    rows = [r for r in store.all_summarized(conn) if not r["has_fulltext"]]
    if not rows:
        print("待取清单为空。")
        return
    print(f"待人工下载全文（{len(rows)} 篇）：\n")
    for r in rows:
        print(f"  · [{r['citations']}引] {r['title']}")
        print(f"    https://doi.org/{r['doi']}" if r["doi"] else "    （无 DOI）")
    print(f"\n清单文件：{TO_FETCH}")


def cmd_index(args, cfg, email):
    conn = store.connect()
    rebuild_index(conn)
    rebuild_to_fetch(conn)
    print(f"已重建 {INDEX} 与 {TO_FETCH}")


def cmd_cost(args, cfg, email):
    conn = store.connect()
    total, by_tag, by_day = store.usage_totals(conn)
    if not total["calls"]:
        print("还没有 token 记录。（记录功能本次新增，之前的调用未计入；"
              "跑一次 discover/ingest/resummarize 后即可见。）")
        return
    print(f"=== 累计 token 用量（{total['calls']} 次调用）===")
    print(f"  输入 prompt     : {total['p']:>12,}")
    print(f"  输出 completion : {total['c']:>12,}")
    print(f"  合计 total      : {total['t']:>12,}")

    pricing = cfg.get("pricing", {})
    if pricing:
        print("\n--- 花费（¥，按 config [pricing]）---")
        grand, has_unknown = 0.0, False
        for r in store.usage_by_model(conn):
            price = pricing.get(r["model"])
            if not price:
                has_unknown = True
                print(f"  {r['model'][:34]:<34} 入{r['p']:>9,} 出{r['c']:>8,}   ¥ —（未配单价）")
                continue
            yuan = r["p"] / 1e6 * price[0] + r["c"] / 1e6 * price[1]
            grand += yuan
            print(f"  {r['model'][:34]:<34} 入{r['p']:>9,} 出{r['c']:>8,}   ¥{yuan:.4f}")
        tail = "（不含未配单价的模型）" if has_unknown else ""
        print(f"  合计 ≈ ¥{grand:.4f}{tail}")

    print("\n--- 按类型 ---")
    for r in by_tag:
        print(f"  {r['tag']:<16} {r['calls']:>3} 次   入 {r['p'] or 0:>10,}  "
              f"出 {r['c'] or 0:>9,}  计 {r['t'] or 0:>10,}")
    print("\n--- 按日期 ---")
    for r in by_day:
        print(f"  {r['d']}   {r['calls']:>3} 次   计 {r['t'] or 0:>10,}")


# ---------------------------------------------------------------- export
def cmd_export(args, cfg, email):
    conn = store.connect()
    rows = list(store.all_summarized(conn))
    if not rows:
        print("没有已总结的文献可导出。")
        return
    if args.limit > 0:
        rows = rows[:args.limit]
    # 转成 rec dict
    recs = [dict(r) for r in rows]
    n = citations.export_bibtex_from_records(recs, args.output)
    print(f"已导出 {n} 条 BibTeX → {args.output}")


# ---------------------------------------------------------------- validate
def cmd_validate(args, cfg, email):
    conn = store.connect()
    rows = list(store.all_summarized(conn))
    if not rows:
        print("没有已总结的文献可验证。")
        return
    if args.limit > 0:
        rows = rows[:args.limit]

    dois = [r["doi"] for r in rows if r["doi"]]
    if not dois:
        print("没有带 DOI 的文献。")
        return

    print(f"验证 {len(dois)} 条 DOI（CrossRef API）...\n")
    results = citations.validate_dois(dois)
    ok = sum(1 for v in results.values() if v["ok"])
    bad = len(dois) - ok
    for doi, info in results.items():
        if info["ok"]:
            print(f"  ✓ {doi}")
            print(f"    {info['journal']}, {info['year']}")
        else:
            print(f"  ✗ {doi}  [DOI 无效]")
    print(f"\n结果：{ok} 条有效，{bad} 条无效。")


# ---------------------------------------------------------------- review
def cmd_review(args, cfg, email):
    """基于已总结文献生成系统化综述（七阶段简化版）。"""
    conn = store.connect()
    apply_model_overrides(args)
    out_path = Path(args.output)
    print(f"生成综述：{args.query}")
    print(f"  输出：{out_path}")
    spath = review.generate_review(
        conn, args.query, out_path,
        llm_summarize_fn=deepseek.summarize,
        max_chars=cfg["fulltext"]["max_chars"],
    )
    store.log_usage(conn, deepseek.drain_usage(), ref=f"[综述] {args.query}")
    print(f"\n完成 → {spath}")


def main():
    ap = argparse.ArgumentParser(description="生物医学文献检索+精读总结 agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="检索+排序+下载OA全文+总结")
    d.add_argument("--query", help="检索式（不与 --profile 同用时必填，支持 AND/OR/括号/短语）")
    d.add_argument("--profile", help="使用 profiles.toml 里的命名检索方案")
    d.add_argument("--intent", help="给 DeepSeek 打相关度分用的描述（覆盖 profile.intent）")
    d.add_argument("--max", type=int, default=None, help="本次最多处理几篇（默认取配置 daily_max）")
    d.add_argument("--min-year", type=int, default=None)
    d.add_argument("--source", choices=["epmc", "ncbi", "all"], default="epmc",
                   help="检索源：epmc(默认) / ncbi / all(两者合并)")
    d.add_argument("--no-fulltext", action="store_true", help="只用摘要，不尝试下载全文")
    d.add_argument("--summary-model", help="覆盖本次 summary 模型，如 qwen:qwen-long")
    d.add_argument("--score-model", help="覆盖本次 score 模型，如 deepseek:deepseek-chat")
    d.set_defaults(func=cmd_discover)

    i = sub.add_parser("ingest", help="处理 inbox/ 里手动下载的 PDF")
    i.add_argument("--query", default="", help="给这批 PDF 一个关键词标签（可选）")
    i.add_argument("--summary-model", help="覆盖本次 summary 模型，如 qwen:qwen-long")
    i.add_argument("--score-model", help=argparse.SUPPRESS)
    i.set_defaults(func=cmd_ingest)

    rs = sub.add_parser("resummarize", help="用当前模板重做已有总结（不计配额）")
    rs.add_argument("--scope", choices=["abstract", "full", "all"], default="abstract",
                    help="重做范围：abstract(默认,只重做摘要笔记)/full/all")
    rs.add_argument("--doi", help="只重做指定 DOI 的一篇")
    rs.add_argument("--summary-model", help="覆盖本次 summary 模型，如 qwen:qwen-long")
    rs.add_argument("--score-model", help=argparse.SUPPRESS)
    rs.set_defaults(func=cmd_resummarize)

    rf = sub.add_parser("refetch", help="对缺全文的文献重试OA下载，下到就补全文精读")
    rf.add_argument("--summary-model", help="覆盖本次 summary 模型，如 qwen:qwen-long")
    rf.add_argument("--score-model", help=argparse.SUPPRESS)
    rf.set_defaults(func=cmd_refetch)

    q = sub.add_parser("queue", help="打印待人工下载清单")
    q.set_defaults(func=cmd_queue)

    x = sub.add_parser("index", help="重建索引文件")
    x.set_defaults(func=cmd_index)

    co = sub.add_parser("cost", help="查看累计 token 用量")
    co.set_defaults(func=cmd_cost)

    ex = sub.add_parser("export", help="导出已总结文献的 BibTeX")
    ex.add_argument("--output", default="references.bib", help="输出文件路径（默认 references.bib）")
    ex.add_argument("--limit", type=int, default=0, help="只导出最近 N 篇（0=全部）")
    ex.set_defaults(func=cmd_export)

    vl = sub.add_parser("validate", help="验证已总结文献的 DOI 有效性")
    vl.add_argument("--limit", type=int, default=0, help="只验证最近 N 篇（0=全部）")
    vl.set_defaults(func=cmd_validate)

    rv = sub.add_parser("review", help="基于已总结文献生成系统化综述")
    rv.add_argument("--query", required=True, help="综述主题（匹配已总结文献的 query/title）")
    rv.add_argument("--output", default="review.md", help="输出 markdown 路径")
    rv.add_argument("--summary-model", help="覆盖本次 summary 模型")
    rv.add_argument("--score-model", help=argparse.SUPPRESS)
    rv.set_defaults(func=cmd_review)

    args = ap.parse_args()
    cfg, email = setup(args)
    args.func(args, cfg, email)


if __name__ == "__main__":
    main()
