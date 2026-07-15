# lit-agent — 生物医学文献检索 + 精读总结 agent

按关键词自动检索文献，优先下载相关度高/引用多的开放获取全文，并可使用用户自有的机构授权与出版商 API 获取有权访问的内容；随后用可配置 LLM 精读并写结构化中文笔记。每天有处理上限。

## 它能做 / 不能做

- ✅ 检索（默认 Europe PMC + Semantic Scholar；有 NCBI key 时再加 PubMed）→ LLM 相关度打分排序 → 取 Top N
- ✅ 可配置期刊/出版商黑名单，在 LLM 打分和 PDF 下载前直接剔除（默认屏蔽 Frontiers 与 OMICS 关联网络）
- ✅ Semantic Scholar 补充总引用数、高影响引用数和合法 OA PDF 直链
- ✅ 全文下载级联：Unpaywall / Semantic Scholar / Europe PMC 合法 OA → 出版商官方 PDF 路由（可加载机构访问 cookie）→ 原有兜底，PyMuPDF 解析 → LLM 精读全文
- ✅ 出版商 cookie/session 持久化：按出版商隔离，支持浏览器 JSON 与 Netscape `cookies.txt`，过滤过期和越域 cookie
- ✅ MDPI / Elsevier 特化：Elsevier 官方 API 服务端优先，随后用可视化 CloakBrowser 处理 ScienceDirect 登录与 MDPI 反爬页面
- ✅ FastMCP stdio 服务：可从 Trae 启动检索、补取、导入、重总结和综述任务，并用 `run_id` 查询长任务状态
- ✅ 拿不到全文的先出**摘要总结**并列入「待取清单」；`refetch` 可日后自动重试
- ✅ `ingest`：你用机构权限手动下的 PDF 丢进 `inbox/`，自动补全文精读

## 安装

```powershell
py -m pip install -r requirements.txt
# 可选预下载 CloakBrowser Chromium（当前 Windows 免费构建约 535MB，版本会变化）
py -m cloakbrowser install
Copy-Item .env.example .env   # 然后编辑 .env，填入 CONTACT_EMAIL 和默认模型需要的 key
```

`.env`：
```
CONTACT_EMAIL=you@example.com
DEEPSEEK_API_KEY=your-deepseek-api-key        # 默认用于 score
DASHSCOPE_API_KEY=your-dashscope-api-key      # 默认用于 summary=qwen:qwen-long
S2_API_KEY=                                   # 可选；留空使用 Semantic Scholar 匿名共享额度
ELSEVIER_API_KEY=                             # 可选；Elsevier Article Retrieval API
ELSEVIER_INST_TOKEN=                          # 可选；高权限 token，只进入服务端请求
```

Semantic Scholar 大多数端点可匿名使用，但匿名请求共享限流且高峰期可能被节流；
`S2_API_KEY` 不是必填项。lit-agent 每次检索只发一个批量请求，遇到 HTTP 429 会遵循 `Retry-After` 退避。

## 用法

```powershell
# 临时检索：直接给检索式（支持 AND/OR/括号/短语）
py lit_agent.py discover --query "GLP-1 receptor agonist cardiovascular outcomes"

# 用命名检索方案（推荐，见 profiles.toml）
py lit_agent.py discover --profile berberine

# 限定本次篇数 / 起始年份
py lit_agent.py discover --profile berberine --max 3 --min-year 2020

# 看哪些还缺全文需要手动下载
py lit_agent.py queue

# 对缺全文的文献自动重试 OA 下载，下到就补全文精读
py lit_agent.py refetch

# 用机构权限下好 PDF 丢进 inbox/ 后，补全文精读
py lit_agent.py ingest
py lit_agent.py ingest --summary-model deepseek:deepseek-chat

# 用当前模板重做已有总结（不计每日配额，就地覆盖）
py lit_agent.py resummarize --scope abstract   # abstract(默认)/full/all，或 --doi 指定一篇

# 查看今日/本月/累计花费，以及按 query/profile 分组的花费
py lit_agent.py cost

# 重建索引
py lit_agent.py index
```

### 使用机构访问 cookie

只导入你有权访问的出版商会话。浏览器 cookie 是明文凭据；不要提交、发送或粘贴到日志中。`sessions/` 已加入 `.gitignore`，状态命令只显示路径和有效数量，不显示 cookie 名和值。

```powershell
# 支持常见浏览器 JSON 导出，以及 Netscape cookies.txt
py lit_agent.py session-import --publisher elsevier --file "D:\Downloads\elsevier-cookies.json"

# 检查全部已配置 session，或用 --publisher 只看一个
py lit_agent.py session-status
py lit_agent.py session-status --publisher elsevier
```

下载时会先尝试合法 OA 仓储副本，再走对应出版商官方路由。cookie 只装入该出版商独立的 `requests.Session`，并按 domain 匹配发送。如果校园代理改写了域名，可在 `config.toml` 的 `[sessions.extra_domains]` 下为单个出版商显式加入；不要把同一个代理域名全局共享。

导出 cookie 只能复用 HTTP 会话。一般出版商遇到必须执行 JavaScript、验证码或多步 SSO 时，仍可使用浏览器下载后放入 `inbox/`；MDPI 与 Elsevier 已有下面的 CloakBrowser 特化通道。

### MDPI / Elsevier 可视化浏览器通道

`config.toml` 默认启用 `[publisher_browser]`，且只处理 `mdpi` 与 `elsevier`：

- 下载级联仍先尝试 Unpaywall、Semantic Scholar 与 Europe PMC 等合法 OA 副本。
- Elsevier DOI 随后优先调用官方 [Article Retrieval API](https://dev.elsevier.com/documentation/ArticleRetrievalAPI.wadl)，请求 `application/pdf`；API 未配置或没有 PDF 权限时，打开 ScienceDirect。
- MDPI 不先用裸 `requests` 触发 bot 检测，直接打开 CloakBrowser。
- CloakBrowser 固定以 `headless=false`、`humanize=true` 启动。首次需要登录或确认挑战时，会保留可视窗口 120 秒供操作。
- `browser_profiles/elsevier` 与 `browser_profiles/mdpi` 持久保存 cookie、localStorage 与缓存，并已被 Git 忽略；同一任务内会复用窗口。
- 已导入到 `sessions/<publisher>` 的合规 cookie 会按原 domain 隔离后同步到相应浏览器 profile，cookie 值不会写日志。

Elsevier 官方要求机构 token 仅保存在服务端，因此 `ELSEVIER_INST_TOKEN` 不会注入浏览器、页面脚本或 URL。可选的 `CLOAKBROWSER_LICENSE_KEY` 与 `CLOAKBROWSER_PROXY` 放在 `.env`；没有 license key 时使用 CloakBrowser 当前公开免费二进制。浏览器只复用用户已有的合法访问权，不替代出版商授权。

为兼容 Elsevier Data Fetcher 的命名，程序也识别 `ELS_API_KEY` / `ELS_INST_TOKEN`；同类变量同时存在时优先使用 README 和 `.env.example` 中的 `ELSEVIER_*` 名称。

CloakBrowser 不代替 CAPTCHA 求解器。当前免费 v146 在本机 ScienceDirect 实测仍会显示人工验证页，因此保留可视窗口和交互等待；MDPI 实测已能自动解析落地页并取得真实 PDF。更新版/Pro 可能减少挑战，但不能保证出版商永不调整检测策略。

中文输出乱码时：`$env:PYTHONUTF8=1` 再跑。

## 产物

```
summaries/
  INDEX.md            总索引表（相关度/引用/高影响引用/标题/链接）
  _to_fetch.md        待人工下载全文清单（带 DOI 链接）
  2026-06-13/         按天分文件夹，每篇一个 [全文]/[摘要] 笔记.md
pdfs/                 下载/归档的开放获取 PDF
db.sqlite             去重 + 每日配额 + 状态
inbox/                你手动丢付费 PDF 进来的地方
```

## 命名检索方案（profiles.toml）

把可复用的检索策略存成 profile，一句 `--profile <名字>` 就能跑。每个 profile：

| 字段 | 作用 |
|---|---|
| `query` | Europe PMC 布尔检索式（决定召回什么） |
| `intent` | 自然语言描述（决定 LLM 怎么打相关度分、什么算切题） |
| `min_year` | 近作起始年份 |
| `keep_classics` | `true` 时保留更早的高被引代表性文献（按引用排序那一路不卡年份） |

已内置 `berberine`：小檗碱本体、盐形、代谢物/衍生物和来源药材。
打分会优先保留直接研究小檗碱药理、机制、药代、临床、制剂或合成的文献，
并压低仅把小檗碱作为复方多成分之一或仅研究来源植物的文献。

> 注：API 拿不到期刊影响因子(IF)，本工具用**引用数 + 期刊名**做近似。若要按真实 IF 过滤，需另配期刊 IF 对照表（可后加）。

## 配置（config.toml）

- `limits.daily_max`：每天最多处理几篇（5 或 10）
- `search.candidates` / `search.min_year`：候选数量 / 起始年份
- `[blacklist]`：按 DOI 前缀、出版商关键词、期刊全名或期刊名前缀过滤。默认屏蔽用户指定的 Frontiers，以及 OMICS、iMedPub、Allied Academies、Pulsus/Trade Science、SciTechnol、EuroSciCon 等关联网络；新增平台时直接向对应列表追加
- `rank.min_relevance`：精读的相关度硬门槛（默认 6）；未达标或评分异常的文献不会被引用数“救回”
- `rank.verify_shortlist`：默认对初评达标文献做严格二次复核，取两次较低分；若入围文献很多，可接近增加一轮打分 token，设为 `false` 可关闭
- `rank.audit_min_candidates`：默认为 3，即初评只有 1-2 篇达标时自动跳过二轮复核
- `rank.relevance_first`：默认相关度绝对优先，引用数/新近度只用来排同分文献；设为 `false` 可恢复综合分直接排序
- `rank.w_*`：综合分中相关度 / 引用 / 新近度的权重
- 精读笔记文件头和 `summaries/INDEX.md` 会写入经严格复核后的最终 0-10 分；总结模型不再另行打一套分
- `llm.max_tokens` / `llm.temperature`：输出上限 / 温度（默认 0.15，低温更忠实）
- `[models]`：每步用哪个 `provider:model`（`score` / `summary`）。改这里换默认
- `[providers.*]`：各家 OpenAI 兼容入口 + 对应 `.env` 的 key 变量名
- `fulltext.max_chars`：超长全文分块阈值；默认让典型论文整篇一次输入，超出后按 PDF 页边界分块
- `[publisher_browser]`：MDPI/Elsevier 可视化路由、profile 目录、页面超时和首次交互等待时间

## Trae / MCP

安装依赖后，在 Trae 的 MCP 配置中加入一个 stdio server（Windows JSON 路径中的反斜杠需要写成 `\\`）：

```json
{
  "mcpServers": {
    "lit-agent": {
      "command": "D:\\python\\python.exe",
      "args": ["E:\\血染\\default\\lit-agent\\mcp_server.py"]
    }
  }
}
```

服务提供：

- `start_discover`、`start_refetch`、`start_ingest`、`start_resummarize`、`start_review`
- `get_run_status`、`get_run_result`（长任务轮询；服务重启后内存中的 `run_id` 会失效）
- `list_profiles`、`get_fetch_queue`、`start_cost_report`、`get_session_status`

例如“检索 CRISPR 相关的 5 篇 2024 年以来综述并总结”，客户端先调用 `start_discover(query=..., max_papers=5, min_year=2024)`，拿到 `run_id` 后轮询状态，完成时从结果里的 `artifacts` 打开生成文件。所有 CLI/LLM 任务在服务内串行执行，避免模型覆盖与 usage 统计互相污染。cookie 导入故意不开放为 MCP tool，请在本机 CLI 完成。

## 设计借鉴与致谢

本轮 session 持久化、出版商 PDF 路由知识和 FastMCP 适配层的设计，参考了以下两个 InstSci 项目：

- [deathcats4/instsci-workflow](https://github.com/deathcats4/instsci-workflow)：重点参考 `session_store.py`、出版商 profile/router 的职责拆分，以及 MCP 工具封装方式。
- [Rimagination/instsci](https://github.com/Rimagination/instsci)：重点参考 `CookieStore`、`publisher_profiles.py`、`publisher_pdf_router.py` 与 `mcp_server.py` 的模块边界。

lit-agent 沿用自身的 CLI、下载级联、存储和 LLM 调度架构，针对当前代码独立适配；没有把 InstSci 整体架构或包结构直接搬入。MDPI/Elsevier 的可视化通道使用 [CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) 作为运行时依赖。许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 公开版说明

这个仓库是可公开分享的源码版，不包含本地运行数据：`.env`、`db.sqlite`、`logs/`、`pdfs/`、`papers/`、`summaries/`、`inbox/`、`sessions/`、`browser_profiles/` 都被 `.gitignore` 排除。

公开仓库不包含 cookie 或付费全文。源码支持合法 OA 来源、用户自行提供且有权使用的出版商会话，以及原有可配置兜底；拿不到全文时会写入待取清单，也可将自己有权访问的 PDF 放入 `inbox/` 后运行 `ingest`。

### 切换 / 对比模型

本工具按「任务」选模型，且支持 DeepSeek / 通义千问 / 智谱 / 豆包 / 302.ai / Ofox（均 OpenAI 兼容）。
在 `.env` 填好对应 key 后：

```powershell
# 临时用通义 Qwen-Long 跑 summary（长文友好），打分仍用默认
py lit_agent.py discover --profile berberine --summary-model qwen:qwen-long
# 拿同一篇做 A/B（resummarize 不计配额）
py lit_agent.py resummarize --doi 10.x/xxxx --summary-model qwen:qwen-long
# 只在少数重点论文上启用高质量终稿模型；不会改变全局默认模型
py lit_agent.py resummarize --scope full --doi 10.x/xxxx --summary-model ofox:openai/gpt-5.6-luna
```

不建议用 reasoning 模型（R1 / QwQ / 豆包思考版）做精读——会过度延展、伤忠实度。
`py lit_agent.py cost` 可查看今日、本月、累计以及按 query/profile 和模型分组的人民币/美元开销。
