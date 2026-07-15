"""FastMCP stdio adapter for lit-agent.

Long-running CLI operations are serialized through one worker because lit-agent
currently keeps selected models and usage accounting in process-global state.
MCP calls therefore return a run id immediately and expose polling tools.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import threading
import traceback
import uuid

from mcp.server.fastmcp import FastMCP

import lit_agent
import publisher_routes


ROOT = Path(__file__).resolve().parent
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lit-agent-mcp")
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_CAPTURE_CHARS = 200_000

mcp = FastMCP(
    "lit-agent",
    instructions=(
        "Search, retrieve and summarize scientific literature. Long operations "
        "return run_id; poll get_run_status and then get_run_result."
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _JobStream:
    encoding = "utf-8"

    def __init__(self, run_id: str):
        self.run_id = run_id

    def write(self, value) -> int:
        text = str(value)
        with _JOBS_LOCK:
            job = _JOBS.get(self.run_id)
            if job is not None:
                job["output"] = (job["output"] + text)[-_MAX_CAPTURE_CHARS:]
        return len(text)

    def flush(self):
        return None

    def isatty(self):
        return False


def _artifact_snapshot(extra_paths: tuple[Path, ...] = ()) -> dict[str, tuple[int, int]]:
    paths: list[Path] = []
    for directory, pattern in ((ROOT / "summaries", "**/*.md"), (ROOT / "pdfs", "**/*.pdf")):
        if directory.exists():
            paths.extend(path for path in directory.glob(pattern) if path.is_file())
    paths.extend(path for path in (ROOT / "references.bib", *extra_paths) if path.is_file())
    result = {}
    for path in paths:
        try:
            stat = path.stat()
            result[str(path.resolve())] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            continue
    return result


def _run_cli_job(run_id: str):
    with _JOBS_LOCK:
        job = _JOBS[run_id]
        job["state"] = "running"
        job["started_at"] = _now()
        argv = list(job["argv"])
        extra_paths = tuple(Path(path) for path in job.get("expected_artifacts", []))
    before = _artifact_snapshot(extra_paths)
    stream = _JobStream(run_id)
    old_argv = sys.argv[:]
    old_cwd = Path.cwd()
    error = ""
    try:
        os.chdir(ROOT)
        sys.argv = [str(ROOT / "lit_agent.py"), *argv]
        with redirect_stdout(stream), redirect_stderr(stream):
            try:
                lit_agent.main()
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    raise RuntimeError(str(exc.code)) from exc
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        stream.write("\n" + traceback.format_exc())
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
    after = _artifact_snapshot(extra_paths)
    artifacts = sorted(path for path, fingerprint in after.items()
                       if before.get(path) != fingerprint)
    with _JOBS_LOCK:
        job = _JOBS[run_id]
        job["state"] = "failed" if error else "completed"
        job["error"] = error
        job["artifacts"] = artifacts
        job["finished_at"] = _now()


def _submit(operation: str, argv: list[str], expected_artifacts: tuple[Path, ...] = ()) -> dict:
    run_id = uuid.uuid4().hex
    job = {
        "run_id": run_id,
        "operation": operation,
        "argv": argv,
        "state": "queued",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "error": "",
        "output": "",
        "artifacts": [],
        "expected_artifacts": [str(path) for path in expected_artifacts],
    }
    with _JOBS_LOCK:
        _JOBS[run_id] = job
    _EXECUTOR.submit(_run_cli_job, run_id)
    return {"run_id": run_id, "operation": operation, "state": "queued"}


def _public_job(run_id: str, include_output: bool = False) -> dict:
    with _JOBS_LOCK:
        stored = _JOBS.get(run_id)
        if stored is None:
            return {"run_id": run_id, "state": "not_found",
                    "error": "Unknown run_id (run state is in-memory and resets with the MCP server)."}
        job = dict(stored)
    output = job.pop("output")
    job.pop("expected_artifacts", None)
    if include_output:
        job["output"] = output
        job["output_truncated"] = len(output) >= _MAX_CAPTURE_CHARS
    else:
        job["output_tail"] = output[-3000:]
    return job


def _add_option(argv: list[str], name: str, value):
    if value not in (None, "", 0, False):
        argv.extend([name, str(value)])


def _discover_argv(query: str = "", profile: str = "", max_papers: int = 5,
                   min_year: int = 0, source: str = "auto", no_fulltext: bool = False,
                   summary_model: str = "", score_model: str = "") -> list[str]:
    if bool(query.strip()) == bool(profile.strip()):
        raise ValueError("Provide exactly one of query or profile.")
    if not 1 <= int(max_papers) <= 50:
        raise ValueError("max_papers must be between 1 and 50.")
    if source not in {"auto", "epmc", "s2", "ncbi", "all"}:
        raise ValueError("source must be auto, epmc, s2, ncbi, or all.")
    argv = ["discover"]
    _add_option(argv, "--query", query.strip())
    _add_option(argv, "--profile", profile.strip())
    _add_option(argv, "--max", max_papers)
    _add_option(argv, "--min-year", min_year)
    _add_option(argv, "--source", source)
    _add_option(argv, "--summary-model", summary_model.strip())
    _add_option(argv, "--score-model", score_model.strip())
    if no_fulltext:
        argv.append("--no-fulltext")
    return argv


@mcp.tool()
def start_discover(query: str = "", profile: str = "", max_papers: int = 5,
                   min_year: int = 0, source: str = "auto", no_fulltext: bool = False,
                   summary_model: str = "", score_model: str = "") -> dict:
    """Start literature search, ranking, official full-text retrieval and summarization."""
    return _submit("discover", _discover_argv(
        query, profile, max_papers, min_year, source, no_fulltext,
        summary_model, score_model))


@mcp.tool()
def start_refetch(profile: str = "", query: str = "", summary_model: str = "") -> dict:
    """Retry official/OA retrieval for queued papers, then create full-text summaries."""
    if profile and query:
        raise ValueError("profile and query cannot be used together.")
    argv = ["refetch"]
    _add_option(argv, "--profile", profile.strip())
    _add_option(argv, "--query", query.strip())
    _add_option(argv, "--summary-model", summary_model.strip())
    return _submit("refetch", argv)


@mcp.tool()
def start_ingest(query: str = "", summary_model: str = "") -> dict:
    """Process PDFs already placed in lit-agent/inbox and summarize them."""
    argv = ["ingest"]
    _add_option(argv, "--query", query.strip())
    _add_option(argv, "--summary-model", summary_model.strip())
    return _submit("ingest", argv)


@mcp.tool()
def start_resummarize(scope: str = "abstract", doi: str = "",
                      summary_model: str = "") -> dict:
    """Regenerate existing summaries with an optional model override."""
    if scope not in {"abstract", "full", "all"}:
        raise ValueError("scope must be abstract, full, or all.")
    argv = ["resummarize", "--scope", scope]
    _add_option(argv, "--doi", doi.strip())
    _add_option(argv, "--summary-model", summary_model.strip())
    return _submit("resummarize", argv)


@mcp.tool()
def start_review(query: str, output_name: str = "review.md",
                 summary_model: str = "") -> dict:
    """Generate a review from stored, screened summaries."""
    if not query.strip():
        raise ValueError("query is required.")
    safe_name = "".join(ch for ch in Path(output_name).stem if ch.isalnum() or ch in "-_")[:80]
    if not safe_name:
        safe_name = "review"
    output = ROOT / "summaries" / "mcp_reviews" / f"{safe_name}.md"
    argv = ["review", "--query", query.strip(), "--output", str(output)]
    _add_option(argv, "--summary-model", summary_model.strip())
    return _submit("review", argv, (output,))


@mcp.tool()
def start_cost_report() -> dict:
    """Queue a quick token and mixed-currency cost report."""
    return _submit("cost", ["cost"])


@mcp.tool()
def get_run_status(run_id: str) -> dict:
    """Poll a queued/running job; includes a short live console-output tail."""
    return _public_job(run_id.strip(), include_output=False)


@mcp.tool()
def get_run_result(run_id: str) -> dict:
    """Return final console output and artifact paths for a run."""
    return _public_job(run_id.strip(), include_output=True)


@mcp.tool()
def list_profiles() -> dict:
    """List named search profiles available in profiles.toml."""
    profiles = lit_agent.load_profiles()
    return {"profiles": [
        {"name": name, "label": value.get("label", ""),
         "query": value.get("query", ""), "min_year": value.get("min_year")}
        for name, value in sorted(profiles.items())
    ]}


@mcp.tool()
def get_fetch_queue() -> dict:
    """Read the current missing-full-text queue artifact without starting a job."""
    path = lit_agent.TO_FETCH
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"path": str(path.resolve()), "exists": path.exists(),
            "content": content[-50_000:], "content_truncated": len(content) > 50_000}


@mcp.tool()
def get_session_status(publisher: str = "") -> dict:
    """Show cookie file/count metadata only; never returns names or values."""
    if publisher and publisher not in publisher_routes.ROUTES_BY_KEY:
        raise ValueError("Unknown publisher key. Use an empty value to list configured stores.")
    cfg = lit_agent.load_config()
    registry = lit_agent._session_registry(cfg)
    routes = ([publisher_routes.ROUTES_BY_KEY[publisher]] if publisher
              else publisher_routes.PUBLISHER_ROUTES)
    statuses = [registry.status(route.key, route.domains) for route in routes]
    if not publisher:
        statuses = [status for status in statuses if status["exists"]]
    return {"sessions": statuses, "cookie_values_included": False}


if __name__ == "__main__":
    mcp.run(transport="stdio")
