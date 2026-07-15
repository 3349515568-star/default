"""sqlite 状态库：去重、每日配额、记录每篇文献的处理状态。"""
import re
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "db.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    uid            TEXT PRIMARY KEY,   -- 优先 DOI，否则 'pmid:xxxxx'
    pmid           TEXT,
    pmcid          TEXT,
    doi            TEXT,
    s2_paper_id    TEXT,
    s2_url         TEXT,
    s2_pdf_url     TEXT,
    title          TEXT,
    title_key      TEXT,   -- 归一化标题（去重用：预印本↔见刊版同名也能判重）
    journal        TEXT,
    year           INTEGER,
    citations      INTEGER,
    influential_citations INTEGER DEFAULT 0,
    authors        TEXT,
    status         TEXT,   -- summarized_full / summarized_abstract / queued / ingested
    has_fulltext   INTEGER DEFAULT 0,
    relevance      REAL,   -- LLM 相关度 0-10
    score          REAL,   -- 最终排序分
    pdf_path       TEXT,
    summary_path   TEXT,
    query          TEXT,   -- 哪个关键词检索到的
    first_seen     TEXT,
    processed_date TEXT    -- 真正出总结的日期（用于每日配额）
);
"""


USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT,
    tag               TEXT,   -- score / summary_full / summary_abstract / summary_chunk
    model             TEXT,
    ref               TEXT,   -- 关联的文献标题或检索标签
    scope             TEXT,   -- query/profile 归属，cost 按研究方向统计
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.execute(USAGE_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """给老库补新列并回填可推导字段。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(papers)")}
    if "title_key" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN title_key TEXT")
        for r in conn.execute("SELECT uid, title FROM papers").fetchall():
            conn.execute("UPDATE papers SET title_key=? WHERE uid=?",
                         (norm_title(r["title"]), r["uid"]))
    paper_additions = {
        "s2_paper_id": "TEXT",
        "s2_url": "TEXT",
        "s2_pdf_url": "TEXT",
        "influential_citations": "INTEGER DEFAULT 0",
    }
    for name, sql_type in paper_additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {name} {sql_type}")
    usage_cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage)")}
    if "scope" not in usage_cols:
        conn.execute("ALTER TABLE usage ADD COLUMN scope TEXT")
    conn.commit()


def norm_title(title: str) -> str:
    """归一化标题用于去重：转小写、只留字母数字与中日韩字，空格/标点/连字符全去掉。"""
    if not title:
        return ""
    return re.sub(r"[^0-9a-z一-鿿]+", "", title.lower())


def make_uid(rec: dict) -> str:
    doi = (rec.get("doi") or "").strip().lower()
    if doi:
        return doi
    pmid = (rec.get("pmid") or "").strip()
    if pmid:
        return f"pmid:{pmid}"
    pmcid = (rec.get("pmcid") or "").strip()
    if pmcid:
        return f"pmcid:{pmcid}"
    s2_paper_id = (rec.get("s2_paper_id") or "").strip()
    if s2_paper_id:
        return f"s2:{s2_paper_id}"
    return f"title:{norm_title(rec.get('title', ''))}"


def seen_uids(conn) -> set:
    """已经处理过（非 queued）的 uid，用于去重，避免重复精读。"""
    rows = conn.execute(
        "SELECT uid FROM papers WHERE status IN ('summarized_full','summarized_abstract','ingested')"
    ).fetchall()
    return {r["uid"] for r in rows}


def seen_title_keys(conn) -> set:
    """已处理论文的归一化标题集合（跨 DOI/源判重，挡预印本↔见刊重复）。"""
    rows = conn.execute(
        "SELECT title_key FROM papers WHERE status IN "
        "('summarized_full','summarized_abstract','ingested') "
        "AND title_key IS NOT NULL AND title_key != ''"
    ).fetchall()
    return {r["title_key"] for r in rows}


def count_today(conn) -> int:
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) c FROM papers WHERE processed_date = ?", (today,)
    ).fetchone()
    return row["c"]


def upsert(conn, rec: dict):
    """插入或更新一篇文献。rec 的键对应 papers 列。title_key 自动按标题算。"""
    rec = {**rec, "title_key": norm_title(rec.get("title", ""))}
    cols = [
        "uid", "pmid", "pmcid", "doi", "s2_paper_id", "s2_url", "s2_pdf_url",
        "title", "title_key", "journal", "year", "citations", "influential_citations",
        "authors", "status", "has_fulltext", "relevance", "score",
        "pdf_path", "summary_path", "query", "first_seen", "processed_date",
    ]
    vals = [rec.get(c) for c in cols]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "uid")
    conn.execute(
        f"INSERT INTO papers ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(uid) DO UPDATE SET {updates}",
        vals,
    )
    conn.commit()


def get(conn, uid: str):
    return conn.execute("SELECT * FROM papers WHERE uid = ?", (uid,)).fetchone()


def find_by_doi(conn, doi: str):
    doi = (doi or "").strip().lower()
    if not doi:
        return None
    return conn.execute("SELECT * FROM papers WHERE doi = ?", (doi,)).fetchone()


def all_summarized(conn):
    return conn.execute(
        "SELECT * FROM papers WHERE status IN "
        "('summarized_full','summarized_abstract','ingested') "
        "ORDER BY processed_date DESC, score DESC"
    ).fetchall()


def queued(conn):
    return conn.execute(
        "SELECT * FROM papers WHERE status='queued' ORDER BY citations DESC"
    ).fetchall()


def log_usage(conn, rows, ref="", scope=""):
    """把 llm.drain_usage() 取到的若干条 token 用量写入 usage 表。"""
    from datetime import datetime
    ts = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        conn.execute(
            "INSERT INTO usage (ts,tag,model,ref,scope,prompt_tokens,completion_tokens,total_tokens)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ts, r["tag"], r["model"], ref, scope,
             r["prompt_tokens"], r["completion_tokens"], r["total_tokens"]),
        )
    conn.commit()


def usage_totals(conn):
    """返回 (总计, 按类型, 按日期) 三组 token 汇总。"""
    total = conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) p,"
        " COALESCE(SUM(completion_tokens),0) c, COALESCE(SUM(total_tokens),0) t FROM usage"
    ).fetchone()
    by_tag = conn.execute(
        "SELECT tag, COUNT(*) calls, SUM(prompt_tokens) p, SUM(completion_tokens) c,"
        " SUM(total_tokens) t FROM usage GROUP BY tag ORDER BY t DESC"
    ).fetchall()
    by_day = conn.execute(
        "SELECT substr(ts,1,10) d, COUNT(*) calls, SUM(total_tokens) t"
        " FROM usage GROUP BY d ORDER BY d"
    ).fetchall()
    return total, by_tag, by_day


def usage_by_model(conn, since: str | None = None):
    """按模型汇总 token，可选限定起始时间。"""
    where, params = (" WHERE ts >= ?", (since,)) if since else ("", ())
    return conn.execute(
        "SELECT model, COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) p,"
        " COALESCE(SUM(completion_tokens),0) c, COALESCE(SUM(total_tokens),0) t"
        f" FROM usage{where} GROUP BY model ORDER BY p+c DESC", params
    ).fetchall()


def usage_period_total(conn, since: str):
    return conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) p,"
        " COALESCE(SUM(completion_tokens),0) c, COALESCE(SUM(total_tokens),0) t"
        " FROM usage WHERE ts >= ?", (since,)
    ).fetchone()


def usage_by_scope_model(conn):
    """按 query/profile + model 汇总；尽量为旧记录从 ref 或 papers 表回填归属。"""
    scope_expr = """
        COALESCE(
            NULLIF(scope, ''),
            CASE
                WHEN ref LIKE '[打分] %' THEN substr(ref, 6)
                WHEN ref LIKE '[综述] %' THEN substr(ref, 6)
                ELSE (SELECT NULLIF(p.query, '') FROM papers p
                      WHERE substr(p.title, 1, 60) = usage.ref LIMIT 1)
            END,
            '(历史未标注)'
        )
    """
    return conn.execute(
        f"SELECT {scope_expr} scope_name, model, COUNT(*) calls,"
        " COALESCE(SUM(prompt_tokens),0) p, COALESCE(SUM(completion_tokens),0) c,"
        " COALESCE(SUM(total_tokens),0) t FROM usage"
        " GROUP BY scope_name, model ORDER BY t DESC"
    ).fetchall()
