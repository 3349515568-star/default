"""多 provider LLM 客户端（模块名 deepseek 为历史遗留）：
候选文献相关度打分 + 全文/摘要的结构化精读总结。

按「步骤」选模型：score / summary 可分别指向不同 provider:model，方便 A/B 与控本。
各 provider 均走 OpenAI 兼容接口（DeepSeek / 通义 Qwen / 智谱 GLM / 豆包 Ark / 302.ai）。
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path
from openai import OpenAI, APIError

_clients = {}      # provider 名 -> OpenAI client（仅为填了 key 的 provider 建）
_step_model = {}   # 步骤名 -> (provider, model)
_providers = {}    # 原始 providers 配置（set_model 校验用）
_defaults = {}     # max_tokens / temperature
_usage = []        # 每次 _chat 的 token 用量，待 lit_agent 取走落库
_LOG_DIR = Path(__file__).parent / "logs"


def _log_raw(tag: str, model: str, content: str, error: str = ""):
    """把每次 LLM 调用的原始响应写到 logs/ 下按日期分文件，方便排查空返回/截断问题。"""
    try:
        _LOG_DIR.mkdir(exist_ok=True)
        path = _LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"
        ts = datetime.now().isoformat(timespec="seconds")
        flag = f" [ERROR: {error}]" if error else ""
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {ts} [{tag}] {model}{flag} ---\n{content}\n")
    except OSError:
        pass


def init(providers: dict, models: dict, keys: dict, max_tokens: int, temperature: float):
    """providers: {名:{base_url,key_env}}；models: {步骤:'provider:model'}；keys: {provider名:key或None}。"""
    global _clients, _step_model, _providers, _defaults
    _providers = providers
    _defaults = {"max_tokens": max_tokens, "temperature": temperature}
    _clients = {
        name: OpenAI(api_key=keys.get(name), base_url=p["base_url"])
        for name, p in providers.items() if keys.get(name)
    }
    _step_model = {}
    for step, spec in models.items():
        _set(step, spec)


def _set(step: str, spec: str):
    prov, _, model = spec.partition(":")
    if prov not in _clients:
        env = _providers.get(prov, {}).get("key_env", "?")
        raise SystemExit(f"✗ 步骤「{step}」要用 provider «{prov}»，但没它的 key"
                         f"（请在 .env 填 {env}，或在 config.toml [models] 改回 deepseek）")
    _step_model[step] = (prov, model)


def set_model(step: str, spec: str):
    """运行时覆盖某步骤的模型（命令行 --summary-model / --score-model 用）。"""
    _set(step, spec)


def drain_usage() -> list:
    """取走并清空累计的 token 用量记录（lit_agent 落库用）。"""
    global _usage
    rows, _usage = _usage, []
    return rows


def _chat(messages, step="summary", json_mode=False, max_tokens=None, tag="chat"):
    prov, model = _step_model[step]
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": _defaults["temperature"],
        "max_tokens": max_tokens or _defaults["max_tokens"],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = None
    last_err = None
    for attempt in range(3):                       # 退避重试：API 抽风/网络抖动/空返回
        try:
            resp = _clients[prov].chat.completions.create(**kwargs)
        except APIError as e:                      # API 层错误（401/429/500...）
            last_err = f"APIError: {e}"
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
            continue
        except Exception as e:                     # 网络层（连接/超时）等
            last_err = f"{type(e).__name__}: {e}"
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
            continue
        if resp.choices[0].message.content:        # 空内容当瞬时失败，继续重试
            break
        last_err = "empty content"
        if attempt < 2:
            time.sleep(3 * (attempt + 1))
    content = resp.choices[0].message.content if resp else ""
    _log_raw(tag, model, content, last_err if not content else "")
    u = getattr(resp, "usage", None) if resp else None
    if u is not None:
        _usage.append({
            "tag": tag, "model": model,
            "prompt_tokens": u.prompt_tokens,
            "completion_tokens": u.completion_tokens,
            "total_tokens": u.total_tokens,
        })
    return content


def _extract_json(text: str):
    """从模型响应里解析 JSON。容错：去 markdown 围栏、抠 {...} 子串、处理空响应。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    if not text:
        raise ValueError("模型返回空内容")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 模型可能在 JSON 前后加了说明，或输出被截断；尝试抠最长的 {...} 子串
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
        raise


def _clean(body: str) -> str:
    """去掉模型可能加的开场白/客套话，从第一个 ## 标题开始。"""
    idx = body.find("## ")
    return body[idx:].strip() if idx != -1 else body.strip()


def score_relevance(query: str, candidates: list[dict]) -> dict[int, float]:
    """对一批候选（标题+摘要）按与 query 的相关度打 0-10 分。返回 {索引: 分数}。

    候选较多时分批调用，避免单次输出过长被截断或返回空内容。
    内置评分锚定(rubric) + few-shot 示例 + 逐条理由(reason)，
    三层约束面向弱模型（V4 Flash）也能打出合理分。
    某批解析失败时给该批默认中等分(5.0)，不阻塞整体流程。
    """
    if not candidates:
        return {}
    BATCH = 15
    out: dict[int, float] = {}
    for start in range(0, len(candidates), BATCH):
        batch = candidates[start:start + BATCH]
        try:
            out.update(_score_one_batch(query, batch, start))
        except Exception:
            # 这批失败（空返回/截断/解析失败/API 抽风）：给默认中等分，不崩
            for i in range(len(batch)):
                out[start + i] = 5.0
    return out


_SCORE_RUBRIC = (
    "评分标准（严格按此打分，不要凭感觉）：\n"
    "· 9-10 分：论文核心主题与研究目标完全一致，摘要中明确出现目标关键词及其组合。\n"
    "· 6-8 分：论文部分涉及研究目标，但非核心主题；或只命中一个子主题。\n"
    "· 3-5 分：论文仅在背景/讨论中一笔带过相关概念，非研究对象。\n"
    "· 0-2 分：论文主题与研究方向无关，或你无法从摘要中判断任何关联。"
)


def _score_one_batch(query: str, batch: list[dict], offset: int) -> dict[int, float]:
    """对一批候选打分，返回 {全局索引: 分数}。批内用本地 0..n-1 编号，映射回 offset+i。"""
    items = []
    for i, c in enumerate(batch):
        abstract = (c["abstract"] or "")[:1500]
        items.append(f"[{i}] 标题：{c['title']}\n摘要：{abstract}")
    body = "\n\n".join(items)

    prompt = (
        f"你是生物医学文献的评审专家。请根据以下研究需求，"
        f"逐篇判断 {len(batch)} 篇候选文献的相关度。\n\n"
        f"=== 研究需求 ===\n{query}\n\n"
        f"=== {_SCORE_RUBRIC}\n\n"
        f"=== 评分示例（演示评分逻辑） ===\n"
        f"示例1 — 研究需求：「甲基转移酶 methyltransferase / methylase（或去甲基化酶 demethylase）与基因编辑/代谢工程/合成生物学/生物合成的结合」。\n"
        f"  · 候选标题：\"CRISPR/dCas9 DNA methylation editing is heritable during human hematopoiesis and shapes immune progeny\"\n"
        f"  · → {{\"reason\": \"dCas9-DNMT3A3L融合蛋白=甲基转移酶×CRISPR基因编辑，在造血干细胞中靶向写入DNA甲基化并跨谱系遗传，两个核心主题完全命中\", \"score\": 10}}\n"
        f"示例2 — 研究需求：「甲基转移酶 methyltransferase / methylase（或去甲基化酶 demethylase）与基因编辑/代谢工程/合成生物学/生物合成的结合」。\n"
        f"  · 候选标题：\"A novel antibiotic resistance gene identified in soil metagenomes\"\n"
        f"  · → {{\"reason\": \"抗生素耐药基因的土壤宏基因组研究，无甲基转移酶，无基因编辑，无代谢工程/合成生物学\", \"score\": 0}}\n\n"
        f"=== 候选文献 ===\n{body}\n\n"
        f'逐篇给出，JSON 格式：{{"scores": [{{"i": 0, "reason": "...", "score": 8}}, ...]}}，覆盖全部 {len(batch)} 篇。'
    )
    # 推理模型（如 deepseek-v4-pro）的 reasoning_tokens 会吃掉 max_tokens 预算，
    # 导致实际 JSON 输出被截断；解析失败时翻倍 max_tokens 重试，留足 reasoning 余量。
    base_tok = 300 + len(batch) * 150
    data = None
    last_exc = None
    for attempt in range(3):
        max_tok = base_tok * (2 ** attempt)
        raw = _chat(
            [{"role": "user", "content": prompt}],
            step="score",
            json_mode=True,
            max_tokens=max_tok,
            tag="score",
        )
        try:
            data = _extract_json(raw)
            break
        except (json.JSONDecodeError, ValueError) as e:
            last_exc = e
    if data is None:
        raise last_exc
    out = {}
    for item in data.get("scores", []):
        try:
            out[offset + int(item["i"])] = float(item["score"])
        except (KeyError, ValueError, TypeError):
            continue
    return out


SUMMARY_SYSTEM = (
    "你是一名严谨的生物化学与分子生物学科研助理。请用简体中文写高密度文献精读报告，"
    "忠于原文、不编造数据。你的第一任务是先判断文献类型：原创研究论文、综述性论文、"
    "或无法明确判断；随后只执行对应类型的报告结构。关键数值（样本量、效应量、HR/OR/RR、"
    "置信区间、p 值，或产量、效率、表达倍数、动力学参数等定量指标）务必照抄原文。"
    "严格区分「原文事实」「作者解释」「基于原文证据的短评」；不要进行开放式推理，"
    "不要把背景常识、外部知识或猜测伪装成原文结论。"
    "如果某项信息原文没有，写「原文未提及」。不要输出隐藏思维链；只输出最终报告。"
    "直接从第一个 ## 标题开始输出，不要写任何开场白、客套话或结尾寒暄。"
)

SUMMARY_TEMPLATE = """请精读下面这篇文献，并生成 Markdown 中文详细精读报告。

核心流程：
1. 必须先判断文献类型：原创研究论文 / 综述性论文 / 无法明确判断。
2. 输出 `## 0. 文献类型判定`，用 2-4 句说明判定依据，例如是否有 Methods/Results、是否有原创实验数据、是否以整合已有研究为主。
3. 根据判定结果，只执行下方对应的报告结构：原创研究论文走“研究型论文模板”；综述性论文走“综述型论文模板”。不要把两个模板都写出来。
4. 必须在报告开头给出“图表覆盖清单”：列出可用正文中识别到的 Figure、Table、Scheme 或图/表编号；后文必须逐一解读。若 PDF 文本抽取不到图注或图表内容，请明确写“可用正文未识别到该图表内容”，不要编造。

共同强制规则：
- 专业术语首次出现时使用“中文 (English)”格式。
- 菌株、基因、蛋白、酶、载体、化合物名称保留英文原文。
- 化合物编号使用 Markdown 粗体，例如化合物 **1**。
- 中文与英文、中文与数字之间保留一个半角空格。
- 尽量覆盖干实验和湿实验：生物信息学、Genome Mining、SSN、系统发育树、AlphaFold、Docking、机理推导、质谱、NMR、酶动力学、遗传操作等。
- 可短句引用原文关键英文结论，但单句引用不超过 25 个英文词；多数情况下请用中文转述。

## 0. 文献类型判定
必须包含：
- 类型：原创研究论文 / 综述性论文 / 无法明确判断
- 判定依据：2-4 句
- 执行模板：研究型论文模板 / 综述型论文模板 / 基于可用信息的混合模板
- 图表覆盖清单：Figure、Table、Scheme 编号列表；若未识别到则说明

如果判定为【原创研究论文】，按以下结构输出：

## 一：基本信息
**文章题目**：英文题目（中文题目）
**文章 DOI 号**：
**期刊名称**：
**通讯作者**：
**通讯作者工作单位**：（分别列举所有通讯作者工作单位的中文译名；原文未提及则写“原文未提及”）

## 二：研究背景
分段阐述该研究领域的现状与重要性、核心科学问题或技术瓶颈、作者开展研究的动机和切入点。语言要专业、精炼，并为后文实验设计提供逻辑铺垫。

## 三：研究思路
按照步骤拆解作者从发现问题到解决问题的逻辑链条，覆盖生物信息学挖掘、假设提出、实验验证、机理推导和结论收束。

## 四：研究方法
列出支撑研究的核心技术手段，格式为“**方法名称**：用途”。必须同时关注干实验和湿实验；如果原文没有某类方法，说明“原文未提及”。

## 五：实验设计及结果分析
这是重点部分。请将全文研究内容逻辑划分为 4-6 个小节，每个小节使用 `###` 标题，并在小节内使用 `#### 实验目的与设计逻辑` 和 `#### 实验结果与深度解析`。

在“实验结果与深度解析”中必须采用连贯的学术段落叙述，禁止使用列表或要点式短句。每个小节要自然融合实验目的、设计逻辑、数据细节、图表解读和结论。必须明确引用并解读 Figure、Table、Scheme，确保“图表覆盖清单”中的每一项至少出现并分析一次。干实验部分要说明其如何指导湿实验，湿实验部分要说明其如何验证或修正干实验模型。尽量写出具体数值，例如产量、转化率、表达倍数、p 值、置信区间、kcat、Km、IC50、样本量等；没有数字时写“原文未提及”。

每个小节末尾可加入“短评”，用 1-3 句评价原文证据是否足以支持作者结论；不要展开外部推理。

## 六：总体结论
总结全文的科学贡献，包括新发现的酶、新机理、新方法、新生物合成逻辑、疾病机制或攻克的技术难题。

## 七：论文评价
### 优点与创新
### 局限与风险
### 未来研究方向

## 八：关键问题及回答
提出并回答 3-6 个围绕核心机制、实验设计、证据强度或可重复性的关键问题。

## 九：与我关键词「{query}」的相关性
说明这篇文献对我的研究有什么用，哪些结果可直接引用，哪些只能作为背景。给出 1-5 分相关性并解释。

如果判定为【综述性论文】，按以下结构输出：

## 一：文献基本信息
**文章题目**：英文题目（中文题目）
**文章 DOI 号**：
**期刊名称**：
**通讯作者**：
**通讯作者工作单位**：（分别列举所有通讯作者工作单位的中文译名；原文未提及则写“原文未提及”）

## 二：核心摘要
用约 200-300 字概括综述的核心主题、讨论范围、关键论点和学术意义。

## 三：结构与逻辑
先列出报告大纲，再解释原文整体论证框架、核心主题和章节组织。说明作者如何从背景问题推进到机制、技术、应用、挑战和展望。

## 四：核心内容深度解读
围绕原文核心论点分节阐述。对生物化学概念、分子机制、信号通路、蛋白功能、调控网络、酶催化、生物合成路径或技术平台进行深入解释。对综述中引用的具体研究案例可概括其结论，不必像原创研究一样逐个拆实验细节。

## 五：图表逐一解读
必须逐一覆盖“图表覆盖清单”中的每一张图、表或 Scheme。每项使用 `[图X：核心内容概括]`、`[表X：核心内容概括]` 或 `[Scheme X：核心内容概括]` 的格式开头，然后先描述其展示内容，再分析其揭示的机制、框架或结论。若可用正文无法识别具体图表内容，必须说明限制。

## 六：关键术语与机制框架
解释文中关键术语、重要分子、通路名称、疾病名称、技术方法或缩写词。首次出现时给出中文定义并标注英文原文全称。

## 七：总结与展望
### 总结
概括该综述的主要贡献和核心信息。
### 展望
提炼作者指出的未来研究方向、未解决关键科学问题和技术挑战。

## 八：与我关键词「{query}」的相关性
说明这篇综述对我的研究有什么用，哪些内容适合作为理论背景、路线图、方法参考或选题依据。给出 1-5 分相关性并解释。

---
文献元信息：
- 标题：{title}
- 期刊/年份：{journal} / {year}
- 引用数：{citations}
- DOI：{doi}

{mode_note}

正文内容如下：
{text}
"""


ABSTRACT_SYSTEM = (
    "你是一名严谨的科研文献助理。现在你只拿到这篇文献的【摘要】，没有全文。"
    "铁律：只能基于摘要原文作答，严禁补充任何外部知识、背景常识或推测；"
    "摘要没写的一律写「摘要未提及（需全文）」，不要脑补、不要扩写、不要替作者把故事讲完整。"
    "关键数值照抄摘要原文。专业术语首次出现用「中文 (English)」格式。"
    "直接从第一个 ## 标题开始输出，不要写任何开场白或寒暄。"
)

ABSTRACT_TEMPLATE = """请仅依据下面这篇文献的【摘要】写一份简短笔记（Markdown，各节用 ## 标题）。
摘要里没有的内容一律不要编；该写「摘要未提及（需全文）」的地方就照实写。

## 一句话结论
（就摘要所述，这篇做了什么、得到什么）

## 文献类型
（从摘要判断：原创研究 / 综述 / 预印本 / 无法确定，附一句依据）

## 研究范围与对象
（研究了什么体系 / 对象 / 问题，仅限摘要明确写到的）

## 摘要要点
（3-6 条，逐条只写摘要明确陈述的内容，含摘要里给出的关键数值；不要补充摘要之外的机制解释）

## 摘要未覆盖、需读全文确认的
（列出对评估这篇有用、但摘要没给的：方法细节、样本量、定量结果、图表、局限等）

## 与我关键词「{query}」的相关性
（1-3 句，给 1-5 分；只基于摘要能判断的部分，拿不准就说明）

---
文献元信息：
- 标题：{title}
- 期刊/年份：{journal} / {year}
- 引用数：{citations}
- DOI：{doi}

摘要原文如下：
{abstract}
"""


def summarize(rec: dict, text: str, query: str, is_fulltext: bool,
              max_chars: int) -> str:
    """对单篇文献产出结构化总结。

    摘要模式走专用轻模板（强约束「只基于摘要、不补外部知识」）；
    全文模式走重模板（类型分流 + 图表覆盖），过长时分块 map-reduce。
    """
    if not is_fulltext:
        return _summarize_abstract(rec, text, query)
    mode_note = "【这是全文】请基于全文作答。"
    if len(text) <= max_chars:
        prompt = SUMMARY_TEMPLATE.format(
            query=query, title=rec["title"], journal=rec.get("journal", ""),
            year=rec.get("year", ""), citations=rec.get("citations", 0),
            doi=rec.get("doi", ""), mode_note=mode_note, text=text,
        )
        return _clean(_chat([
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": prompt},
        ], tag="summary_full"))
    # 全文过长：分块各自记笔记，再汇总
    return _map_reduce(rec, text, query, max_chars)


def _summarize_abstract(rec: dict, abstract: str, query: str) -> str:
    """摘要模式：轻模板 + 强约束，只基于摘要、不补外部知识。"""
    prompt = ABSTRACT_TEMPLATE.format(
        query=query, title=rec["title"], journal=rec.get("journal", ""),
        year=rec.get("year", ""), citations=rec.get("citations", 0),
        doi=rec.get("doi", ""), abstract=abstract,
    )
    return _clean(_chat([
        {"role": "system", "content": ABSTRACT_SYSTEM},
        {"role": "user", "content": prompt},
    ], tag="summary_abstract"))


def _map_reduce(rec, text, query, max_chars):
    chunks = [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
    notes = []
    for idx, ch in enumerate(chunks, 1):
        note = _chat([
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content":
                f"这是文献《{rec['title']}》的第 {idx}/{len(chunks)} 部分。"
                f"请提取其中与「{query}」相关的文献类型线索（原创研究/综述）、"
                f"研究问题、方法细节、综述框架、关键结果、具体数字、统计方法、"
                f"质量控制、局限风险和可复用信息。"
                f"如果文本中出现图表编号、组别、处理条件或参数，也要保留。"
                f"简洁列点，供后续汇总：\n\n{ch}"},
        ], max_tokens=3000, tag="summary_chunk")
        notes.append(f"[部分 {idx}]\n{note}")
    combined = "\n\n".join(notes)
    prompt = SUMMARY_TEMPLATE.format(
        query=query, title=rec["title"], journal=rec.get("journal", ""),
        year=rec.get("year", ""), citations=rec.get("citations", 0),
        doi=rec.get("doi", ""),
        mode_note="【这是全文，已分段提取要点，下面是各段笔记的合并】",
        text=combined,
    )
    return _clean(_chat([
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": prompt},
    ], tag="summary_full"))
