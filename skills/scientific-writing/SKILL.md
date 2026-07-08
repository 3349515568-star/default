---
name: "scientific-writing"
description: "按 IMRaD 结构撰写或润色科学论文。当用户要写论文、修改草稿、将笔记整理成论文段落、或需要符合特定期刊格式时调用此技能。与 lit-agent 的总结输出衔接——把精读笔记升级为论文段落。"
---

# Scientific Writing - 科学论文写作

## 何时触发

- 用户说"帮我把这些笔记写成论文"
- 需要从 lit-agent 的文献总结生成引言/讨论段落
- 修改论文草稿使其符合期刊要求
- 将中文草稿写成英文论文
- 需要按特定期刊格式排版

## IMRaD 结构

所有原创研究论文都按此结构。综述例外——综述走 Introduction → Thematic Sections → Discussion → Conclusions。

### Introduction（引言）
**三段式漏斗结构：**
1. **大背景**（1-2 句）：这个领域为什么重要？
2. **已知和缺口**（3-5 句）：前人做了什么，还有什么不知道？
3. **本文工作**（1-2 句）：我们做了什么，核心发现是什么？

### Methods（方法）
**按小标题分节，要求可复现：**
- 实验设计（样本量、分组、随机化）
- 材料/试剂（厂商、货号、纯度）
- 数据采集（仪器型号、参数）
- 统计分析（软件版本、检验方法、显著性水平）
- 代码/数据获取（GitHub/数据库链接）

### Results（结果）
**只描述发现了什么，不解释为什么：**
- 每个段落对应一个图/表
- 先给总体趋势，再给具体数字
- 统计结果格式：`(t=2.34, df=28, p=0.027, Cohen's d=0.85)`
- 用主动语态："We found..." 而非 "It was found..."

### Discussion（讨论）
**倒漏斗结构：**
1. **重申主要发现**（1 段）：我们发现了什么？
2. **与文献比较**（2-3 段）：和其他人的发现一致/不一致？为什么？
3. **机制解释**（1-2 段）：可能的生物学/物理学机制是什么？
4. **局限性**（1 段）：样本量/方法/推广性的局限
5. **意义与展望**（1 段）：对领域意味着什么？下一步该做什么？

## 写作原则

### 每段一个要点
每段只讲一个事。读完第一句就应该知道这段讲什么。段首句（topic sentence）是整段的论点，后面是证据。

### 具体的 > 含糊的
- 不好："The treatment improved outcomes significantly."
- 好："Treatment A reduced tumor volume by 47% (95% CI: 32-62%) compared to vehicle at day 21."

### 主动 > 被动
- 不好："It was observed that..."
- 好："We observed that..."

### 短句 > 长句
一句话超过 30 个词就考虑拆成两句。中文学术语感：一句话超过 40 个字就拆。

### 先结论后解释
每段先说发现了什么，再解释可能的原因。不要让读者看到最后才知道你想说什么。

## 期刊格式速查

| 期刊 | 特色要求 |
|------|----------|
| Nature | 字母编号的参考文献（非数字），无单独 Conclusions 节 |
| Science | 极简摘要（≤125 词），正文分 Results / Discussion / Materials and Methods |
| Cell | Graphical Abstract 必须，STAR Methods |
| PLOS ONE | 不要求新颖性，Methods 放最后 |
| Lancet | 结构化摘要（Background/Methods/Findings/Interpretation） |

## 工作流程

### 第一步：确认结构
根据用户意图和目标期刊，确定用 IMRaD 还是综述结构，确认各部分的篇幅和侧重点。

### 第二步：从粗到细
先写各级标题搭骨架，再填每段的 topic sentence，最后逐段展开。不要从头到尾逐字写——容易前后脱节。

### 第三步：逐段轮询
每写完一个 section，回读检查：这个 section 的每段主题句串起来是不是一个连贯的 mini-argument？

### 第四步：统一术语与格式
全篇统一：基因名斜体、蛋白名正体、单位空格（如 5 mM 不是 5mM）、统计报告格式。

## 输出格式

直接输出论文段落，用 markdown 的 `# ## ###` 表示层级。不确定的数据用 `[需要补充: ...]` 标记。

```markdown
# [论文标题]

## Introduction
[漏斗: 大背景 → 已知缺口 → 本文工作]

## Methods
### 实验设计
...

### 统计分析
...

## Results
### [关键发现 1]
...

## Discussion
...
```

## 连接 lit-agent

lit-agent 的 summarize 输出已经是结构化的精读笔记。写论文时：
- 把多篇同类文献的总结喂给 agent，说"帮我把这些笔记写成 Introduction 的文献回顾段落"
- 指定目标期刊，agent 会按该期刊风格调整
- 用 peer-review skill 审一下产出的段落
