---
name: "infographics"
description: "信息图设计方法论。当用户需要数据可视化信息图、科普信息图、流程信息图时调用。提供 10 种信息图类型模板 + python 代码生成方案 + AI 辅助生成指南。"
---

# Infographics — 信息图设计

## 何时触发
- 用户说"做个信息图""把这个数据可视化"
- 需要把论文结果做成科普信息图
- 需要时间线/流程/对比/数据驱动的可视化
- lit-agent 的 review 输出需要一张总结图

## 信息图 vs 论文图 vs PPT

| | 信息图 | 论文图 | PPT |
|------|--------|--------|-----|
| 受众 | 公众/跨领域 | 同行专家 | 现场观众 |
| 文字量 | 适中 | 极简 | 极简 |
| 解释性 | 自明 | 需正文 | 需讲者 |
| 渠道 | 社媒/海报/报告 | 期刊 | 屏幕投影 |

---

## 10 种信息图类型速查

### 1. 统计类（Statistical）
大数字 + 简单图表。适合：调查结果、流行率、市场规模。

```
示例布局：
┌──────────────────────────────┐
│         [大标题]              │
│                              │
│   [72pt 数字]    [72pt 数字]  │
│   [标签]         [标签]       │
│                              │
│   [柱状图 / 饼图]             │
└──────────────────────────────┘
```

### 2. 时间线（Timeline）
横向或纵向排列关键事件。

### 3. 流程/步骤（Process）
编号步骤 + 箭头连接。适合：protocol、方法学。

### 4. 对比（Comparison）
左右并列对比 A vs B。适合：治疗前后、方法对比。

### 5. 列表（List）
图标 + 标题 + 一句话。适合：要点总结、tips。

### 6. 地理（Geographic）
地图 + 区域数据标注。适合：流行病学、多中心研究。

### 7. 层级（Hierarchical）
金字塔/树形图。适合：证据等级、组织架构。

### 8. 解剖（Anatomical）
用人体/细胞/结构做比喻标注。适合：机制解释。

### 9. 简历（Resume）
技能可视化 + 时间线。适合：学术简历。

### 10. 社交媒体（Social）
大字 + 强对比 + 一句话。适合：Twitter/LinkedIn 传播论文。

---

## python 代码生成方案

用 matplotlib 手动画信息图（完全可控，不依赖 API）：

```python
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

def make_stat_infographic(metrics, title, out_path):
    """metrics = {"指标名": 数值, ...}"""
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")

    # 标题
    ax.text(5, 9.3, title, ha="center", fontsize=28, fontweight="bold",
            fontfamily="sans-serif")

    n = len(metrics)
    colors = ["#0072B2", "#E69F00", "#009E73", "#D55E00"]
    x_positions = np.linspace(1.5, 8.5, n)

    for i, (label, value) in enumerate(metrics.items()):
        x = x_positions[i]
        # 大数字
        ax.text(x, 6.5, str(value), ha="center", fontsize=64, fontweight="bold",
                color=colors[i % len(colors)], fontfamily="sans-serif")
        # 标签
        ax.text(x, 5.5, label, ha="center", fontsize=18,
                fontfamily="sans-serif")
        # 彩色方块
        rect = mpatches.FancyBboxPatch(
            (x - 0.9, 6.2), 1.8, 2.0,
            boxstyle="round,pad=0.1", facecolor=colors[i % len(colors)],
            alpha=0.12, edgecolor="none")
        ax.add_patch(rect)

    fig.tight_layout(pad=2)
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close()


def make_timeline_infographic(events, title, out_path):
    """events = [(年份, 事件), ...]"""
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.set_xlim(-0.5, len(events))
    ax.set_ylim(0, 3)
    ax.axis("off")

    ax.text(len(events) / 2 - 0.5, 2.8, title, ha="center",
            fontsize=24, fontweight="bold")

    # 横向时间线
    ax.plot([0, len(events) - 1], [1.5, 1.5], "k-", linewidth=2, color="#0072B2")

    for i, (year, event) in enumerate(events):
        # 节点
        ax.plot(i, 1.5, "o", markersize=14, color="#0072B2")
        # 年份（上方）
        ax.text(i, 2.0, str(year), ha="center", fontsize=18, fontweight="bold")
        # 事件（下方，交替上下防重叠）
        y = 1.0 if i % 2 == 0 else 0.3
        ax.text(i, y, event, ha="center", fontsize=13, va="top" if i % 2 == 0 else "bottom")

    fig.tight_layout(pad=2)
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close()
```

---

## AI 生成方案（有 OpenRouter API 时）

如果你有 OpenRouter API key（`OPENROUTER_API_KEY` 环境变量），可以用 Nano Banana Pro 生成信息图：

```bash
# 生成统计信息图
<PYTHON_EXE> -c "
import requests, os, json
api_key = os.environ['OPENROUTER_API_KEY']
prompt = 'Create a professional infographic in English: 5 key statistics about CRISPR gene editing in 2025. Use modern flat design, blue and orange color scheme, large numbers, clean layout. No tiny text.'
# 调用 OpenRouter 的 Nano Banana Pro 模型
resp = requests.post('https://openrouter.ai/api/v1/chat/completions',
    headers={'Authorization': f'Bearer {api_key}'},
    json={'model': 'nano-banana-pro', 'messages': [{'role': 'user', 'content': prompt}]})
"
```

如果没装 Nano Banana Pro，用 matplotlib 方案更稳定——完全本地，出图质量可控。

---

## 配色方案速查

### Okabe-Ito（色盲友好，学术首选）
```
#E69F00  #56B4E9  #009E73  #F0E442  #0072B2  #D55E00  #CC79A7  #000000
```

### 医疗/健康
```
#1B4965(深海蓝)  #62B6CB(天蓝)  #CAE9FF(极浅蓝)  #5FA8D3(中蓝)
```

### 科技/AI
```
#1A1A2E(深黑蓝)  #16213E(深蓝)  #0F3460(钴蓝)  #E94560(霓虹粉)
```

### 自然/环境
```
#2D6A4F(森林绿)  #40916C(绿)  #52B788(浅绿)  #D8F3DC(薄绿)
```

### 暖色/社科
```
#582F0E(深棕)  #7F4F24(棕)  #936639(浅棕)  #C2C5AA(沙色)
```

---

## 信息图设计 checklist

- [ ] 一个信息图只讲一个主题
- [ ] 视觉层级：大数字 > 标题 > 正文
- [ ] 最核心的数据用最大字号（≥ 48pt）
- [ ] 至少留 30% 白空间
- [ ] 颜色 ≤ 4 种
- [ ] 字体 ≤ 2 种（一种标题一种正文）
- [ ] 数据来源标注在底部
- [ ] 导出为 PNG（社媒）或 PDF（打印）

## 从 lit-agent 输出生成

把 lit-agent review 的 PRISMA 数据和关键发现喂进去：

```python
# 示例：生成 PRISMA 信息图
make_timeline_infographic(
    events=[
        (f"检索 n={initial}", "数据库检索"),
        (f"去重 n={dedup}", "去重后"),
        (f"筛选 n={screened}", "标题/摘要筛选"),
        (f"纳入 n={included}", "全文纳入"),
    ],
    title="文献筛选流程",
    out_path="prisma_infographic.png"
)
```

---

## 注意事项
- matplotlib 方案不依赖任何外部 API，100% 可复现
- 中文字体问题：调用 plotkit 的 `setup_chinese_font()` 处理
- 颜色用 RGB HEX，不要用 named colors（`"blue"` 太丑）
- 导出 DPI ≥ 200，否则放大后模糊
- 信息图不需要图例——标签直接标在数据旁边
