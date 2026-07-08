"""
MCP Server: Python Code Execution + Matplotlib Chart Output
Used by 河城荷取 (Nitori) agent for precise data-driven chart generation.

实现要点（踩过的坑，勿改回去）：
1. 用户代码一律在**独立子进程**里执行，不在 server 进程内 exec。
   原因：FastMCP 直接在 asyncio 事件循环线程上同步执行工具函数，若在该线程里
   import numpy/matplotlib（加载 C 扩展 .pyd），Windows+Py3.14 会死锁。
2. 子进程的 stdout/stderr **重定向到临时文件**，不用管道(PIPE)。
   原因：Trae 把 server 当无控制台的子进程启动、stdio 是异步管道；再用 PIPE 抓
   子进程输出时，子进程会继承父进程的管道句柄，导致父进程读管道永远收不到 EOF、
   subprocess 卡死（子进程其实早跑完、图也存了）。写文件则完全没有管道，不会死锁。
3. stdin=DEVNULL + CREATE_NO_WINDOW：不继承 Trae 的 stdin 管道、不分配控制台。
"""
import os
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("python-run")

OUTPUT_DIR = Path(os.environ.get("PYTHON_RUN_OUTPUT_DIR", "./python_output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 执行用户代码的解释器：默认就是本 server 用的 python（sys.executable），
# 可用环境变量显式覆盖，确保用的是装了 numpy/matplotlib 的那个。
INTERPRETER = os.environ.get("PYTHON_RUN_INTERPRETER") or sys.executable

# 子进程超时（秒）
RUN_TIMEOUT = int(os.environ.get("PYTHON_RUN_TIMEOUT", "120"))

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".svg", ".pdf")

# Windows：子进程不分配控制台窗口
_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log(msg: str) -> None:
    """诊断信息打到真实 stderr —— Trae 会记进 mcp-servers-host.log。"""
    print(f"[python-run] {msg}", file=sys.__stderr__, flush=True)


def _child_env() -> dict:
    env = dict(os.environ)
    env["MPLBACKEND"] = "Agg"          # 无界面后端，禁止弹 GUI 窗口
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONSTARTUP", None)
    return env


def _run_in_subprocess(script_path: Path, workdir: Path) -> tuple[str, str, object]:
    """在子进程里执行 script_path，cwd=workdir。
    stdout/stderr 写到 workdir 下的临时文件（不走管道，避免句柄继承死锁）。
    返回 (stdout, stderr, returncode|None)。"""
    out_path = workdir / "__stdout.txt"
    err_path = workdir / "__stderr.txt"
    rc: object = None
    timed_out = False
    with open(out_path, "wb") as o, open(err_path, "wb") as e:
        try:
            proc = subprocess.run(
                [INTERPRETER, str(script_path)],
                cwd=str(workdir),
                env=_child_env(),
                stdin=subprocess.DEVNULL,
                stdout=o,
                stderr=e,
                timeout=RUN_TIMEOUT,
                creationflags=_CREATIONFLAGS,
                close_fds=True,
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
    out = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
    err = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
    out_path.unlink(missing_ok=True)
    err_path.unlink(missing_ok=True)
    if timed_out:
        err += f"\n[超时] 子进程执行超过 {RUN_TIMEOUT}s 已被终止。解释器: {INTERPRETER}"
    return out, err, rc


def _collect_images(src_dir: Path) -> list[str]:
    saved = []
    for f in src_dir.glob("*"):
        if f.suffix.lower() in IMAGE_EXTS:
            dest = OUTPUT_DIR / f.name
            shutil.move(str(f), str(dest))
            saved.append(str(dest))
    return saved


def _format(out: str, err: str, saved: list[str]) -> str:
    parts = []
    if out.strip():
        parts.append(f"[stdout]\n{out}")
    if err.strip():
        parts.append(f"[stderr]\n{err}")
    if saved:
        parts.append("[saved files]\n" + "\n".join(saved))
    return "\n\n".join(parts) or "(no output)"


@mcp.tool()
def run_python(code: str) -> str:
    """执行 Python 代码（在独立子进程中），返回 stdout 输出。
    matplotlib 图表用 plt.savefig('name.png') 保存后会自动收集到输出目录。

    Args:
        code: 要执行的 Python 代码字符串
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="py_mcp_", dir=str(OUTPUT_DIR)))
    try:
        script = tmpdir / "_run.py"
        script.write_text(code, encoding="utf-8")
        out, err, _ = _run_in_subprocess(script, tmpdir)
        script.unlink(missing_ok=True)
        saved = _collect_images(tmpdir)
        return _format(out, err, saved)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@mcp.tool()
def run_python_file(file_path: str) -> str:
    """执行指定路径的 Python 文件（在独立子进程中）。用于执行已写好的绘图脚本。

    Args:
        file_path: Python 文件的绝对路径
    """
    fp = Path(file_path)
    if not fp.exists():
        return f"错误：文件不存在: {fp}"
    out, err, _ = _run_in_subprocess(fp, fp.parent)
    return _format(out, err, [])


@mcp.tool()
def get_output_dir() -> str:
    """返回图表输出目录的路径"""
    return str(OUTPUT_DIR)


if __name__ == "__main__":
    _log(f"starting | sys.executable={sys.executable} | INTERPRETER={INTERPRETER} | "
         f"python={sys.version.split()[0]} | timeout={RUN_TIMEOUT}s | output={OUTPUT_DIR}")
    mcp.run(transport="stdio")
