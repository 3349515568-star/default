---
name: "scientific-slides"
description: "为学术演讲设计和生成幻灯片。用于学术会议报告、课题组会、答辩、学术讲座。用 ofox 网关：chatgpt-image-2 生成幻灯片图片 + Gemini 审查质量 + PIL 拼成 PDF。备选 python-pptx 代码排版。"
---

# Scientific Slides — 学术幻灯片设计

## 何时触发
- 用户说"帮我做会议PPT""做个组会汇报"
- 需要从 lit-agent 的综述/总结生成演讲 slides
- 答辩/学术报告的结构化设计

## 核心原则

**学术 slides 和商业 slides 完全不同：**
- 你不是在推销，你是在告诉同行你发现了什么
- 每页只讲一个点，但那个点要讲透
- 观众是来听你讲的，不是来读 slides 的
- 图 > 表 > 文字。文字越少，观众越听你讲

---

## 演讲类型与结构模板

### 学术会议报告（15 分钟）

| 页序 | 内容 | 时间 |
|------|------|------|
| 1 | 标题 + 作者 + 单位 | 30s |
| 2 | 背景 & 科学问题 | 1min |
| 3 | 假说 / 研究思路 | 1min |
| 4-6 | 实验设计 / 方法 | 3min |
| 7-10 | 核心结果（最重要的！） | 6min |
| 11 | 讨论 / 与文献比较 | 1.5min |
| 12 | 结论（3 take-home） | 1min |
| 13 | 致谢 + 联系方式 | 30s |

### 课题组会汇报（30-40 分钟）
比会议多：更详细的背景（3-4 页）、方法（3-4 页）、失败实验（2 页）、下一步计划（2 页）。

### 毕业论文答辩（45-50 分钟）
加：研究意义（2 页）、文献综述（3-4 页）、创新点总结（1 页）、发表论文列表（1 页）。

---

## 方案一：ofox 网关逐页生成（★推荐，最美观）

**你的环境：**
- `mcp_ofox-image` → chatgpt-image-2（文生图），endpoint: `https://api.ofox.ai`
- ofox Gemini API → vision 质量审查，endpoint: `https://api.ofox.ai/gemini`，SDK: `google.genai`
- ofox API key 你自己管理

**四步工作流：**

```
1. 规划每页（标题 + 视觉元素描述）
2. mcp_ofox-image 逐页生成幻灯片图片
3. ofox Gemini vision 审查质量 → 分数不够自动改 prompt 重生成
4. PIL 拼成 PDF
```

### Step 1：规划每页

```markdown
# Slide 01 — 标题页
- 标题：CRISPR-Cas9 脱靶检测：方法、进展与挑战
- 副标题：2026 生物信息学年会
- 演讲者：李某某 · 某某大学
- 视觉：深蓝色渐变背景，DNA 双螺旋剪影低透明度，白色大字标题，金色强调

# Slide 02 — 科学问题
- 标题：CRISPR 脱靶——基因编辑的阿喀琉斯之踵
- 要点：on-target vs off-target 对比
- 视觉：左侧绿色靶心命中 / 右侧红色偏差警告

# Slide 03 — 假说
- 标题：计算预测 + 实验验证 = 安全编辑
- 视觉：Venn 图

# Slide 07-10 — 核心结果
- 如果有数据图（matplotlib / plotkit 生成的），在 prompt 里描述"右下角放置一张柱状图"
- 如果没有图，直接用 ofox 生成概念插图
```

### Step 2：逐页生成（mcp_ofox-image → chatgpt-image-2）

每页调一次 `run_mcp`：

```python
# 第 1 页：标题页（定风格！）
run_mcp(
    "mcp_ofox-image", "generate_image",
    {
        "prompt": (
            "Scientific conference TITLE SLIDE. "
            "Title: 'CRISPR-Cas9 Off-Target Detection' in LARGE white bold text. "
            "Subtitle: 'Bioinformatics Conference 2026'. "
            "Speaker: 'Li, University of X' in smaller text below. "
            "FORMATTING GOAL FOR ENTIRE DECK: "
            "DARK NAVY BLUE gradient background (#1a1a2e). WHITE text. "
            "GOLD (#E69F00) accent on key words. "
            "Arial sans-serif. Clean, minimal, academic — not corporate. "
            "Abstract DNA double helix silhouette at 10% opacity in background. "
            "NO decorative lines. NO clutter. "
            "ALL TEXT LARGE, READABLE FROM 20 FEET."
        ),
        "size": "1536x1024",
        "quality": "high",
    }
)
# → 保存 slides/01_title.png

# 第 2 页：内容页
run_mcp(
    "mcp_ofox-image", "generate_image",
    {
        "prompt": (
            "Scientific presentation slide titled 'The Off-Target Problem'. "
            "STYLE: dark navy background, white text, gold accents, Arial. "
            "Content layout: two large side-by-side panels. "
            "Left panel: green checkmark icon + 'On-Target: 95% precision'. "
            "Right panel: red warning icon + 'Off-Target: 5% risk'. "
            "Arrows pointing to a central DNA icon between panels. "
            "Bottom: small citation text '(Tsai et al., Nature, 2015)'. "
            "READABLE FROM 20 FEET. Clean minimal design."
        ),
        "size": "1536x1024",
        "quality": "high",
    }
)
# → slides/02_problem.png

# 第 3 页：假说
run_mcp(
    "mcp_ofox-image", "generate_image",
    {
        "prompt": (
            "Scientific presentation slide titled 'Computational + Experimental = Safe Editing'. "
            "STYLE: dark navy background, white text, gold accents, Arial. "
            "Content: a Venn diagram with two overlapping circles — "
            "left circle 'Computational Prediction', right circle 'Experimental Validation', "
            "overlap labeled 'Safe Genome Editing'. "
            "Clean, minimal. Large bold text. READABLE FROM 20 FEET."
        ),
        "size": "1536x1024",
        "quality": "high",
    }
)
# → slides/03_hypothesis.png

# ... 逐页继续

# 修改某页不满意的地方
run_mcp(
    "mcp_ofox-image", "edit_image",
    {
        "image_path": "slides/05_methods.png",
        "prompt": "Change background to slightly darker navy, make title text larger",
    }
)
```

**保持视觉一致性**：每页 prompt 都重复同样的 STYLE 描述（`dark navy background, white text, gold accents, Arial`），chatgpt-image-2 没有 attach 机制，靠 prompt 一致性来维持风格。

### Step 3：质量审查（ofox → Gemini vision）

用 ofox 的 Gemini 视觉模型审查生成的幻灯片图片。ofox Gemini 走 Google 原生 SDK：

```python
# pip install google-genai Pillow
import os, json
from google import genai
from PIL import Image

OFX_KEY = os.environ["OFOX_API_KEY"]
client = genai.Client(
    api_key=OFX_KEY,
    http_options={"api_version": "v1beta", "url": "https://api.ofox.ai/gemini"},
)

THRESHOLDS = {
    "marketing": 8.5, "report": 8.0, "presentation": 7.5, "draft": 6.5
}


def review_slide(image_path, doc_type="presentation"):
    """Gemini 审查幻灯片质量，返回 (通过, 分数, fix_prompt)"""
    threshold = THRESHOLDS.get(doc_type, 7.5)

    # 加载图片
    img = Image.open(image_path)

    prompt = (
        "You are a presentation design critic. Rate this slide 0-10. "
        "Check five dimensions (0-2 each): "
        "1) Visual hierarchy & layout "
        "2) Typography readability (is text large enough?) "
        "3) Data visualization clarity "
        "4) Color contrast & accessibility "
        "5) Overall professional impact. "
        "Return ONLY valid JSON: "
        '{"score": X.X, "critique": "...", "issues": [...], "fix_prompt": "..."}'
    )

    response = client.models.generate_content(
        model="google/gemini-3.1-flash-lite",  # 或 gemini-2.5-pro
        contents=[prompt, img],
    )

    result = json.loads(response.text)
    passed = result["score"] >= threshold
    return passed, result["score"], result.get("fix_prompt", "")


def generate_slide_with_review(prompt, out_path, max_retries=3):
    """生成幻灯片 + Gemini 审查 + 自动重生成"""
    # 用 run_mcp 调 mcp_ofox-image，会保存到本地
    # （实际保存路径由 MCP 返回，这里假设你知道）
    for attempt in range(max_retries):
        passed, score, fix_prompt = review_slide(out_path, "presentation")
        print(f"  [{out_path}] Gemini score: {score}/10 {'✓' if passed else '✗'}")

        if passed:
            break

        if fix_prompt and attempt < max_retries - 1:
            # 用 Gemini 的建议改 prompt
            improved = f"{prompt}\n\nFIX THESE ISSUES: {fix_prompt}"
            run_mcp("mcp_ofox-image", "generate_image", {
                "prompt": improved, "size": "1536x1024", "quality": "high"
            })
    return out_path
```

**审查重点**：
- 抽查标题页+结果页+结论页（3 页即可，不用 14 页全查）
- 如果分数 ≥ 7.5（presentation 标准）→ 直接过
- 如果分数低于阈值 → Gemini 返回 `fix_prompt`，把建议拼进原 prompt 重生成

### Step 4：拼成 PDF

```python
from PIL import Image
from pathlib import Path

def slides_to_pdf(image_dir, out_pdf):
    images = sorted(Path(image_dir).glob("*.png"))
    first = Image.open(images[0]).convert("RGB")
    rest = [Image.open(p).convert("RGB") for p in images[1:]]
    first.save(out_pdf, save_all=True, append_images=rest, resolution=150)
    print(f"→ {out_pdf} ({len(images)} 页)")

slides_to_pdf("slides_output", "presentation.pdf")
```

---

## 方案二：python-pptx 代码生成（兜底）

不需要 AI 生图时用：

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

def create_conf_presentation(title, author, affiliation, topics, out_path):
    """topics = [("页标题", "内容文字"), ...]"""
    prs = Presentation()
    prs.slide_width  = Inches(13.333)
    prs.slide_height = Inches(7.5)
    layouts = prs.slide_layouts
    BLUE = RGBColor(0x00, 0x72, 0xB2)

    s = prs.slides.add_slide(layouts[0])
    s.shapes.title.text = title
    s.shapes.title.text_frame.paragraphs[0].font.color.rgb = BLUE
    s.placeholders[1].text = f"{author}\n{affiliation}"

    for i, (page_title, body) in enumerate(topics):
        s = prs.slides.add_slide(layouts[1])
        s.shapes.title.text = page_title
        s.shapes.title.text_frame.paragraphs[0].font.color.rgb = BLUE
        p = s.placeholders[1].text_frame.paragraphs[0]
        p.text = body
        p.font.size = Pt(20)

    prs.save(out_path)


def review_to_slides(review_md_path, out_pptx):
    """lit-agent review markdown → 组会 PPT"""
    with open(review_md_path, encoding="utf-8") as f:
        content = f.read()
    prs = Presentation()
    prs.slide_width  = Inches(13.333)
    prs.slide_height = Inches(7.5)
    layouts = prs.slide_layouts
    sections = content.split("\n## ")
    title = sections[0].replace("# ", "").strip()
    s = prs.slides.add_slide(layouts[0])
    s.shapes.title.text = title
    for sec in sections[1:]:
        lines = sec.split("\n")
        page_title = lines[0].strip()
        body_text = "\n".join(lines[1:]).strip()[:300]
        s = prs.slides.add_slide(layouts[1])
        s.shapes.title.text = page_title
        s.placeholders[1].text_frame.paragraphs[0].text = body_text
        s.placeholders[1].text_frame.paragraphs[0].font.size = Pt(18)
    prs.save(out_pptx)
```

---

## 方案选择决策树

```
要漂亮的 AI 生成幻灯片？
├── 是 → 方案一：
│   1. mcp_ofox-image 逐页生成图片
│   2. ofox Gemini 抽查质量
│   3. PIL 拼 PDF
└── 否 → 方案二：python-pptx 纯代码排版
```

---

## chatgpt-image-2 prompt 规范

**每页 prompt 模板：**
```
Scientific [页类型] SLIDE. 
Title: '[标题]' in large white bold text.
STYLE: dark navy background, white text, gold (#E69F00) accents, Arial font.
Content: [视觉元素描述]. [需要标注的数据].
Citation at bottom if needed: (Author, Year).
READABLE FROM 20 FEET. Clean minimal academic design. NO decorative lines.
```

**保持一致性：每页 prompt 重复同样的 STYLE 描述。**

**chatgpt-image-2 参数速查：**

| 参数 | 值 | 用途 |
|------|-----|------|
| `prompt` | 英文详细描述 | 必填 |
| `size` | `"1536x1024"` | 最接近 16:9 |
| `quality` | `"high"` | 幻灯片要对得起投影仪 |
| `n` | `1` | 一页一张 |

## 时间分配公式

- 背景+问题：20%
- 方法：15%
- **结果：50%** ← 最重要
- 讨论+结论：15%

15 分钟报告 = 3min 背景 + 2min 方法 + 7.5min 结果 + 2.5min 结论

## 常见错误

- ❌ chatgpt-image-2 生成的文字可能太小——prompt 里写 `LARGE TEXT, READABLE FROM 20 FEET`
- ❌ 不同页的风格不统一——每页 prompt 都写同样的 STYLE 描述
- ❌ 结果页不放真实数据图——有 matplotlib 图就描述它让 AI 融入，别纯靠 AI 编数据
- ❌ 一页塞太多信息——拆成多页
- ❌ 结论写"需要进一步研究"——太虚，具体说下一步做什么
