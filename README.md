# Trae Config Export

一份从本地 Trae 工作区筛选出来的可复用配置包，包含通用 skills、MCP server 示例和 agent prompt 模板。

## 收录内容

- `mcp/`：Trae MCP 示例配置和 4 个本地 MCP server。
- `skills/`：通用工作流 skills，例如代码审查、系统化调试、文档处理、PPT、Mermaid、科研写作等。
- `agent-prompts/`：从原始 agent prompt 中提炼出来的通用模板，不包含私人角色设定。
- `docs/`：Trae MCP 排坑笔记，已去除本机绝对路径和高风险配置。

## 已排除内容

- `.trae/skills/*.md` 中的大量角色卡、世界观、剧情设定和成人/私人变体。
- `.trae/agents/*_agent_prompt.md` 的原始角色扮演提示词。
- 下载版权文献等高风险 MCP 配置。
- 运行日志、截图、缓存、输出目录和任何真实 API key。

## 使用方式

1. 复制 `mcp/mcp.json.example` 到 Trae 的 MCP 配置位置。
2. 把 `<REPO>`, `<WORKSPACE>`, `<PYTHON_EXE>` 等占位符替换为你自己的路径。
3. API key 建议只放在本地 Trae 配置或系统环境变量里，不要提交到 Git。
4. 修改 MCP server 代码或 `mcp.json` 后，在 Trae 里关闭再打开对应 MCP，或重启 Trae。

## 安全说明

这份导出包保留的是“配置方法”和“可复用工作流”，不是完整私人工作区备份。上传公开仓库前仍建议运行：

```powershell
rg -n -i "api[_-]?key|secret|token|password|bearer|authorization|cookie|[A-Z]:\\\\" .
```
