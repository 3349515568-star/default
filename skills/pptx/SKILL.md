---
name: "pptx"
description: "使用 python-pptx 创建和编辑 PowerPoint 演示文稿。当用户提到幻灯片、PPT、演示、deck、presentation、.pptx 文件时调用。支持创建、读取、编辑、模板填充、批量生成。"
---

# PPTX — PowerPoint 自动化

## 何时触发
- 用户说"做个PPT""生成幻灯片""做一份演示文稿"
- 需要批量生成报告 PPT（如 lit-agent 输出转展示）
- 修改已有 .pptx 文件
- 从数据/论文自动生成汇报材料

## 环境
- python-pptx 已安装
- Node.js v24 + npm 可用（备选 pptxgenjs）
- **不依赖 LibreOffice**：所有操作纯 Python

---

## 快速上手 3 步

### 1. 创建
```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor

prs = Presentation()
# 默认 16:9 宽屏（10×5.625 英寸）
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)
```

### 2. 常用布局（7 种）

| 索引 | 布局名 | 用途 |
|------|--------|------|
| 0 | Title Slide | 标题页 |
| 1 | Title and Content | 文字+图/表 |
| 2 | Section Header | 章节分隔 |
| 3 | Two Content | 左右对比 |
| 5 | Title Only | 自定义布局 |
| 6 | Blank | 完全自由 |
| 7 | Picture with Caption | 大图 |

```python
layouts = prs.slide_layouts
# 标题页
slide = prs.slides.add_slide(layouts[0])
title = slide.shapes.title
subtitle = slide.placeholders[1]
title.text = "CRISPR-Cas9 研究进展"
subtitle.text = "李某某 · 2026"
```

### 3. 保存
```python
prs.save("output.pptx")
```

---

## 文字排版速查

```python
from pptx.util import Pt, Inches
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

# 添加文本框
left, top, width, height = Inches(1), Inches(2), Inches(11), Inches(4)
txBox = slide.shapes.add_textbox(left, top, width, height)
tf = txBox.text_frame
tf.word_wrap = True

# 段落
p = tf.paragraphs[0]
p.text = "主要发现"
p.font.size = Pt(44)
p.font.bold = True
p.font.color.rgb = RGBColor(0x00, 0x72, 0xB2)  # Okabe-Ito 蓝
p.alignment = PP_ALIGN.LEFT

# 多段
for text, level in [("结论一", 0), ("结论二", 1), ("结论三", 1)]:
    p = tf.add_paragraph()
    p.text = text
    p.font.size = Pt(28) if level == 0 else Pt(22)
    p.level = level
    p.space_before = Pt(12)
```

## 表格

```python
rows, cols = 4, 3
table = slide.shapes.add_table(rows, cols,
    Inches(1), Inches(3), Inches(11), Inches(4)).table

# 列宽
table.columns[0].width = Inches(3)
table.columns[1].width = Inches(4)

# 写数据
for r in range(rows):
    for c in range(cols):
        cell = table.cell(r, c)
        cell.text = f"r{r}c{c}"
        for p in cell.text_frame.paragraphs:
            p.font.size = Pt(14)
            if r == 0:           # 表头加粗
                p.font.bold = True
```

## 图表（柱状/折线/饼图）

```python
from pptx.chart.data import CategoryChartData

chart_data = CategoryChartData()
chart_data.categories = ['对照', '处理A', '处理B']
chart_data.add_series('表达量', (1.0, 2.3, 1.8))

chart = slide.shapes.add_chart(
    XL_CHART_TYPE.COLUMN_CLUSTERED,  # 柱状图
    Inches(1), Inches(3), Inches(6), Inches(4),
    chart_data
).chart

chart.has_legend = True
chart.has_title = True
chart.chart_title.text_frame.paragraphs[0].text = "基因表达量"
```
XL_CHART_TYPE 常用值：`COLUMN_CLUSTERED` `BAR_CLUSTERED` `LINE` `PIE` `SCATTER`

## 图片

```python
from pptx.util import Inches

img = slide.shapes.add_picture("figures/result.png",
    Inches(1), Inches(2), width=Inches(5))
# 保持比例：设 width 或 height 任一即可，另一个自动
```

## 页眉/页脚（页码）

```python
# 给所有幻灯片加页码
for slide in prs.slides:
    slide_num = slide.slide_number
    left = prs.slide_width - Inches(1.5)
    top  = prs.slide_height - Inches(0.5)
    txBox = slide.shapes.add_textbox(left, top, Inches(1), Inches(0.4))
    p = txBox.text_frame.paragraphs[0]
    p.text = str(slide_num)
    p.font.size = Pt(10)
    p.alignment = PP_ALIGN.RIGHT
```

## 模板填充（从 lit-agent 输出生成）

```python
def make_report_slides(records, out_path):
    prs = Presentation()
    layouts = prs.slide_layouts
    # 封面
    s = prs.slides.add_slide(layouts[0])
    s.shapes.title.text = "文献检索报告"
    s.placeholders[1].text = f"共 {len(records)} 篇"
    # 每篇一页
    for r in records:
        s = prs.slides.add_slide(layouts[1])
        s.shapes.title.text = r.get("title", "")[:80]
        body = s.placeholders[1]
        tf = body.text_frame
        tf.paragraphs[0].text = f"作者: {r.get('authors','')[:60]}"
        p2 = tf.add_paragraph()
        p2.text = f"期刊: {r.get('journal','')} ({r.get('year','')})"
        p3 = tf.add_paragraph()
        p3.text = r.get("finding", "")[:200]
    prs.save(out_path)
```

## 配色方案（科学会议用）

```python
# Okabe-Ito 色盲友好
OI = {
    "orange":    RGBColor(0xE6, 0x9F, 0x00),
    "sky_blue":  RGBColor(0x56, 0xB4, 0xE9),
    "green":     RGBColor(0x00, 0x9E, 0x73),
    "yellow":    RGBColor(0xF0, 0xE4, 0x42),
    "blue":      RGBColor(0x00, 0x72, 0xB2),
    "vermillion":RGBColor(0xD5, 0x5E, 0x00),
    "purple":    RGBColor(0xCC, 0x79, 0xA7),
    "black":     RGBColor(0x00, 0x00, 0x00),
}

# 背景用浅色版本（RGBColor(0xE8, 0xE8, 0xE8) 等）
```

## 常用排版参数

| 元素 | 字号 | 用途 |
|------|------|------|
| 大标题 | 36-44pt bold | 封面/分节 |
| 页标题 | 28-32pt bold | 每页 |
| 正文 | 18-22pt | 内容 |
| 图注/脚注 | 10-12pt | 引用/页码 |
| 表格文字 | 14-16pt | 表格内 |

## 注意事项
- 优先用 python-pptx（纯 Python，无需外部依赖）
- 备选 pptxgenjs（npm install -g pptxgenjs，JS 生成，图更复杂）
- 模板文件里的占位符索引从 0 开始，不是从 1
- 添加 slide 后要 `add_slide()` 才会落地
- 表格列宽设 `None` 可以自适应
