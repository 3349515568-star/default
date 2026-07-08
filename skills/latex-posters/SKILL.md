---
name: "latex-posters"
description: "学术海报设计指南。当用户需要做会议海报、学术墙报、Poster 时调用。提供 python-pptx 替代方案（不依赖 LaTeX），含布局模板、配色、字号规范。"
---

# LaTeX Posters — 学术海报设计（无 LaTeX 方案）

## 何时触发
- 用户说"做个学术海报""做个 Poster""会议海报"
- 需要将论文转成一张大海报
- 学术会议 Poster Session 准备

## 无 LaTeX 方案

本 skill 默认用 **python-pptx 做大尺寸单页幻灯片**来替代 LaTeX 海报。原因：
- 不需要装 LaTeX / tikzposter / beamerposter
- 用你已经在用的 python-pptx 即可
- 导出 PDF 只需要"另存为 PDF"（Word 内操作）

---

## 海报尺寸标准

| 会议类型 | 常用尺寸 | 英寸 | python-pptx |
|----------|----------|------|-------------|
| 国际会议 | A0 竖版 | 33.1 × 46.8" | `(33.1*914400, 46.8*914400)` EMU |
| 国内会议 | 90×120cm | ≈ 35.4 × 47.2" | 同上换算 |
| 小型会议 | A1 竖版 | 23.4 × 33.1" | 同上 |

python-pptx 最大支持 56 英寸，A0 没问题。

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu

prs = Presentation()
# A0 竖版换算为 EMU（1 inch = 914400 EMU）
prs.slide_width  = Emu(int(33.1 * 914400))
prs.slide_height = Emu(int(46.8 * 914400))
```

---

## 海报标准分区

```
┌──────────────────────────────────────────────────┐
│              标题栏 (高 12-15cm)                   │
│    大标题(72pt+) | 作者 | 单位 | Logo             │
├──────────┬───────────────┬────────────────────────┤
│          │               │                        │
│  引言    │   方法        │   结果(占最多空间)      │
│  (左)    │   (中)        │   (右)                 │
│          │               │                        │
│  图表    │   流程图      │   关键数据图           │
│          │               │                        │
├──────────┴───────────────┴────────────────────────┤
│         结论 | 参考文献 | 联系方式 | QR码          │
└──────────────────────────────────────────────────┘
```

三栏布局是最经典的。左栏引言+背景，中栏方法，右栏结果+讨论。

---

## 字号规范（Poster 专有）

**Poster 是远距离看的（1-2 米），字母要足够大：**

| 元素 | 字号 (pt) | 说明 |
|------|-----------|------|
| 主标题 | 72-96 | 3 米外能看清 |
| 作者名 | 48-60 | bold |
| 单位 | 36-44 | regular |
| 栏目标题 | 48-56 | bold |
| 正文 | 28-34 | 1 米外可读 |
| 图注 | 22-26 | 走近看 |
| 参考文献 | 18-22 | 最小字号 |

---

## 海报配色

- 背景：白色为主，栏目标题块可用浅色背景区分
- 主色调：和你的机构/研究主题相关的一个主色
- Okabe-Ito 方案：蓝色 `#0072B2` 为主，橙色 `#E69F00` 强调
- 浅灰分区：`#F0F0F0` 给方法的背景块
- 不要超过 3 种颜色——海报太大，颜色多了眼花

---

## python-pptx 海报生成模板

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

def make_a0_poster(title, authors, affiliation, sections, out_path):
    """sections = [("标题", "文字"), ...] 按左中右三栏"""
    prs = Presentation()
    # A0 竖版
    prs.slide_width  = Emu(int(33.1 * 914400))
    prs.slide_height = Emu(int(46.8 * 914400))

    # 标题栏
    txBox = prs.slides[0].shapes.add_textbox(
        Inches(1.5), Inches(0.8), Inches(30), Inches(6))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(84)
    p.font.bold = True
    p.font.color.rgb = RGBColor(0x00, 0x72, 0xB2)
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = authors
    p2.font.size = Pt(52)
    p2.alignment = PP_ALIGN.CENTER
    p3 = tf.add_paragraph()
    p3.text = affiliation
    p3.font.size = Pt(36)
    p3.alignment = PP_ALIGN.CENTER

    # 分隔线
    line = prs.slides[0].shapes.add_shape(
        1, Inches(2), Inches(7.5), Inches(29), Inches(0.05))  # 1=矩形
    line.fill.solid()
    line.fill.fore_color.rgb = RGBColor(0x00, 0x72, 0xB2)
    line.line.fill.background()

    # 三栏
    col_width = Inches(9.3)
    col_gap   = Inches(0.8)
    left_x    = Inches(1.5)
    for i in range(3):
        x = left_x + (col_width + col_gap) * i
        col_box = prs.slides[0].shapes.add_textbox(
            x, Inches(8.5), col_width, Inches(36))
        col_tf = col_box.text_frame
        col_tf.word_wrap = True
        # 栏目标题
        hp = col_tf.paragraphs[0]
        hp.text = sections[i][0] if i < len(sections) else ""
        hp.font.size = Pt(52)
        hp.font.bold = True
        hp.font.color.rgb = RGBColor(0x00, 0x72, 0xB2)
        hp.space_after = Pt(24)
        # 内容
        cp = col_tf.add_paragraph()
        cp.text = sections[i][1] if i < len(sections) else ""
        cp.font.size = Pt(28)
        cp.space_before = Pt(12)

    prs.save(out_path)
    # 打开后用 Word "另存为 PDF" 即可
```

---

## 内容写作原则

### 标题
- 用发现做标题，不要用描述做标题
- 不好："关于XXX的研究"
- 好："A 蛋白通过 B 通路调控 C 肿瘤的 D 表型"

### 引言（左栏上部）
- 最多 3-4 句话
- 只写给没看过你论文的人看的最核心背景
- 最后一句是科学问题/假说

### 方法（中栏）
- 画一个实验流程图（用 mermaid 或 plotkit 画好了贴进来）
- 文字部分只写关键词：样本量/分组/检测方法
- 不要写详细 protocol——那是论文正文的事

### 结果（右栏，占最大面积）
- 每个核心发现一张大图（不是论文 multi-panel 的小图）
- 每张图配一句话解释
- 图注字体 ≥ 22pt

### 结论（底部）
- 3 条 take-home，每条 ≤ 15 字
- 写在最显眼的位置（底部中央）

---

## 需要 LaTeX 时

如果你装了 LaTeX，可用的包：
- `tikzposter`：最灵活，适合自定义设计
- `beamerposter`：适合用过 Beamer 做 slides 的人
- `baposter`：经典三栏布局

推荐命令：
```bash
# 安装 texlive（约 3GB，需要的话）
# 或用 MiKTeX（Windows 友好）
pdflatex poster.tex
```

## 注意事项
- A0 太大了，编辑时可能在屏幕上看不全，保存 PDF 后检查
- 打印前让打印店确认颜色（屏幕和印刷色差大）
- 图要从原论文里导 300dpi+ 的版本，别截低清图
- 随身带 PDF 电子版 + 实体海报（打印店可能搞丢）
