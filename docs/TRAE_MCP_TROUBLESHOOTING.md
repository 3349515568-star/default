# Trae MCP 排坑笔记

## 配置位置

Trae CN 的 MCP 配置通常在：

```text
%APPDATA%\Trae CN\User\mcp.json
```

日志通常在：

```text
%APPDATA%\Trae CN\logs\<latest>\window1\
```

重点看：

- `mcp-servers-render.log`：工具调用、结果、超时。
- `exthost\mcp-servers-host.log`：server stderr、启动参数、报错。

## python-run 卡死

现象：运行 matplotlib/numpy 任务时，图已经生成，但 Trae 调用一直转圈直到 `REQUEST_TIMEOUT`。

根因有两层：

- FastMCP 工具函数同步跑在 asyncio 事件循环线程里，在 server 进程内 `exec()` 重型导入容易卡死。
- Trae 以无控制台 Electron 子进程方式启动 MCP，子进程再用 PIPE 捕获 stdout/stderr 时，可能继承父进程管道句柄，导致调用方等不到 EOF。

解决：

- 用户代码放到独立子进程执行。
- 子进程 stdout/stderr 写临时文件，不用 PIPE。
- `stdin=subprocess.DEVNULL`，Windows 上加 `CREATE_NO_WINDOW`。
- 强制 `MPLBACKEND=Agg`，并用 `PYTHON_RUN_INTERPRETER` 指定真正装好依赖的 Python。

`mcp/servers/python-run/mcp_python_run.py` 已按这个方式实现。

## Playwright 找不到浏览器

如果 `@playwright/mcp` 报找不到 Chrome，可以先用系统 Edge：

```json
"playwright": {
  "command": "npx",
  "args": ["@playwright/mcp@latest", "--browser", "msedge"]
}
```

如果要用 Playwright 自带 Chromium，再单独运行：

```powershell
npx playwright install chromium
```

## 发布前检查

```powershell
rg -n -i "api[_-]?key|secret|token|password|bearer|authorization|cookie|[A-Z]:\\\\" .
```

真实 key、本机绝对路径、日志、输出图片都不应该进公开仓库。
