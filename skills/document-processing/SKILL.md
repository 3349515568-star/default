---
name: "document-processing"
description: "处理科学文献的格式转换与批量操作。当需要批量 PDF 转文本、Word 转 Markdown、文献元数据提取、或大量 PDF 文件分类整理时调用。与 lit-agent 的 fulltext 模块配合使用。"
---

# Document Processing - 文献批量处理

## 何时触发

- 用户说"把这批 PDF 转成文本"
- 需要从 Word/LaTeX 论文中提取文本做分析
- 批量处理下载的 PDF 文献（重命名、分类、提取元数据）
- 需要从 PDF 中提取图表或表格
- lit-agent 下载了一批文献后需要批量处理

## 文件格式转换矩阵

| 源格式 | 目标格式 | 工具/库 | 适用场景 |
|--------|----------|---------|----------|
| PDF | 纯文本 (.txt) | PyMuPDF (fitz) | 给 LLM 做精读 |
| PDF | Markdown (.md) | pymupdf4llm / marker-pdf | 保留结构的精读 |
| PDF | 提取图片 | PyMuPDF | 提取论文中的图表 |
| Word (.docx) | Markdown | python-docx → md | 草稿→AI 润色 |
| Word (.docx) | PDF | docx2pdf / libreoffice | 投稿格式 |
| LaTeX (.tex) | PDF | pdflatex | 本地编译 |
| LaTeX (.tex) | Word | pandoc | 协作/投稿 |
| 扫描版 PDF | 可搜索 PDF | ocrmypdf (调 Tesseract) | 老文献 OCR |
| 图片 (.png/.jpg) | 文本 | Tesseract / mcp_ocr | 扫描件/截图转文字 |
| HTML | Markdown | markdownify / html2text | PMC 全文转文本 |

## 常用命令速查

### PDF → 文本（lit-agent 已有 fulltext.py）
```powershell
# lit-agent 内置
<PYTHON_EXE> lit_agent.py ingest --pdf paper.pdf

# 独立使用
<PYTHON_EXE> -c "import fitz; doc=fitz.open('paper.pdf'); [print(page.get_text()) for page in doc]"
```

### PDF → Markdown（保留标题/段落结构）
```powershell
<PYTHON_EXE> -m pymupdf4llm paper.pdf output.md
```

### 批量 PDF 重命名（DOI → 标题）
```python
import fitz, os
for f in os.listdir("pdfs"):
    if f.endswith(".pdf"):
        doc = fitz.open(f"pdfs/{f}")
        title = doc.metadata.get("title", "").strip().replace("/", "_")[:80]
        if title:
            os.rename(f"pdfs/{f}", f"pdfs/{title}.pdf")
```

### Word → Markdown
```powershell
pandoc manuscript.docx -t markdown -o manuscript.md
```

### LaTeX → Word（给不接 LaTeX 的合作者/期刊）
```powershell
pandoc manuscript.tex -o manuscript.docx --bibliography=refs.bib
```

### 扫描版 PDF OCR
```powershell
ocrmypdf --language chi_sim+eng scanned.pdf searchable.pdf
```

## 文献批量整理工作流

### 场景：lit-agent discover 下载了 50 篇 PDF，要分类和重命名

1. **先用 DOI 重命名**
2. **再用 lit-agent 的 summarize 分类标签**（研究类型/模型/方法）自动建子文件夹
3. **给 LLM 喂文件名清单**，让它按主题归类

### 场景：导师发来一个 Word 草稿，要润色成英文

1. Word → Markdown（pandoc）
2. 喂给 LLM + scientific-writing skill 润色
3. 润色后的 Markdown → Word（pandoc 反向转换）

## 大文件处理策略

单篇 PDF 超过 100 页或 50MB 时：
- 不喂全本给 LLM，先提取目录页判断哪些章节相关
- 用 PyMuPDF 的 `doc.get_toc()` 获取目录
- 只提取相关章节的文本
- 分块送给 LLM，每块 ≤ 8000 字

## lit-agent 集成指南

lit-agent 的 `fulltext.py` 已有：
- OA PDF 定位（Unpaywall API）
- PDF → text 提取（PyMuPDF）
- 分块送达 DeepSeek（按 max_chars）

此 skill 补充的是 lit-agent 不覆盖的场景：
- 批量操作（不是单篇是整文件夹）
- Word/LaTeX 格式转换
- 扫描版 OCR
- 写入投稿模板

## 输出原则

- 批处理时先打印"将要操作 N 个文件"让用户确认
- 重命名/移动文件前做干跑（dry run）
- 原文件不覆盖，输出到新目录或加后缀
- 出错不中断整批，收集错误最后统一报告
