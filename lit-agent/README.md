# lit-agent — 生物医学文献检索 + 精读总结 agent

按关键词自动检索文献，下载相关度高/引用多的**开放获取**全文，用可配置 LLM 精读并写结构化中文笔记。每天有处理上限。

## 它能做 / 不能做

- ✅ 检索（Europe PMC，带引用数）→ LLM 相关度打分排序 → 取 Top N
- ✅ 全文下载级联：Unpaywall OA 定位 → 金色 OA 出版商直连(Frontiers/PLOS) → Europe PMC render，PyMuPDF 解析 → LLM 精读全文
- ✅ 拿不到全文的先出**摘要总结**并列入「待取清单」；`refetch` 可日后自动重试
- ✅ `ingest`：你用机构权限手动下的 PDF 丢进 `inbox/`，自动补全文精读

## 安装

```powershell
py -m pip install -r requirements.txt
Copy-Item .env.example .env   # 然后编辑 .env，填入 CONTACT_EMAIL 和默认模型需要的 key
```

`.env`：
```
CONTACT_EMAIL=you@example.com
DEEPSEEK_API_KEY=your-deepseek-api-key        # 默认用于 score
DASHSCOPE_API_KEY=your-dashscope-api-key      # 默认用于 summary=qwen:qwen-long
```

## 用法

```powershell
# 临时检索：直接给检索式（支持 AND/OR/括号/短语）
py lit_agent.py discover --query "GLP-1 receptor agonist cardiovascular outcomes"

# 用命名检索方案（推荐，见 profiles.toml）
py lit_agent.py discover --profile methyl-edit

# 限定本次篇数 / 起始年份
py lit_agent.py discover --profile methyl-edit --max 3 --min-year 2018

# 看哪些还缺全文需要手动下载
py lit_agent.py queue

# 对缺全文的文献自动重试 OA 下载，下到就补全文精读
py lit_agent.py refetch

# 用机构权限下好 PDF 丢进 inbox/ 后，补全文精读
py lit_agent.py ingest
py lit_agent.py ingest --summary-model deepseek:deepseek-chat

# 用当前模板重做已有总结（不计每日配额，就地覆盖）
py lit_agent.py resummarize --scope abstract   # abstract(默认)/full/all，或 --doi 指定一篇

# 查看累计 token 用量（按类型/日期）
py lit_agent.py cost

# 重建索引
py lit_agent.py index
```

中文输出乱码时：`$env:PYTHONUTF8=1` 再跑。

## 产物

```
summaries/
  INDEX.md            总索引表（日期/类型/引用/标题/链接）
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
| `intent` | 自然语言描述（决定 DeepSeek 怎么打相关度分、什么算切题） |
| `min_year` | 近作起始年份 |
| `keep_classics` | `true` 时保留更早的高被引代表性文献（按引用排序那一路不卡年份） |

已内置 `methyl-edit`：甲基转移酶 × 基因编辑/代谢工程/合成生物学/生物合成，
打分规则为「共现主题越多分越高，只把甲基化当背景给低分」。照着加自己的即可。

> 注：API 拿不到期刊影响因子(IF)，本工具用**引用数 + 期刊名**做近似。若要按真实 IF 过滤，需另配期刊 IF 对照表（可后加）。

## 配置（config.toml）

- `limits.daily_max`：每天最多处理几篇（5 或 10）
- `search.candidates` / `search.min_year`：候选数量 / 起始年份
- `rank.*`：排序权重（相关度 / 引用 / 新近度）
- `llm.max_tokens` / `llm.temperature`：输出上限 / 温度（默认 0.15，低温更忠实）
- `[models]`：每步用哪个 `provider:model`（`score` / `summary`）。改这里换默认
- `[providers.*]`：各家 OpenAI 兼容入口 + 对应 `.env` 的 key 变量名
- `fulltext.max_chars`：超长全文分块阈值

## 公开版说明

这个仓库是可公开分享的源码版，不包含本地运行数据：`.env`、`db.sqlite`、`logs/`、`pdfs/`、`papers/`、`summaries/`、`inbox/` 都被 `.gitignore` 排除。

公开版只保留开放获取下载路径。拿不到 OA 全文时，会写入待取清单；用户可用自己拥有访问权限的 PDF 通过 `inbox/` + `ingest` 流程补全文。

### 切换 / 对比模型

本工具按「步骤」选模型，且支持 DeepSeek / 通义千问 / 智谱 / 豆包 / 302.ai（均 OpenAI 兼容）。
在 `.env` 填好对应 key 后：

```powershell
# 临时用通义 Qwen-Long 跑 summary（长文友好），打分仍用默认
py lit_agent.py discover --profile methyl-edit --summary-model qwen:qwen-long
# 拿同一篇做 A/B（resummarize 不计配额）
py lit_agent.py resummarize --doi 10.x/xxxx --summary-model qwen:qwen-long
```

不建议用 reasoning 模型（R1 / QwQ / 豆包思考版）做精读——会过度延展、伤忠实度。
`py lit_agent.py cost` 可对比不同模型的 token 开销。
