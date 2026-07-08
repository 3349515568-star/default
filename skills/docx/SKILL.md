---
name: "docx"
description: "使用 python-docx 创建和编辑 Word 文档。当用户提到 Word文档、docx、word、报告、申请书、标书、协议时调用。支持创建、读取、编辑、样式排版、表格、图片、目录。"
---

# DOCX — Word 文档自动化

## 何时触发
- 用户说"写个Word文档""生成一份报告""做个协议"
- 基金申请书/标书排版
- lit-agent 综述输出转 Word 格式
- 批量替换模板生成文件

## 环境
- python-docx 已安装
- **不依赖 LibreOffice**：所有操作纯 Python

---

## 快速上手

```python
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

doc = Document()
```

## 页面设置

```python
from docx.shared import Inches, Cm
from docx.enum.section import WD_ORIENT

section = doc.sections[0]
section.page_width  = Cm(21.0)   # A4
section.page_height = Cm(29.7)
section.top_margin    = Cm(2.54)  # 1 英寸标准
section.bottom_margin = Cm(2.54)
section.left_margin   = Cm(3.17)
section.right_margin  = Cm(3.17)
```

## 段落与文字（最常用）

```python
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

# 添加段落
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.LEFT  # 或 CENTER / RIGHT / JUSTIFY

# 段落内跑 (run) —— 文字的最小单元
run = p.add_run("这是一段重要发现。")
run.font.size = Pt(14)
run.font.name = "宋体"
run.bold = True
run.font.color.rgb = RGBColor(0x00, 0x72, 0xB2)

# 多跑混合（加粗+正常混排）
p2 = doc.add_paragraph()
p2.add_run("结论：").bold = True
p2.add_run("处理组表达量显著升高 (p < 0.01)。")
```

## 标题（Heading 1/2/3）

```python
doc.add_heading("第一章 引言", level=1)
doc.add_heading("1.1 研究背景", level=2)
doc.add_heading("1.1.1 前期工作", level=3)
```

修改标题样式：
```python
from docx.shared import Pt
style = doc.styles['Heading 1']
style.font.size = Pt(22)
style.font.bold = True
style.font.name = "黑体"
```

## 字体设置

Windows 上中英文推荐：中文用宋体/黑体，英文用 Times New Roman/Arial。

```python
from docx.oxml.ns import qn

def set_run_font(run, cn_font="宋体", en_font="Times New Roman", size=12):
    """中英文字体分开设——中文字体走 rFonts 的 east-asia 属性"""
    run.font.size = Pt(size)
    run.font.name = en_font
    r = run._element
    rPr = r.find(qn('w:rPr'))
    if rPr is None:
        rPr = r.makeelement(qn('w:rPr'), {})
        r.insert(0, rPr)
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = rPr.makeelement(qn('w:rFonts'), {})
        rPr.insert(0, rFonts)
    rFonts.set(qn('w:eastAsia'), cn_font)
```

## 表格

```python
rows, cols = 4, 3
table = doc.add_table(rows=rows, cols=cols, style='Table Grid')
table.alignment = WD_TABLE_ALIGNMENT.CENTER

# 写表头
for c, text in enumerate(["基因", "表达量", "p值"]):
    cell = table.cell(0, c)
    cell.text = text
    for p in cell.paragraphs:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in p.runs:
            run.bold = True
            run.font.size = Pt(11)

# 写数据
data = [("TP53", "2.3", "0.001"), ("BRCA1", "1.8", "0.02"), ("EGFR", "0.9", "0.15")]
for r, (gene, expr, pval) in enumerate(data, 1):
    table.cell(r, 0).text = gene
    table.cell(r, 1).text = expr
    table.cell(r, 2).text = pval
```

## 图片

```python
doc.add_picture("figures/result.png", width=Inches(4.5))
# 居中
last_paragraph = doc.paragraphs[-1]
last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
```

## 换页 / 分节

```python
doc.add_page_break()
# 或
from docx.enum.section import WD_BREAK
doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
```

## 页码（页脚）

```python
from docx.oxml.ns import qn

section = doc.sections[0]
footer = section.footer
footer.is_linked_to_previous = False
p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER

# 加 "第 X 页" 风格的页码域代码
run = p.add_run()
fldChar1 = run._r.makeelement(qn('w:fldChar'), {qn('w:fldCharType'): 'begin'})
run._r.append(fldChar1)
instrText = run._r.makeelement(qn('w:instrText'), {})
instrText.text = 'PAGE'
run._r.append(instrText)
fldChar2 = run._r.makeelement(qn('w:fldChar'), {qn('w:fldCharType'): 'end'})
run._r.append(fldChar2)
# Word 打开后会渲染成实际页码
```

## 空白段落 / 间距

```python
p = doc.add_paragraph("")
p.paragraph_format.space_before = Pt(6)
p.paragraph_format.space_after  = Pt(6)
p.paragraph_format.line_spacing = 1.5  # 1.5 倍行距
```

## 全文替换

```python
def replace_text(doc, old, new):
    for p in doc.paragraphs:
        if old in p.text:
            for run in p.runs:
                if old in run.text:
                    run.text = run.text.replace(old, new)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for run in p.runs:
                        if old in run.text:
                            run.text = run.text.replace(old, new)
```

## 基金申请书模板填充

```python
def fill_grant_template(template_path, data, out_path):
    """把 lit-agent 的综述输出填进基金 Doc 模板"""
    doc = Document(template_path)
    replacements = {
        "{{TITLE}}": data["title"],
        "{{BACKGROUND}}": data["background"],
        "{{METHODS}}": data["methods"],
        "{{REFERENCES}}": "\n".join(data["references"]),
    }
    for old, new in replacements.items():
        replace_text(doc, old, new)
    doc.save(out_path)
```

## 常用字号对照

| 用途 | 中文 | 英文 | 字号 |
|------|------|------|------|
| 大标题 | 黑体 | Arial bold | 22pt |
| 一级标题 | 黑体 | Arial bold | 18pt |
| 二级标题 | 黑体 | Arial bold | 16pt |
| 正文 | 宋体 | Times New Roman | 12pt |
| 图注/页脚 | 宋体 | Times New Roman | 9pt |

## 注意事项
- python-docx 不直接支持中文下划线/双下划线，要绕道 XML
- 表格合并单元格用 `table.cell(r,c).merge(table.cell(r,c+1))`
- `doc.save()` 后无法继续编辑，要在保存前完成所有操作
- 模板 docx 里用 `{{变量名}}` 占位，然后用上面的 `replace_text` 自动填
- 输出 docx 可直接用 Word 打开修，不需要 LibreOffice 渲染
