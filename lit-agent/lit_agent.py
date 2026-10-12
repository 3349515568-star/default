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
from collections import Counter
import math
import os
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

import citations
import llm
import europepmc
import fulltext
import ncbi
import review
import semantic_scholar
import publisher_routes
import session_store
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


def _load_seen_manifest(path: Path) -> list[dict]:
    """从文献总清单 Markdown 表格读取 DOI/题名去重指纹。"""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"本地已下载文献清单不存在：{path}")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header = None
    records = []
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header is None and "题名" in cells and "DOI" in cells:
            header = {name: i for i, name in enumerate(cells)}
            continue
        if header is None or not cells or not cells[0].isdigit():
            continue
        if max(header["题名"], header["DOI"]) >= len(cells):
            continue
        title = cells[header["题名"]].strip()
        doi = cells[header["DOI"]].strip().strip("`").lower()
        doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
        if not title and not doi:
            continue
        year = None
        if "年份" in header and header["年份"] < len(cells):
            try:
                year = int(cells[header["年份"]])
            except (TypeError, ValueError):
                pass
        rec = {"doi": doi, "title": title}
        records.append({
            "uid": store.make_uid(rec),
            "title": title,
            "title_key": store.norm_title(title),
            "authors": "",
            "year": year,
        })
    if not records:
        raise ValueError(f"本地已下载文献清单没有解析到任何记录：{path}")
    return records


def _exclude_seen_sources(candidates: list[dict], database_seen: list[dict],
                          manifest_seen: list[dict]) -> tuple[list[dict], dict]:
    """依次按 lit-agent 数据库、本地下载清单排重，并分别计数。"""
    after_database = [
        rec for rec in candidates if not store.matches_seen(rec, database_seen)
    ]
    after_manifest = [
        rec for rec in after_database if not store.matches_seen(rec, manifest_seen)
    ]
    return after_manifest, {
        "database": len(candidates) - len(after_database),
        "manifest": len(after_database) - len(after_manifest),
    }


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
        llm_cfg = cfg["llm"]
        models = dict(cfg["models"])
        if getattr(args, "score_model", None):
            models["score"] = args.score_model
        if getattr(args, "summary_model", None):
            for task in ("summary_full", "summary_abstract", "review"):
                models[task] = args.summary_model
        llm.init(providers, models, keys, llm_cfg["max_tokens"], llm_cfg["temperature"])
    sessions = cfg.get("sessions", {})
    cookie_dir = Path(sessions.get("cookie_dir", "sessions"))
    if not cookie_dir.is_absolute():
        cookie_dir = ROOT / cookie_dir
    fulltext.configure_sessions(cookie_dir, sessions.get("extra_domains", {}))
    fulltext.configure_publisher_browser(
        cfg.get("publisher_browser", {}), ROOT,
        elsevier_api_key=(os.getenv("ELSEVIER_API_KEY", "")
                           or os.getenv("ELS_API_KEY", "")),
        elsevier_inst_token=(os.getenv("ELSEVIER_INST_TOKEN", "")
                              or os.getenv("ELS_INST_TOKEN", "")),
    )
    # NCBI Entrez（PubMed / Gene / Protein 多库入口）
    ncbi_api_key = os.getenv("NCBI_API_KEY", "")
    ncbi.init(email=email, api_key=ncbi_api_key)
    semantic_scholar.init(os.getenv("S2_API_KEY", ""))
    return cfg, email


def apply_model_overrides(args):
    """命令行覆盖：--summary-model 显式覆盖所有总结/综述路由。"""
    if getattr(args, "score_model", None):
        llm.set_model("score", args.score_model)
    if getattr(args, "summary_model", None):
        for task in ("summary_full", "summary_abstract", "review"):
            llm.set_model(task, args.summary_model)


def slugify(text: str, maxlen: int = 60) -> str:
    text = re.sub(r"[^\w一-鿿 -]", "", text).strip().replace(" ", "_")
    return text[:maxlen] or "untitled"


def _summary_header(rec: dict) -> str:
    relevance = rec.get("relevance")
    try:
        relevance = float(relevance)
        if not math.isfinite(relevance) or not 0 <= relevance <= 10:
            raise ValueError("invalid relevance")
        relevance_line = f"> 最终筛选相关度：{relevance:g}/10\n"
    except (TypeError, ValueError, OverflowError):
        relevance_line = "> 最终筛选相关度：未评分（手动导入或历史记录）\n"
    fallback_line = ""
    if rec.get("selection_fallback"):
        threshold = float(rec.get("selection_threshold", 6.0))
        fallback_line = (
            f"> 选入方式：每日最低产出兜底（低于常规门槛 {threshold:g}/10）\n"
        )
    return (
        f"# {rec['title']}\n\n"
        f"> {rec.get('journal','')} · {rec.get('year','')} · "
        f"引用 {rec.get('citations',0)} · 高影响引用 {rec.get('influential_citations',0)} "
        f"· DOI: {rec.get('doi','')}\n"
        f"> 作者：{rec.get('authors','')}\n\n"
        f"{relevance_line}{fallback_line}\n"
    )


def write_summary_file(rec: dict, body: str, tag: str) -> Path:
    day_dir = SUMMARIES / date.today().isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"[{tag}]{slugify(rec['title'])}.md"
    path.write_text(_summary_header(rec) + body + "\n", encoding="utf-8")
    return path


def _merge_candidate_sources(*source_lists: list[dict]) -> list[dict]:
    """按 DOI/标题合并多检索源，同时用 S2 补齐引用数和 OA 链接。"""
    merged: list[dict] = []
    doi_index: dict[str, dict] = {}
    title_index: dict[str, dict] = {}
    for records in source_lists:
        for incoming in records:
            doi = (incoming.get("doi") or "").strip().lower()
            title_key = store.norm_title(incoming.get("title", ""))
            current = doi_index.get(doi) if doi else None
            if current is None and title_key:
                current = title_index.get(title_key)
            if current is None:
                current = dict(incoming)
                current.setdefault("influential_citations", 0)
                merged.append(current)
            else:
                for field in (
                    "pmid", "pmcid", "doi", "s2_paper_id", "s2_url", "s2_pdf_url",
                    "journal", "publisher", "year", "authors",
                ):
                    if not current.get(field) and incoming.get(field):
                        current[field] = incoming[field]
                if len(incoming.get("abstract") or "") > len(current.get("abstract") or ""):
                    current["abstract"] = incoming["abstract"]
                current["citations"] = max(
                    current.get("citations") or 0, incoming.get("citations") or 0)
                current["influential_citations"] = max(
                    current.get("influential_citations") or 0,
                    incoming.get("influential_citations") or 0)
                current["has_pdf"] = bool(current.get("has_pdf") or incoming.get("has_pdf"))
                current["is_oa"] = bool(current.get("is_oa") or incoming.get("is_oa"))
                sources = set((current.get("source") or "").split("+"))
                sources.update((incoming.get("source") or "").split("+"))
                current["source"] = "+".join(sorted(x for x in sources if x))
            if current.get("doi"):
                doi_index[current["doi"].strip().lower()] = current
            if store.norm_title(current.get("title", "")):
                title_index[store.norm_title(current["title"])] = current
    return merged


def _blacklist_reason(rec: dict, blacklist_cfg: dict) -> str | None:
    """返回候选文献命中的黑名单规则；未命中返回 None。

    DOI 前缀优先于期刊名判断，因为它通常能更准确地识别出版平台；期刊名和
    publisher 则用于 DOI/出版商元数据缺失时兜底。所有匹配均忽略大小写。
    """
    if not blacklist_cfg or not blacklist_cfg.get("enabled", True):
        return None

    def norm(value) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

    doi = norm(rec.get("doi"))
    for prefix in blacklist_cfg.get("doi_prefixes", []):
        normalized = norm(prefix)
        if normalized and doi.startswith(normalized):
            return "DOI 前缀"

    publisher = norm(rec.get("publisher"))
    for keyword in blacklist_cfg.get("publishers", []):
        normalized = norm(keyword)
        if normalized and normalized in publisher:
            return "出版商"

    journal = norm(rec.get("journal"))
    exact_journals = {norm(name) for name in blacklist_cfg.get("journals", [])}
    if journal and journal in exact_journals:
        return "期刊名"
    for prefix in blacklist_cfg.get("journal_prefixes", []):
        normalized = norm(prefix)
        if normalized and journal.startswith(normalized):
            return "期刊名前缀"
    return None


def _filter_blacklisted(candidates: list[dict], blacklist_cfg: dict
                        ) -> tuple[list[dict], dict]:
    """在 LLM 打分和全文下载之前剔除黑名单文献。"""
    kept = []
    reasons = Counter()
    blocked_titles = []
    for rec in candidates:
        reason = _blacklist_reason(rec, blacklist_cfg)
        if reason:
            reasons[reason] += 1
            blocked_titles.append(rec.get("title") or "(无标题)")
        else:
            kept.append(rec)
    return kept, {
        "blocked": len(blocked_titles),
        "reasons": dict(reasons),
        "titles": blocked_titles,
    }


def _rank_and_select(candidates: list[dict], relevance_by_index: dict[int, float],
                     rank_cfg: dict, quota: int,
                     minimum: int = 0) -> tuple[list[dict], dict]:
    """先用相关度做硬门槛，再在合格集合内用引用数和新近度重排。

    当当天尚未达到最低产出且没有候选过门槛时，从有效评分中选择最高分
    兜底。评分缺失、非有限数、越界分数始终不进入精读。
    """
    min_rel = float(rank_cfg.get("min_relevance", 6.0))
    newest = max((c.get("year") for c in candidates if c.get("year")),
                 default=date.today().year)
    eligible = []
    valid_candidates = []
    low_relevance = 0
    unscored = 0
    for i, candidate in enumerate(candidates):
        raw_relevance = relevance_by_index.get(i)
        try:
            relevance = float(raw_relevance)
        except (TypeError, ValueError, OverflowError):
            relevance = None
        if (relevance is None or not math.isfinite(relevance)
                or not 0 <= relevance <= 10):
            candidate["relevance"] = None
            candidate["score"] = None
            unscored += 1
            continue

        candidate["relevance"] = relevance
        candidate["score"] = (
            rank_cfg["w_relevance"] * relevance
            + europepmc.prelim_score(
                candidate, rank_cfg["w_citation"], rank_cfg["w_recency"], newest,
                rank_cfg.get("w_influential", 0.0))
        )
        candidate.pop("selection_fallback", None)
        candidate.pop("selection_threshold", None)
        valid_candidates.append(candidate)
        if relevance < min_rel:
            low_relevance += 1
            continue
        eligible.append(candidate)

    relevance_first = rank_cfg.get("relevance_first", True)
    if relevance_first:
        # 相关度为第一排序键；引用数/新近度只在相关度同分时破局。
        eligible.sort(key=lambda c: (c["relevance"], c["score"]), reverse=True)
    else:
        eligible.sort(key=lambda c: c["score"], reverse=True)
    selected = eligible[:max(0, quota)]
    fallback_selected = 0
    minimum = min(max(0, minimum), max(0, quota))
    if not selected and minimum and valid_candidates:
        if relevance_first:
            valid_candidates.sort(
                key=lambda c: (c["relevance"], c["score"]), reverse=True)
        else:
            valid_candidates.sort(key=lambda c: c["score"], reverse=True)
        selected = valid_candidates[:minimum]
        for candidate in selected:
            candidate["selection_fallback"] = True
            candidate["selection_threshold"] = min_rel
        fallback_selected = len(selected)
    stats = {
        "threshold": min_rel,
        "valid": len(candidates) - unscored,
        "eligible": len(eligible),
        "low_relevance": low_relevance,
        "unscored": unscored,
        "fallback_selected": fallback_selected,
    }
    return selected, stats


def _strict_audit(intent: str, candidates: list[dict],
                  initial_scores: dict[int, float], rank_cfg: dict
                  ) -> tuple[dict[int, float], dict]:
    """对初评达标文献再做一次严格复核，取两次评分的较低值。

    复核只覆盖初评达标项，因此通常比对全部候选重打一遍更省 token。
    复核缺失时按未评分淘汰，不使用可能虚高的初评兜底。
    """
    threshold = float(rank_cfg.get("min_relevance", 6.0))
    if not rank_cfg.get("verify_shortlist", True):
        return dict(initial_scores), {
            "audited": 0, "downgraded": 0, "audit_failed": 0, "skipped_small": 0,
        }

    original_indices = [
        i for i, score in initial_scores.items()
        if isinstance(score, (int, float)) and math.isfinite(float(score))
        and float(score) >= threshold
    ]
    if not original_indices:
        return dict(initial_scores), {
            "audited": 0, "downgraded": 0, "audit_failed": 0, "skipped_small": 0,
        }
    audit_min_candidates = int(rank_cfg.get("audit_min_candidates", 3))
    if len(original_indices) < audit_min_candidates:
        return dict(initial_scores), {
            "audited": 0, "downgraded": 0, "audit_failed": 0,
            "skipped_small": len(original_indices),
        }

    audit_candidates = [candidates[i] for i in original_indices]
    audit_scores = llm.score_relevance(
        intent, audit_candidates, audit=True, threshold=threshold)
    combined = dict(initial_scores)
    downgraded = 0
    audit_failed = 0
    for audit_i, original_i in enumerate(original_indices):
        audit_score = audit_scores.get(audit_i)
        if audit_score is None:
            combined.pop(original_i, None)
            audit_failed += 1
            continue
        final_score = min(float(initial_scores[original_i]), float(audit_score))
        if float(initial_scores[original_i]) >= threshold > final_score:
            downgraded += 1
        combined[original_i] = final_score
    return combined, {
        "audited": len(original_indices),
        "downgraded": downgraded,
        "audit_failed": audit_failed,
        "skipped_small": 0,
    }


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
    intent = args.intent or prof.get("intent") or query   # LLM 打分依据
    label = prof.get("label") or query                     # 显示/归档标签
    usage_scope = f"profile:{args.profile}" if args.profile else label
    min_year = (args.min_year if args.min_year is not None
                else prof.get("min_year", cfg["search"]["min_year"]))
    keep_classics = bool(prof.get("keep_classics", False))
    manifest_seen = []
    manifest_path = prof.get("seen_manifest")
    if manifest_path:
        manifest_path = Path(manifest_path)
        if not manifest_path.is_absolute():
            manifest_path = ROOT / manifest_path
        try:
            manifest_seen = _load_seen_manifest(manifest_path)
        except (OSError, ValueError) as exc:
            sys.exit(f"✗ 为避免重复精读，检索已停止：{exc}")
    conn = store.connect()
    apply_model_overrides(args)

    processed_today = store.count_today(conn)
    allowed = cfg["limits"]["daily_max"] - processed_today
    requested = args.max if args.max is not None else cfg["limits"]["daily_max"]
    quota = min(requested, allowed)
    daily_min = max(0, int(cfg["limits"].get("daily_min", 0)))
    minimum_needed = min(quota, max(0, daily_min - processed_today))
    if quota <= 0:
        print(f"今天已达每日上限（{cfg['limits']['daily_max']} 篇），明天再来或调高 daily_max。")
        return

    print(f"检索{label}：")
    print(f"  query: {query[:140]}")
    print(f"  (min_year={min_year}, keep_classics={keep_classics}, source={args.source}) ...")
    if manifest_seen:
        print(f"  本地已下载文献清单：{len(manifest_seen)} 篇")

    # ── 按 source 选检索源 ──
    auto_ncbi = args.source == "auto" and bool(os.getenv("NCBI_API_KEY", "").strip())
    if args.source in ("ncbi", "all") or auto_ncbi:
        cands_ncbi = ncbi.search_pubmed(query, cfg["search"]["candidates"], min_year)
    else:
        cands_ncbi = []
    if args.source in ("epmc", "all", "auto"):
        cands_epmc = europepmc.search(query, cfg["search"]["candidates"], min_year, keep_classics)
    else:
        cands_epmc = []
    if args.source in ("s2", "all", "auto"):
        cands_s2 = semantic_scholar.search(
            query, cfg["search"]["candidates"], min_year)
    else:
        cands_s2 = []

    # 合并去重并互相补充元数据（S2 主要补引用数/高影响引用/OA 链接）
    cands = _merge_candidate_sources(cands_epmc, cands_s2, cands_ncbi)

    print(f"  EPMC={len(cands_epmc)} S2={len(cands_s2)} NCBI={len(cands_ncbi)} "
          f"合并去重→{len(cands)} 候选")
    cands, blacklist_stats = _filter_blacklisted(cands, cfg.get("blacklist", {}))
    if blacklist_stats["blocked"]:
        reason_text = "、".join(
            f"{reason} {count}" for reason, count in blacklist_stats["reasons"].items())
        print(f"  黑名单过滤 {blacklist_stats['blocked']} 篇（{reason_text}），"
              "不会进入 LLM 打分或全文下载")
    processed = store.seen_records(conn)
    cands, seen_stats = _exclude_seen_sources(cands, processed, manifest_seen)
    if seen_stats["database"] or seen_stats["manifest"]:
        print(
            f"  历史排重：lit-agent 数据库 {seen_stats['database']} 篇，"
            f"本地已下载清单 {seen_stats['manifest']} 篇"
        )
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
    print(f"  拿到 {len(cands)} 篇新候选，LLM 正在打相关度分 ...")

    rk = cfg["rank"]
    rel = llm.score_relevance(
        intent, cands, threshold=float(rk.get("min_relevance", 6.0)))
    rel, audit_stats = _strict_audit(intent, cands, rel, rk)
    store.log_usage(conn, llm.drain_usage(), ref=f"[打分] {label}", scope=usage_scope)
    selected, score_stats = _rank_and_select(
        cands, rel, rk, quota, minimum=minimum_needed)
    print(
        f"  打分完成：有效 {score_stats['valid']}/{len(cands)}，"
        f"达标 {score_stats['eligible']} 篇（相关度 ≥ {score_stats['threshold']:g}），"
        f"低分淘汰 {score_stats['low_relevance']} 篇，"
        f"评分异常 {score_stats['unscored']} 篇"
    )
    if audit_stats["audited"]:
        print(
            f"  严格复核：{audit_stats['audited']} 篇，"
            f"降到门槛下 {audit_stats['downgraded']} 篇，"
            f"复核异常淘汰 {audit_stats['audit_failed']} 篇"
        )
    elif audit_stats["skipped_small"]:
        print(f"  严格复核：仅 {audit_stats['skipped_small']} 篇初评达标，已智能跳过以节省 token")
    if score_stats["fallback_selected"]:
        print(
            f"  ⚠ 无文献达到常规门槛；为满足每日最低 {daily_min} 篇，"
            f"兜底选择最高分 {score_stats['fallback_selected']} 篇"
        )
    if not selected:
        print("  没有文献达到精读门槛，本次不会强行精读低分文章。")
        return

    print(f"  选出 Top {len(selected)} 篇开始精读：\n")
    for rec in selected:
        uid = store.make_uid(rec)
        print(f"  · [{rec['relevance']:.0f}分/{rec['citations']}引/"
              f"{rec.get('influential_citations', 0)}高影响] {rec['title'][:70]}")

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
            body = llm.summarize(
                rec, text, label, is_full, cfg["fulltext"]["max_chars"])
        except Exception as e:
            llm.drain_usage()
            print(f"      ✗ 总结失败跳过 ({type(e).__name__})")
            continue
        store.log_usage(conn, llm.drain_usage(), ref=rec["title"][:60], scope=usage_scope)
        tag = "全文" if is_full else "摘要"
        spath = write_summary_file(rec, body, tag)
        print(f"      → {'下载全文✓' if is_full else '仅摘要(待取全文)'}  {spath.name}")

        store.upsert(conn, {
            "uid": uid, "pmid": rec["pmid"], "pmcid": rec["pmcid"], "doi": rec["doi"],
            "s2_paper_id": rec.get("s2_paper_id"), "s2_url": rec.get("s2_url"),
            "s2_pdf_url": rec.get("s2_pdf_url"),
            "title": rec["title"], "journal": rec["journal"], "year": rec["year"],
            "citations": rec["citations"],
            "influential_citations": rec.get("influential_citations", 0),
            "authors": rec["authors"],
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
        "s2_paper_id": rec.get("s2_paper_id"), "s2_url": rec.get("s2_url"),
        "s2_pdf_url": rec.get("s2_pdf_url"),
        "title": rec["title"], "journal": rec["journal"], "year": rec["year"],
        "citations": rec["citations"],
        "influential_citations": rec.get("influential_citations", 0),
        "authors": rec["authors"],
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
        pdf_meta = fulltext.pdf_file_metadata(p)
        hints = fulltext.metadata_hints(pdf_meta)
        doi = fulltext.guess_doi(text, pdf_meta)
        row = store.find_by_doi(conn, doi) if doi else None
        # 待取清单优先；匹配不上时使用 PDF metadata，最后才退回文件名。
        rec = {
            "title": (row["title"] if row else hints["title"] or p.stem),
            "journal": row["journal"] if row else hints["journal"],
            "year": row["year"] if row else hints["year"],
            "citations": row["citations"] if row else 0,
            "influential_citations": row["influential_citations"] if row else 0,
            "doi": doi or (row["doi"] if row else ""),
            "pmid": row["pmid"] if row else None,
            "pmcid": row["pmcid"] if row else None,
            "authors": row["authors"] if row else hints["authors"],
            "s2_paper_id": row["s2_paper_id"] if row else None,
            "s2_url": row["s2_url"] if row else "",
            "s2_pdf_url": row["s2_pdf_url"] if row else "",
            "relevance": row["relevance"] if row else None,
            "score": row["score"] if row else None,
        }
        query = (row["query"] if row else args.query) or rec["title"]
        try:
            body = llm.summarize(rec, text, query, True, cfg["fulltext"]["max_chars"])
        except Exception as e:
            llm.drain_usage()
            print(f"      ✗ 总结失败跳过 ({type(e).__name__})")
            continue
        store.log_usage(conn, llm.drain_usage(), ref=rec["title"][:60], scope=query)
        spath = write_summary_file(rec, body, "全文")
        # 命中已有摘要记录时，用全文版替换而不是并排保留两份。
        old_summary = row["summary_path"] if row else ""
        if old_summary and Path(old_summary).exists() and Path(old_summary).resolve() != spath.resolve():
            try:
                Path(old_summary).unlink()
            except OSError:
                pass
        # 归档 PDF
        PDFS.mkdir(exist_ok=True)
        dest = PDFS / f"{slugify(rec['title'])}.pdf"
        p.replace(dest)
        uid = store.make_uid(rec) if (rec["doi"] or rec["pmid"]) else f"ingest:{p.stem}"
        store.upsert(conn, {
            "uid": uid, "pmid": rec["pmid"], "pmcid": rec["pmcid"], "doi": rec["doi"],
            "s2_paper_id": rec.get("s2_paper_id"), "s2_url": rec.get("s2_url"),
            "s2_pdf_url": rec.get("s2_pdf_url"),
            "title": rec["title"], "journal": rec["journal"], "year": rec["year"],
            "citations": rec["citations"],
            "influential_citations": rec.get("influential_citations", 0),
            "authors": rec["authors"],
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
            body = llm.summarize(rec, text, label, is_full, cfg["fulltext"]["max_chars"])
        except Exception as e:
            llm.drain_usage()
            print(f"  ✗ 总结失败跳过 ({type(e).__name__})")
            continue
        store.log_usage(conn, llm.drain_usage(), ref=rec["title"][:60], scope=label)
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
    filter_label = ""
    if getattr(args, "profile", None):
        profiles = load_profiles()
        if args.profile not in profiles:
            sys.exit(f"✗ 没找到 profile «{args.profile}»。可用：{', '.join(profiles) or '(无)'}")
        prof = profiles[args.profile]
        filter_label = prof.get("label") or prof.get("query") or args.profile
    elif getattr(args, "query", None):
        filter_label = args.query
    usage_scope = f"profile:{args.profile}" if getattr(args, "profile", None) else filter_label
    seen, targets = set(), []
    for r in [x for x in store.all_summarized(conn) if not x["has_fulltext"]] + list(store.queued(conn)):
        if filter_label and filter_label.lower() not in (r["query"] or "").lower():
            continue
        if r["uid"] not in seen:
            seen.add(r["uid"])
            targets.append(r)
    if not targets:
        suffix = f"（筛选：{filter_label}）" if filter_label else ""
        print(f"没有缺全文的文献需要重试{suffix}。")
        return
    filter_text = f"（{filter_label}）" if filter_label else ""
    print(f"对 {len(targets)} 篇{filter_text}缺全文的文献重试 OA 下载"
          f"（先刷新元数据拿最新 PMCID）...\n")
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
            body = llm.summarize(rec, text, label, True, cfg["fulltext"]["max_chars"])
        except Exception as e:
            llm.drain_usage()
            print(f"  · 总结失败跳过：{r['title'][:45]} ({type(e).__name__})")
            continue
        store.log_usage(
            conn, llm.drain_usage(), ref=rec["title"][:60],
            scope=usage_scope or label)
        spath = write_summary_file(rec, body, "全文")
        old = r["summary_path"]                    # 删掉旧的 [摘要] 文件
        if old and Path(old).exists() and Path(old).resolve() != spath.resolve():
            try:
                Path(old).unlink()
            except OSError:
                pass
        store.upsert(conn, {
            "uid": r["uid"], "pmid": rec["pmid"], "pmcid": rec["pmcid"], "doi": rec["doi"],
            "s2_paper_id": rec.get("s2_paper_id"), "s2_url": rec.get("s2_url"),
            "s2_pdf_url": rec.get("s2_pdf_url"),
            "title": rec["title"], "journal": rec["journal"], "year": rec["year"],
            "citations": rec["citations"],
            "influential_citations": rec.get("influential_citations", 0),
            "authors": rec["authors"],
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
             "| 日期 | 类型 | 相关度 | 引用 | 高影响引用 | 年 | 标题 | 期刊 | 总结 |",
             "|---|---|---:|---:|---:|---|---|---|---|"]
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
        relevance = (f"{r['relevance']:g}/10"
                     if r["relevance"] is not None else "未评分")
        lines.append(
            f"| {r['processed_date'] or ''} | {tag.get(r['status'],'')} "
            f"| {relevance} | {r['citations'] or 0} | {r['influential_citations'] or 0} "
            f"| {r['year'] or ''} "
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


def _session_registry(cfg) -> session_store.SessionRegistry:
    sessions = cfg.get("sessions", {})
    cookie_dir = Path(sessions.get("cookie_dir", "sessions"))
    if not cookie_dir.is_absolute():
        cookie_dir = ROOT / cookie_dir
    return session_store.SessionRegistry(cookie_dir, sessions.get("extra_domains", {}))


def cmd_session_import(args, cfg, email):
    """Import a browser export without ever printing cookie names or values."""
    route = publisher_routes.ROUTES_BY_KEY[args.publisher]
    registry = _session_registry(cfg)
    destination = registry.cookie_dir / f"{route.key}.json"
    allowed = registry.allowed_domains(route.key, route.domains)
    count = session_store.import_cookie_file(args.file, destination, allowed)
    if not count:
        sys.exit("✗ 没有导入任何有效 cookie：请检查是否过期，以及 domain 是否属于该出版商。")
    print(f"已导入 {count} 个有效 cookie → {destination}")
    print("cookie 值未输出；该目录已被 .gitignore 排除。")


def cmd_session_status(args, cfg, email):
    """Show cookie counts/paths only, never cookie contents."""
    registry = _session_registry(cfg)
    routes = ([publisher_routes.ROUTES_BY_KEY[args.publisher]] if args.publisher
              else publisher_routes.PUBLISHER_ROUTES)
    found = False
    for route in routes:
        status = registry.status(route.key, route.domains)
        if not args.publisher and not status["exists"]:
            continue
        found = found or status["exists"]
        state = f"{status['valid_cookies']} 个有效 cookie" if status["exists"] else "未配置"
        print(f"{route.key:<18} {state}  {status['path']}")
    if not args.publisher and not found:
        print(f"尚未配置出版商 cookie。目录：{registry.cookie_dir}")


def cmd_index(args, cfg, email):
    conn = store.connect()
    rebuild_index(conn)
    rebuild_to_fetch(conn)
    print(f"已重建 {INDEX} 与 {TO_FETCH}")


def _estimate_cost(rows, pricing: dict) -> tuple[float, set[str]]:
    """根据按模型聚合的 token 行估算人民币花费。"""
    total = 0.0
    unknown = set()
    for row in rows:
        price = pricing.get(row["model"])
        if not price:
            unknown.add(row["model"])
            continue
        total += row["p"] / 1e6 * price[0] + row["c"] / 1e6 * price[1]
    return total, unknown


def _estimate_mixed_cost(rows, pricing: dict, pricing_usd: dict):
    """按模型分别累计人民币与美元单价，避免把中转美元价格误标成人民币。"""
    yuan = usd = 0.0
    unknown = set()
    for row in rows:
        model = row["model"]
        try:
            provider_usd = float(row["provider_usd"] or 0)
        except (KeyError, IndexError, TypeError, ValueError):
            provider_usd = 0.0
        usd += provider_usd
        price = pricing.get(model)
        if price:
            yuan += row["p"] / 1e6 * price[0] + row["c"] / 1e6 * price[1]
            continue
        price = pricing_usd.get(model)
        if price:
            usd += row["p"] / 1e6 * price[0] + row["c"] / 1e6 * price[1]
            continue
        if row["p"] or row["c"]:
            unknown.add(model)
    return yuan, usd, unknown


def _format_mixed_cost(yuan: float, usd: float) -> str:
    parts = []
    if yuan:
        parts.append(f"{yuan:.4f} 元")
    if usd:
        parts.append(f"${usd:.4f}")
    return " + ".join(parts) if parts else "0"


def _period_cost_line(name: str, token_row, model_rows, pricing: dict,
                      pricing_usd: dict | None = None) -> str:
    yuan, usd, unknown = _estimate_mixed_cost(
        model_rows, pricing, pricing_usd or {})
    provider_calls = token_row["provider_calls"] if "provider_calls" in token_row.keys() else 0
    suffix = " + 未配单价模型" if unknown else ""
    if provider_calls:
        suffix += f"（含 {provider_calls} 条服务商账单）"
    return (f"  {name:<4}  {token_row['calls']:>4} 次  token {token_row['t']:>11,}  "
            f"≈ {_format_mixed_cost(yuan, usd)}{suffix}")


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
    pricing_usd = cfg.get("pricing_usd", {})
    if pricing or pricing_usd:
        today = date.today().isoformat()
        month = date.today().replace(day=1).isoformat()
        all_model_rows = store.usage_by_model(conn)
        print("\n--- 花费摘要（按 config 人民币/美元单价分别估算）---")
        print(_period_cost_line(
            "今日", store.usage_period_total(conn, today),
            store.usage_by_model(conn, today), pricing, pricing_usd))
        print(_period_cost_line(
            "本月", store.usage_period_total(conn, month),
            store.usage_by_model(conn, month), pricing, pricing_usd))
        print(_period_cost_line(
            "累计", total, all_model_rows, pricing, pricing_usd))

        print("\n--- 累计按模型 ---")
        grand_yuan = grand_usd = 0.0
        has_unknown = False
        for r in all_model_rows:
            provider_usd = float(r["provider_usd"] or 0)
            price = pricing.get(r["model"])
            if price:
                amount = r["p"] / 1e6 * price[0] + r["c"] / 1e6 * price[1]
                grand_yuan += amount
                cost_text = f"{amount:.4f} 元"
            else:
                price = pricing_usd.get(r["model"])
                if price:
                    amount = (r["p"] / 1e6 * price[0]
                              + r["c"] / 1e6 * price[1] + provider_usd)
                    grand_usd += amount
                    cost_text = f"${amount:.4f}"
                else:
                    amount = None
            if amount is None:
                has_unknown = True
                print(f"  {r['model'][:34]:<34} 入{r['p']:>9,} 出{r['c']:>8,}   未配单价")
                continue
            billing_suffix = (f"  +{r['provider_calls']} 条账单"
                              if r["provider_calls"] else "")
            print(f"  {r['model'][:34]:<34} 入{r['p']:>9,} 出{r['c']:>8,}   "
                  f"{cost_text}{billing_suffix}")
        tail = "（不含未配单价的模型）" if has_unknown else ""
        print(f"  合计 ≈ {_format_mixed_cost(grand_yuan, grand_usd)}{tail}")

        print("\n--- 按 query/profile ---")
        scope_aliases = {
            p.get("label"): f"profile:{name}"
            for name, p in load_profiles().items() if p.get("label")
        }
        grouped = {}
        for r in store.usage_by_scope_model(conn):
            scope_name = scope_aliases.get(r["scope_name"], r["scope_name"])
            group = grouped.setdefault(scope_name, {
                "rows": [], "calls": 0, "tokens": 0,
            })
            group["rows"].append(r)
            group["calls"] += r["calls"]
            group["tokens"] += r["t"]
            group["provider_calls"] = group.get("provider_calls", 0) + r["provider_calls"]
        ranked_groups = []
        for scope_name, group in grouped.items():
            yuan, usd, unknown = _estimate_mixed_cost(
                group["rows"], pricing, pricing_usd)
            ranked_groups.append((yuan + usd, scope_name, group, yuan, usd, unknown))
        for _, scope_name, group, yuan, usd, unknown in sorted(
                ranked_groups, reverse=True):
            suffix = " + 未配单价" if unknown else ""
            if group["provider_calls"]:
                suffix += f"（含 {group['provider_calls']} 条服务商账单）"
            print(f"  {scope_name[:38]:<38} {group['calls']:>3} 次  "
                  f"token {group['tokens']:>10,}  "
                  f"≈ {_format_mixed_cost(yuan, usd)}{suffix}")

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
        llm_summarize_fn=llm.summarize,
        max_chars=cfg["fulltext"]["max_chars"],
        min_relevance=float(cfg["rank"].get("min_relevance", 6.0)),
    )
    store.log_usage(conn, llm.drain_usage(), ref=f"[综述] {args.query}", scope=args.query)
    print(f"\n完成 → {spath}")


def main():
    ap = argparse.ArgumentParser(description="生物医学文献检索+精读总结 agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="检索+排序+下载OA全文+总结")
    d.add_argument("--query", help="检索式（不与 --profile 同用时必填，支持 AND/OR/括号/短语）")
    d.add_argument("--profile", help="使用 profiles.toml 里的命名检索方案")
    d.add_argument("--intent", help="给 LLM 打相关度分用的描述（覆盖 profile.intent）")
    d.add_argument("--max", type=int, default=None, help="本次最多处理几篇（默认取配置 daily_max）")
    d.add_argument("--min-year", type=int, default=None)
    d.add_argument("--source", choices=["auto", "epmc", "s2", "ncbi", "all"], default="auto",
                   help="检索源：auto(默认 EPMC+S2，有 NCBI key 时再加 PubMed) / epmc / s2 / ncbi / all")
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
    rf.add_argument("--profile", help="只重试指定 profile 之前检索到的文献")
    rf.add_argument("--query", help="只重试 query/归档标签包含此文本的文献")
    rf.add_argument("--summary-model", help="覆盖本次 summary 模型，如 qwen:qwen-long")
    rf.add_argument("--score-model", help=argparse.SUPPRESS)
    rf.set_defaults(func=cmd_refetch)

    q = sub.add_parser("queue", help="打印待人工下载清单")
    q.set_defaults(func=cmd_queue)

    si = sub.add_parser("session-import", help="导入某出版商的浏览器 cookie（JSON/cookies.txt）")
    si.add_argument("--publisher", required=True,
                    choices=sorted(publisher_routes.ROUTES_BY_KEY))
    si.add_argument("--file", required=True, help="浏览器 cookie 导出文件")
    si.set_defaults(func=cmd_session_import)

    ss = sub.add_parser("session-status", help="查看出版商 cookie 文件和有效数量（不显示值）")
    ss.add_argument("--publisher", choices=sorted(publisher_routes.ROUTES_BY_KEY))
    ss.set_defaults(func=cmd_session_status)

    x = sub.add_parser("index", help="重建索引文件")
    x.set_defaults(func=cmd_index)

    co = sub.add_parser("cost", help="查看今日/本月/累计及按研究方向分组的 token 与费用")
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
    try:
        args.func(args, cfg, email)
    finally:
        # Playwright/CloakBrowser objects must close on their creator thread.
        fulltext.close_publisher_browsers()


if __name__ == "__main__":
    main()
