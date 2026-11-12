import asyncio
from collections import defaultdict, deque
import io
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

# إجبار مكتبة google-genai (الإصدار 3+) على استخدام واجهة v1beta التي تدعم
# جميع نماذج Gemini الحديثة وخاصية system_instruction والبحث المباشر
os.environ["GOOGLE_GENAI_API_VERSION"] = "v1beta"

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from google import genai
from google.genai import types as genai_types

# مكتبات توليد ملفات PDF و Word وتنسيق المعادلات الرياضية والفيزيائية
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["mathtext.fontset"] = "stix"
from PIL import Image as PILImage

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    arabic_reshaper = None
    get_display = None

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.shared import Inches, Pt, RGBColor

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

# تسجيل خطوط DejaVu المدمجة داخل matplotlib لدعم العربية والفرنسية والرموز الرياضية والفيزيائية
TTF_DIR = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(TTF_DIR, "DejaVuSans.ttf")))
pdfmetrics.registerFont(
    TTFont("DejaVu-Bold", os.path.join(TTF_DIR, "DejaVuSans-Bold.ttf"))
)

# ==========================================
# 1. إعداد المفاتيح وتنظيف المسافات الزائدة تلقائياً
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ضع_مفتاح_تيليجرام_هنا").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "ضع_مفتاح_جيميني_هنا").strip()
PORT = int(os.getenv("PORT", "8080"))

MAX_CONCURRENT_REQUESTS = 50
MAX_HISTORY_MESSAGES = 10
RATE_LIMIT_SECONDS = 1.0

FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

DISCOVERED_MODELS: List[str] = []

# ==========================================
# 2. محرك ضبط الرموز الرياضية والفيزيائية وتحويل LaTeX
# ==========================================
SUBSCRIPTS = str.maketrans(
    "0123456789+-=()aeioruvnpkijx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒᵣᵤᵥₙₚₖᵢⱼₓ"
)
SUPERSCRIPTS = str.maketrans(
    "0123456789+-=()npkijxabcm*", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿᵖᵏⁱʲˣᵃᵇᶜᵐ*"
)

LATEX_SYMBOLS = [
    (r"\\Longleftrightarrow", " ⟺ "),
    (r"\\Leftrightarrow", " ⇔ "),
    (r"\\iff", " ⇔ "),
    (r"\\Longrightarrow", " ⟹ "),
    (r"\\Rightarrow", " ⇒ "),
    (r"\\implies", " ⇒ "),
    (r"\\longrightarrow", " ⟶ "),
    (r"\\rightarrow", " → "),
    (r"\\to", " → "),
    (r"\\mapsto", " ↦ "),
    (r"\\forall", "∀"),
    (r"\\exists", "∃"),
    (r"\\nexists", "∄"),
    (r"\\notin", "∉"),
    (r"\\in", "∈"),
    (r"\\subseteq", "⊆"),
    (r"\\subset", "⊂"),
    (r"\\cup", "∪"),
    (r"\\cap", "∩"),
    (r"\\emptyset", "∅"),
    (r"\\mathbb\{N\}", "ℕ"),
    (r"\\mathbb\{Z\}", "ℤ"),
    (r"\\mathbb\{Q\}", "ℚ"),
    (r"\\mathbb\{R\}", "ℝ"),
    (r"\\mathbb\{C\}", "ℂ"),
    (r"\\leq", "≤"),
    (r"\\le", "≤"),
    (r"\\geq", "≥"),
    (r"\\ge", "≥"),
    (r"\\neq", "≠"),
    (r"\\ne", "≠"),
    (r"\\approx", "≈"),
    (r"\\equiv", "≡"),
    (r"\\sim", "∼"),
    (r"\\pm", "±"),
    (r"\\mp", "∓"),
    (r"\\times", "×"),
    (r"\\cdot", "·"),
    (r"\\div", "÷"),
    (r"\\infty", "∞"),
    (r"\\partial", "∂"),
    (r"\\nabla", "∇"),
    (r"\\sum", "∑"),
    (r"\\prod", "∏"),
    (r"\\int", "∫"),
    (r"\\alpha", "α"),
    (r"\\beta", "β"),
    (r"\\gamma", "γ"),
    (r"\\delta", "δ"),
    (r"\\Delta", "Δ"),
    (r"\\epsilon", "ε"),
    (r"\\varepsilon", "ε"),
    (r"\\zeta", "ζ"),
    (r"\\eta", "η"),
    (r"\\theta", "θ"),
    (r"\\Theta", "Θ"),
    (r"\\lambda", "λ"),
    (r"\\Lambda", "Λ"),
    (r"\\mu", "μ"),
    (r"\\nu", "ν"),
    (r"\\xi", "ξ"),
    (r"\\pi", "π"),
    (r"\\Pi", "Π"),
    (r"\\rho", "ρ"),
    (r"\\sigma", "σ"),
    (r"\\Sigma", "Σ"),
    (r"\\tau", "τ"),
    (r"\\phi", "φ"),
    (r"\\varphi", "φ"),
    (r"\\Phi", "Φ"),
    (r"\\chi", "χ"),
    (r"\\psi", "ψ"),
    (r"\\Psi", "Ψ"),
    (r"\\omega", "ω"),
    (r"\\Omega", "Ω"),
    (r"\\ell", "ℓ"),
    (r"\\inf", "inf"),
    (r"\\sup", "sup"),
    (r"\\min", "min"),
    (r"\\max", "max"),
    (r"\\lim", "lim"),
    (r"\\ln", "ln"),
    (r"\\log", "log"),
    (r"\\exp", "exp"),
    (r"\\sin", "sin"),
    (r"\\cos", "cos"),
    (r"\\tan", "tan"),
    (r"\\left", ""),
    (r"\\right", ""),
    (r"\\,", " "),
    (r"\\;", " "),
    (r"\\:", " "),
    (r"\\!", ""),
    (r"\\quad", "  "),
    (r"\\qquad", "    "),
    (r"\\\{", "{"),
    (r"\\\}", "}"),
]


def latex_to_clean_unicode(text: str) -> str:
    """تحويل صيغ LaTeX إلى رموز رياضية وفيزيائية قياسية واضحة (Unicode Math)."""
    if not text:
        return ""
    s = text
    s = re.sub(r"\\(?:text|mathrm|mathbf|mathcal|binom)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", s)
    for _ in range(3):
        s = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", s)
    for pattern, repl in LATEX_SYMBOLS:
        s = re.sub(pattern + r"(?![a-zA-Z])", repl, s)

    def sub_brace(m):
        inner = m.group(1)
        if all(c in "0123456789+-=()aeioruvnpkijx" for c in inner):
            return inner.translate(SUBSCRIPTS)
        return f"_({inner})"

    s = re.sub(r"_\{([^{}]+)\}", sub_brace, s)
    s = re.sub(r"_([0-9a-zA-Z])", lambda m: m.group(1).translate(SUBSCRIPTS), s)

    def sup_brace(m):
        inner = m.group(1)
        if all(c in "0123456789+-=()npkijxabcm*" for c in inner):
            return inner.translate(SUPERSCRIPTS)
        return f"^({inner})"

    s = re.sub(r"\^\{([^{}]+)\}", sup_brace, s)
    s = re.sub(r"\^([0-9a-zA-Z\+\-\*])", lambda m: m.group(1).translate(SUPERSCRIPTS), s)
    s = s.replace("$$", "").replace("$", "")
    return s


def format_for_telegram_chat(raw_text: str) -> str:
    """تنسيق النص للعرض المباشر داخل محادثة تيليجرام بدون أي رموز LaTeX خام مشوهة."""
    cleaned = latex_to_clean_unicode(raw_text)
    cleaned = re.sub(r"^#{1,6}\s*(.+)$", r"📌 **\1**", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\*{4,}", "**", cleaned)
    return cleaned


def has_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def is_primarily_arabic(text: str) -> bool:
    ar_chars = len(re.findall(r"[\u0600-\u06FF]", text or ""))
    lat_chars = len(re.findall(r"[a-zA-Z]", text or ""))
    return ar_chars > lat_chars


def render_latex_formula_png(latex_str: str) -> Optional[Tuple[io.BytesIO, int, int]]:
    """توليد صورة عالية الدقة (300 DPI) للمعادلات الرياضية والفيزيائية المعقدة باستخدام خط STIX."""
    clean = latex_str.strip().strip("$").strip()
    if not clean or has_arabic(clean):
        return None
    clean = (
        clean.replace(r"\implies", r"\Longrightarrow")
        .replace(r"\iff", r"\Longleftrightarrow")
        .replace(r"\le", r"\leq")
        .replace(r"\ge", r"\geq")
        .replace(r"\ne", r"\neq")
    )
    clean = re.sub(r"\\text\{([^{}]*)\}", r"\\mathrm{\1}", clean)
    lines = [l.strip() for l in clean.split(r"\\") if l.strip()]
    if not lines:
        return None
    try:
        fig_h = max(0.6, 0.55 * len(lines))
        fig = plt.figure(figsize=(6.5, fig_h), dpi=300)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        if len(lines) == 1:
            ax.text(
                0.5,
                0.5,
                f"${lines[0]}$",
                fontsize=14,
                ha="center",
                va="center",
                color="#0f172a",
            )
        else:
            for idx, ln in enumerate(lines):
                y_pos = 1.0 - (idx + 0.5) / len(lines)
                ax.text(
                    0.5,
                    y_pos,
                    f"${ln}$",
                    fontsize=13,
                    ha="center",
                    va="center",
                    color="#0f172a",
                )
        buf = io.BytesIO()
        plt.savefig(
            buf,
            format="png",
            dpi=300,
            bbox_inches="tight",
            pad_inches=0.12,
            facecolor="#f8fafc",
            edgecolor="none",
        )
        plt.close(fig)
        buf.seek(0)
        with PILImage.open(buf) as im:
            w_px, h_px = im.size
        buf.seek(0)
        return buf, w_px, h_px
    except Exception:
        plt.close("all")
        return None


def split_text_and_math_blocks(raw_text: str) -> List[Tuple[str, str]]:
    pattern = re.compile(r"\$\$(.*?)\$\$|\\\[(.*?)\\\]", re.DOTALL)
    segments = []
    last_idx = 0
    for m in pattern.finditer(raw_text):
        if m.start() > last_idx:
            segments.append(("text", raw_text[last_idx : m.start()]))
        expr = m.group(1) if m.group(1) is not None else m.group(2)
        segments.append(("math", expr.strip()))
        last_idx = m.end()
    if last_idx < len(raw_text):
        segments.append(("text", raw_text[last_idx:]))
    return segments


# ==========================================
# 3. محرك توليد ملفات Word (.docx) و PDF الاحترافية
# ==========================================
def set_docx_rtl(p, is_rtl: bool):
    if not is_rtl:
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        return
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    pPr = p._p.get_or_add_pPr()
    bidi = OxmlElement("w:bidi")
    pPr.append(bidi)


def add_formatted_runs(p, line_text: str):
    parts = re.split(r"(\*\*.*?\*\*)", line_text)
    for part in parts:
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            run = p.add_run(part[2:-2])
            run.bold = True
            run.font.size = Pt(11.5)
        else:
            run = p.add_run(part)
            run.font.size = Pt(11.5)


def generate_docx_bytes(raw_text: str) -> bytes:
    """توليد ملف Word (.docx) منسق بالكامل بنفس لغة النص المرفوع مع ضبط المعادلات والاتجاه."""
    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    rtl_doc = is_primarily_arabic(raw_text)
    title_str = (
        "الحل المفصل والشرح الدقيق — NeuralTech AI"
        if rtl_doc
        else "Solution Détaillée & Analyse — NeuralTech AI"
    )
    header_p = doc.add_paragraph()
    set_docx_rtl(header_p, rtl_doc)
    title_run = header_p.add_run(title_str)
    title_run.bold = True
    title_run.font.size = Pt(16)
    title_run.font.color.rgb = RGBColor(30, 58, 138)

    segments = split_text_and_math_blocks(raw_text)
    for stype, sval in segments:
        if stype == "math":
            png_res = render_latex_formula_png(sval)
            if png_res:
                buf, w_px, _ = png_res
                p_img = doc.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                width_in = min(5.8, max(2.0, w_px / 300.0))
                p_img.add_run().add_picture(buf, width=Inches(width_in))
            else:
                p_math = doc.add_paragraph()
                p_math.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = p_math.add_run(latex_to_clean_unicode(sval))
                r.bold = True
                r.font.size = Pt(12)
                r.font.color.rgb = RGBColor(15, 23, 42)
        else:
            for raw_line in sval.splitlines():
                line = raw_line.strip()
                if not line or line == "---":
                    continue
                is_heading = bool(re.match(r"^#{1,6}\s+", line))
                clean_line = re.sub(r"^#{1,6}\s+", "", line)
                clean_line = latex_to_clean_unicode(clean_line)
                line_rtl = has_arabic(clean_line) or (
                    rtl_doc and not re.search(r"^[A-Za-z0-9]", clean_line)
                )
                p = doc.add_paragraph()
                set_docx_rtl(p, line_rtl)
                if is_heading:
                    r = p.add_run(clean_line.replace("**", ""))
                    r.bold = True
                    r.font.size = Pt(13.5)
                    r.font.color.rgb = RGBColor(29, 78, 216)
                else:
                    add_formatted_runs(p, clean_line)

    out_buf = io.BytesIO()
    doc.save(out_buf)
    return out_buf.getvalue()


def shape_pdf_text(text: str) -> str:
    if has_arabic(text) and arabic_reshaper and get_display:
        try:
            plain = text.replace("**", "")
            reshaped = arabic_reshaper.reshape(plain)
            bidi_txt = get_display(reshaped)
            return xml_escape(bidi_txt)
        except Exception:
            pass
    escaped = xml_escape(text)
    escaped = re.sub(r"\*\*(.*?)\*\*", r'<font name="DejaVu-Bold">\1</font>', escaped)
    return escaped


def generate_pdf_bytes(raw_text: str) -> bytes:
    """توليد ملف PDF احترافي مقاس A4 بنفس لغة المحتوى مع معادلات رياضية وفيزيائية مضبوطة."""
    out_buf = io.BytesIO()
    doc = SimpleDocTemplate(
        out_buf,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=42,
        bottomMargin=42,
    )
    rtl_doc = is_primarily_arabic(raw_text)
    styles = getSampleStyleSheet()
    style_ltr = ParagraphStyle(
        "BodyLTR",
        parent=styles["Normal"],
        fontName="DejaVu",
        fontSize=11,
        leading=16,
        textColor=colors.HexColor("#0f172a"),
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    style_rtl = ParagraphStyle("BodyRTL", parent=style_ltr, alignment=TA_RIGHT)
    style_h_ltr = ParagraphStyle(
        "HeadLTR",
        parent=style_ltr,
        fontName="DejaVu-Bold",
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#1d4ed8"),
        spaceBefore=10,
        spaceAfter=6,
    )
    style_h_rtl = ParagraphStyle("HeadRTL", parent=style_h_ltr, alignment=TA_RIGHT)
    style_math = ParagraphStyle(
        "MathFallback",
        parent=style_ltr,
        fontName="DejaVu-Bold",
        fontSize=11.5,
        leading=16,
        alignment=TA_CENTER,
        backColor=colors.HexColor("#f1f5f9"),
        borderPadding=6,
        spaceBefore=6,
        spaceAfter=8,
    )

    story = []
    title_str = (
        "الحل المفصل والشرح الدقيق — NeuralTech AI"
        if rtl_doc
        else "Solution Détaillée & Analyse — NeuralTech AI"
    )
    story.append(
        Paragraph(
            shape_pdf_text(title_str),
            style_h_rtl if rtl_doc else style_h_ltr,
        )
    )
    story.append(
        HRFlowable(
            width="100%",
            thickness=1,
            color=colors.HexColor("#cbd5e1"),
            spaceAfter=10,
        )
    )

    segments = split_text_and_math_blocks(raw_text)
    for stype, sval in segments:
        if stype == "math":
            png_res = render_latex_formula_png(sval)
            if png_res:
                buf, w_px, h_px = png_res
                max_w = 460.0
                disp_w = min(max_w, max(130.0, w_px * 0.24))
                disp_h = disp_w * (h_px / float(w_px))
                img_flow = RLImage(buf, width=disp_w, height=disp_h)
                img_flow.hAlign = "CENTER"
                story.append(Spacer(1, 4))
                story.append(img_flow)
                story.append(Spacer(1, 6))
            else:
                clean_m = latex_to_clean_unicode(sval)
                story.append(Paragraph(shape_pdf_text(clean_m), style_math))
        else:
            for raw_line in sval.splitlines():
                line = raw_line.strip()
                if not line or line == "---":
                    continue
                is_heading = bool(re.match(r"^#{1,6}\s+", line))
                clean_line = re.sub(r"^#{1,6}\s+", "", line)
                clean_line = latex_to_clean_unicode(clean_line)
                line_rtl = has_arabic(clean_line) or (
                    rtl_doc and not re.search(r"^[A-Za-z0-9]", clean_line)
                )
                if is_heading:
                    st = style_h_rtl if line_rtl else style_h_ltr
                    story.append(
                        Paragraph(shape_pdf_text(clean_line.replace("**", "")), st)
                    )
                else:
                    st = style_rtl if line_rtl else style_ltr
                    story.append(Paragraph(shape_pdf_text(clean_line), st))

    doc.build(story)
    return out_buf.getvalue()


# ==========================================
# 4. أنماط الذكاء الاصطناعي (تطابق صارم للغة الملف والرموز)
# ==========================================
COMMON_RULES = (
    "القواعد الصارمة التي يجب الالتزام بها بدقة متناهية:\n"
    "1. تطابق اللغة التام (Strict Language Mirroring): أجب دائماً وبنسبة 100% بنفس لغة السؤال أو الوثيقة/الصورة المرفوعة! "
    "إذا كانت الوثيقة أو التمارين باللغة الفرنسية (Français)، فيجب أن يكون الحل والشرح بالكامل باللغة الفرنسية دون أي كلمة عربية. "
    "وإذا كانت بالإنجليزية فبالإنجلزية، وإذا كانت بالعربية فبالعربية.\n"
    "2. ضبط الرموز الرياضية والفيزيائية: اكتب المعادلات المستقلة داخل $$...$$ بلغة LaTeX القياسية السليمة ليتم تحويلها إلى معادلات مصورة عالية الدقة في ملفي PDF و Word، "
    "واستخدم الرموز الرياضية القياسية الواضحة (مثل ∀, ∃, ∈, ℕ, ℝ, ∑, ∏, √, ≤, ≥, ⇒, ⇔) داخل السطور النصية.\n"
    "3. الدقة العلمية: قدم خطوات البرهان والحل كاملة ومفصلة ومنظمة."
)

MODES = {
    "fast": {
        "name": "⚡ السريع والذكي (Flash)",
        "model": "gemini-2.5-flash",
        "temp": 0.25,
        "search": True,
        "instruction": f"أنت مساعد ذكي عالمي فائق السرعة والدقة.\n{COMMON_RULES}",
    },
    "deep": {
        "name": "🧠 التفكير العميق (Pro)",
        "model": "gemini-2.5-pro",
        "temp": 0.15,
        "search": True,
        "instruction": f"أنت خبير استشاري وباحث علمي وتحليلي فائق الذكاء.\n{COMMON_RULES}",
    },
    "academic": {
        "name": "🎓 المعلم والأكاديمي",
        "model": "gemini-2.5-flash",
        "temp": 0.2,
        "search": True,
        "instruction": f"أنت أستاذ جامعي وخبير في الرياضيات والفيزياء والعلوم والبيداغوجيا.\n{COMMON_RULES}",
    },
    "coder": {
        "name": "💻 خبير البرمجة",
        "model": "gemini-2.5-flash",
        "temp": 0.1,
        "search": False,
        "instruction": f"أنت مهندس برمجيات أول (Senior Software Architect).\n{COMMON_RULES}",
    },
}


# ==========================================
# 5. إدارة حالة المستخدمين والذاكرة الآمنة
# ==========================================
class UserSessionManager:
    def __init__(self):
        self.histories: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=MAX_HISTORY_MESSAGES)
        )
        self.user_modes: Dict[int, str] = defaultdict(lambda: "fast")
        self.last_request_time: Dict[int, float] = defaultdict(float)
        self.last_bot_reply: Dict[int, str] = {}

    def add_exchange(self, user_id: int, user_text: str, model_text: str):
        self.histories[user_id].append(
            genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=user_text)],
            )
        )
        self.histories[user_id].append(
            genai_types.Content(
                role="model",
                parts=[genai_types.Part.from_text(text=model_text)],
            )
        )

    def build_contents(
        self, user_id: int, current_user_text: str
    ) -> List[genai_types.Content]:
        history_list = list(self.histories[user_id])
        history_list.append(
            genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=current_user_text)],
            )
        )
        return history_list

    def clear_history(self, user_id: int):
        self.histories[user_id].clear()
        self.last_bot_reply.pop(user_id, None)


sessions = UserSessionManager()
concurrency_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

ai_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={"api_version": "v1beta"},
)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


async def discover_available_models():
    global DISCOVERED_MODELS
    try:
        models_found = []
        for m in ai_client.models.list():
            name = getattr(m, "name", "") or ""
            if name.startswith("models/"):
                name = name.replace("models/", "", 1)
            actions = getattr(m, "supported_actions", []) or []
            if "gemini" in name.lower() and "embedding" not in name.lower():
                if not actions or "generateContent" in actions:
                    models_found.append(name)
        if models_found:
            DISCOVERED_MODELS = models_found
            logging.info(f"✅ النماذج المكتشفة: {DISCOVERED_MODELS[:6]}")
    except Exception as e:
        logging.warning(f"Model discovery warning: {e}")


def get_candidate_models(primary_model: str) -> List[str]:
    candidates = [primary_model]
    for m in FALLBACK_MODELS:
        if m not in candidates:
            candidates.append(m)
    for m in DISCOVERED_MODELS:
        if "flash" in m and m not in candidates:
            candidates.append(m)
    for m in DISCOVERED_MODELS:
        if m not in candidates:
            candidates.append(m)
    return candidates[:6]


async def generate_smart_response(
    contents,
    mode_key: str,
    allow_search: bool = True,
) -> str:
    mode = MODES.get(mode_key, MODES["fast"])
    candidates = get_candidate_models(mode["model"])
    errors_log = []

    for model_name in candidates:
        if allow_search and mode["search"]:
            try:
                config = genai_types.GenerateContentConfig(
                    system_instruction=mode["instruction"],
                    temperature=mode["temp"],
                    tools=[{"google_search": {}}],
                )
                response = await ai_client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                logging.warning(f"[{model_name} + search] failed: {e}")

        try:
            config = genai_types.GenerateContentConfig(
                system_instruction=mode["instruction"],
                temperature=mode["temp"],
            )
            response = await ai_client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
            if response and response.text:
                return response.text
        except Exception as e:
            err_msg = f"{model_name}: {str(e)[:120]}"
            logging.warning(f"[{model_name} standard] failed: {e}")
            errors_log.append(err_msg)

    raise RuntimeError(" | ".join(errors_log[:3]))


# ==========================================
# 6. لوحات المفاتيح التفاعلية مع أزرار تصدير PDF و Word
# ==========================================
def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    current_mode = sessions.user_modes[user_id]
    buttons = []
    row = []
    for mode_key, mode_info in MODES.items():
        prefix = "✅ " if mode_key == current_mode else ""
        row.append(
            InlineKeyboardButton(
                text=f"{prefix}{mode_info['name']}",
                callback_data=f"mode:{mode_key}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append(
        [
            InlineKeyboardButton(
                text="🗑️ مسح ذاكرة المحادثة", callback_data="action:clear"
            ),
            InlineKeyboardButton(
                text="ℹ️ حول البوت والمميزات", callback_data="action:help"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_response_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📕 تحميل كملف PDF", callback_data="export:pdf"
                ),
                InlineKeyboardButton(
                    text="📘 تحميل كملف Word", callback_data="export:docx"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 تلخيص", callback_data="quick:summarize"
                ),
                InlineKeyboardButton(
                    text="💡 تبسيط", callback_data="quick:simplify"
                ),
                InlineKeyboardButton(
                    text="🌐 ترجمة (AR/FR/EN)", callback_data="quick:translate"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ تغيير وضع الذكاء", callback_data="action:menu"
                ),
                InlineKeyboardButton(
                    text="🗑️ محادثة جديدة", callback_data="action:clear"
                ),
            ],
        ]
    )


async def safe_send_or_edit(
    message: types.Message,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
    is_edit: bool = False,
):
    display_text = format_for_telegram_chat(text)
    if not display_text or not display_text.strip():
        display_text = "عذراً، لم أتمكن من استخراج إجابة واضحة. يرجى إعادة صياغة السؤال."

    max_len = 4000
    chunks = [
        display_text[i : i + max_len] for i in range(0, len(display_text), max_len)
    ]

    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        try:
            if is_edit and idx == 0:
                await message.edit_text(
                    chunk, parse_mode=ParseMode.MARKDOWN, reply_markup=markup
                )
            else:
                await message.answer(
                    chunk, parse_mode=ParseMode.MARKDOWN, reply_markup=markup
                )
        except Exception:
            if is_edit and idx == 0:
                await message.edit_text(chunk, parse_mode=None, reply_markup=markup)
            else:
                await message.answer(chunk, parse_mode=None, reply_markup=markup)


async def send_generated_documents(chat_id: int, raw_text: str):
    """إرسال نسختي PDF و Word (.docx) تلقائياً بنفس لغة المحتوى ومعادلات مضبوطة."""
    is_ar = is_primarily_arabic(raw_text)
    pdf_caption = (
        "📕 نسخة PDF منسقة مع ضبط الرموز والمعادلات الرياضية والفيزيائية"
        if is_ar
        else "📕 Document PDF formaté avec formules mathématiques et scientifiques"
    )
    docx_caption = (
        "📘 نسخة Word (.docx) قابلة للتعديل والطباعة"
        if is_ar
        else "📘 Document Word (.docx) modifiable et prêt à imprimer"
    )
    try:
        pdf_bytes = await asyncio.to_thread(generate_pdf_bytes, raw_text)
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(
                pdf_bytes, filename="NeuralTech_AI_Solution.pdf"
            ),
            caption=pdf_caption,
        )
    except Exception as e:
        logging.error(f"PDF generation error: {e}")

    try:
        docx_bytes = await asyncio.to_thread(generate_docx_bytes, raw_text)
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(
                docx_bytes, filename="NeuralTech_AI_Solution.docx"
            ),
            caption=docx_caption,
        )
    except Exception as e:
        logging.error(f"DOCX generation error: {e}")


# ==========================================
# 7. معالجة الأوامر والأزرار التفاعلية
# ==========================================
@dp.message(CommandStart())
@dp.message(Command("command1"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    mode_name = MODES[sessions.user_modes[user_id]]["name"]
    welcome_text = (
        f"أهلاً بك يا **{message.from_user.first_name}** في **NeuralTech AI**! 🌟\n\n"
        "أنا مساعدك الذكي العالمي لتحليل النصوص، حل التمارين الرياضية والفيزيائية، وتوليد ملفات **PDF** و **Word** احترافية.\n\n"
        "✨ **مميزات الدقة الفائقة:**\n"
        "• 🌐 **تطابق تام مع لغة الملف:** إذا أرسلت تمريناً بالفرنسية أو الإنجليزية أو العربية، سأجيبك بنفس لغة الملف تماماً.\n"
        "• 📐 **ضبط الرموز الرياضية والفيزيائية:** تحويل تلقائي للمعادلات والكسور والرموز العلمية بدقة مطبعية.\n"
        "• 📕📘 **توليد فوري لملفات PDF و Word:** استلم الحل جاهزاً للطباعة والتعديل بضغطة زر.\n\n"
        f"🧭 **الوضع الحالي:** `{mode_name}`\n\n"
        "👇 **أرسل صورة التمرين أو ملفك أو سؤالك الآن للبدء:**"
    )
    await safe_send_or_edit(
        message, welcome_text, reply_markup=get_main_menu_keyboard(user_id)
    )


@dp.message(Command("mode", "settings", "command2"))
async def cmd_mode(message: types.Message):
    user_id = message.from_user.id
    await message.answer(
        "⚙️ **إعدادات وضع الذكاء الاصطناعي:**\nاختر النمط الأنسب لطلبك:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard(user_id),
    )


@dp.message(Command("clear", "reset", "command3"))
async def cmd_clear(message: types.Message):
    sessions.clear_history(message.from_user.id)
    await message.answer(
        "✨ تم مسح ذاكرة المحادثة بنجاح! يمكنك البدء في موضوع جديد الآن."
    )


@dp.message(Command("status", "debug", "command4"))
async def cmd_status(message: types.Message):
    status_msg = await message.answer("📡 جاري فحص الاتصال بمحرك الذكاء الاصطناعي...")
    try:
        if not DISCOVERED_MODELS:
            await discover_available_models()
        reply = await generate_smart_response(
            contents="أجب بكلمة واحدة فقط: متصل",
            mode_key="fast",
            allow_search=False,
        )
        models_preview = (
            ", ".join(DISCOVERED_MODELS[:4])
            if DISCOVERED_MODELS
            else "gemini-2.5-flash"
        )
        await status_msg.edit_text(
            f"✅ **حالة النظام ممتازة!**\n"
            f"• الاتصال بـ Gemini API (v1beta): `نشط ({reply.strip()})`\n"
            f"• محرك ملفات PDF و Word والمعادلات: `جاهز 100%`\n"
            f"• النماذج المتاحة: `{models_preview}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await status_msg.edit_text(
            f"⚠️ **تفاصيل الخطأ التقني:**\n`{str(e)[:350]}`",
            parse_mode=ParseMode.MARKDOWN,
        )


@dp.callback_query(F.data.startswith("export:"))
async def handle_export_files(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    fmt = callback.data.split(":")[1]
    last_reply = sessions.last_bot_reply.get(user_id)

    if not last_reply:
        await callback.answer(
            "لا يوجد رد سابق لتصديره. أرسل سؤالك أو ملفك أولاً!", show_alert=True
        )
        return

    await callback.answer("جاري تجهيز الملف بمعادلات مضبوطة... 📄")
    await bot.send_chat_action(
        chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT
    )

    try:
        if fmt == "pdf":
            pdf_bytes = await asyncio.to_thread(generate_pdf_bytes, last_reply)
            await bot.send_document(
                chat_id=callback.message.chat.id,
                document=BufferedInputFile(
                    pdf_bytes, filename="NeuralTech_AI_Document.pdf"
                ),
                caption="📕 ملف PDF جاهز للطباعة مع ضبط الرموز والمعادلات",
            )
        elif fmt == "docx":
            docx_bytes = await asyncio.to_thread(generate_docx_bytes, last_reply)
            await bot.send_document(
                chat_id=callback.message.chat.id,
                document=BufferedInputFile(
                    docx_bytes, filename="NeuralTech_AI_Document.docx"
                ),
                caption="📘 ملف Word (.docx) قابل للتعديل مع ضبط الرموز والمعادلات",
            )
    except Exception as e:
        logging.error(f"Export callback error: {e}")
        await callback.message.answer("⚠️ حدث خطأ أثناء توليد الملف، حاول مجدداً.")


@dp.callback_query(F.data.startswith("mode:"))
async def handle_mode_switch(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    mode_key = callback.data.split(":")[1]
    if mode_key in MODES:
        sessions.user_modes[user_id] = mode_key
        mode_name = MODES[mode_key]["name"]
        await callback.answer(f"تم التفعيل: {mode_name} ✅")
        await callback.message.edit_reply_markup(
            reply_markup=get_main_menu_keyboard(user_id)
        )


@dp.callback_query(F.data.startswith("action:"))
async def handle_action_buttons(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split(":")[1]

    if action == "clear":
        sessions.clear_history(user_id)
        await callback.answer("تم تصفير الذاكرة بنجاح! 🗑️", show_alert=False)
        await callback.message.answer(
            "🔄 **بدأت جلسة جديدة!** أرسل سؤالك أو ملفك الجديد الآن.",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif action == "menu":
        await callback.answer()
        await callback.message.answer(
            "⚙️ **اختر وضع الذكاء الاصطناعي:**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu_keyboard(user_id),
        )
    elif action == "help":
        await callback.answer()
        help_text = (
            "🚀 **مميزات NeuralTech AI الاحترافية:**\n\n"
            "1. **تطابق تام مع لغة الملف المرفوع:** يحل التمارين الفرنسية بالفرنسية، والعربية بالعربية، والإنجليزية بالإنجليزية.\n"
            "2. **توليد تلقائي لملفات PDF و Word:** مع ضبط المعادلات الرياضية والفيزيائية بدقة 300 DPI.\n"
            "3. **دعم الوسائط المتعددة:** يقرأ الصور، التمارين، ملفات PDF، والرسائل الصوتية.\n"
            "4. **تعدد المستخدمين والانتقال الذكي بين النماذج.**"
        )
        await safe_send_or_edit(callback.message, help_text)


@dp.callback_query(F.data.startswith("quick:"))
async def handle_quick_followup(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    q_type = callback.data.split(":")[1]
    last_reply = sessions.last_bot_reply.get(user_id)

    if not last_reply:
        await callback.answer(
            "لا يوجد رد سابق في الذاكرة لتطبيق الإجراء عليه.", show_alert=True
        )
        return

    is_ar = is_primarily_arabic(last_reply)
    prompts = {
        "summarize": (
            f"قم بتلخيص هذا النص في نقاط مركزة وبنفس لغته الأصلية:\n\n{last_reply}"
            if is_ar
            else f"Summarize the following text concisely in its exact same language:\n\n{last_reply}"
        ),
        "simplify": (
            f"اشرح هذا المحتوى بأسلوب مبسط جداً مع أمثلة واضحة وبنفس لغته الأصلية:\n\n{last_reply}"
            if is_ar
            else f"Explain the following content simply with clear examples in its exact same language:\n\n{last_reply}"
        ),
        "translate": (
            f"Translate this content accurately into French and English:\n\n{last_reply}"
            if is_ar
            else f"قم بترجمة هذا النص والمحتوى العلمي بدقة متناهية إلى اللغة العربية:\n\n{last_reply}"
        ),
    }

    await callback.answer("جاري المعالجة الفورية... ⚡")
    status_msg = await callback.message.answer(
        "⏳ *جاري تنفيذ طلبك...*", parse_mode=ParseMode.MARKDOWN
    )

    mode_key = sessions.user_modes[user_id]
    try:
        async with concurrency_semaphore:
            reply_text = await generate_smart_response(
                contents=prompts[q_type],
                mode_key=mode_key,
                allow_search=False,
            )
            sessions.last_bot_reply[user_id] = reply_text
            await safe_send_or_edit(
                status_msg,
                reply_text,
                reply_markup=get_response_actions_keyboard(),
                is_edit=True,
            )
    except Exception as e:
        logging.error(f"Quick Action Error: {e}")
        await status_msg.edit_text("⚠️ حدث خطأ مؤقت أثناء المعالجة، يرجى المحاولة لاحقاً.")


# ==========================================
# 8. معالجة الوسائط المتعددة مع توليد ملفي PDF و Word تلقائياً
# ==========================================
@dp.message(F.photo | F.document | F.voice | F.audio)
async def handle_multimodal(message: types.Message):
    user_id = message.from_user.id
    now = time.time()
    if now - sessions.last_request_time[user_id] < RATE_LIMIT_SECONDS:
        await message.answer("⏳ يرجى الانتظار لحظة قبل إرسال طلب جديد.")
        return
    sessions.last_request_time[user_id] = now

    status_msg = await message.answer(
        "🔍 *جاري قراءة الملف وإعداد الحل بملفي PDF و Word بنفس لغة الوثيقة...*",
        parse_mode=ParseMode.MARKDOWN,
    )
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        file_id = None
        mime_type = "image/jpeg"
        # موجه متعدد اللغات يضمن الإجابة بنفس لغة الوثيقة المرفوعة 100%
        default_prompt = (
            "CRITICAL INSTRUCTION:\n"
            "1. Detect the EXACT language of the attached image/document (e.g., French, Arabic, English).\n"
            "2. Write your ENTIRE response, headings, and step-by-step solutions 100% in that EXACT SAME LANGUAGE (if the document is in French, respond 100% in French; if in Arabic, respond 100% in Arabic).\n"
            "3. Solve all exercises, questions, or problems rigorously step by step with extreme mathematical and physical precision.\n"
            "4. Place standalone equations inside $$...$$ blocks using standard LaTeX, and use clean Unicode math symbols (∀, ∃, ∈, ℕ, ℝ, ≤, ≥, ⇒, ⇔, ∑, ∏) for inline expressions."
        )

        if message.photo:
            file_id = message.photo[-1].file_id
            mime_type = "image/jpeg"
        elif message.document:
            file_id = message.document.file_id
            mime_type = message.document.mime_type or "application/pdf"
        elif message.voice:
            file_id = message.voice.file_id
            mime_type = message.voice.mime_type or "audio/ogg"
            default_prompt = (
                "Listen carefully to this voice message and answer in the exact same language spoken by the user."
            )
        elif message.audio:
            file_id = message.audio.file_id
            mime_type = message.audio.mime_type or "audio/mpeg"

        user_caption = (
            f"{message.caption}\n\n{default_prompt}"
            if message.caption
            else default_prompt
        )

        file_info = await bot.get_file(file_id)
        file_buffer = io.BytesIO()
        await bot.download_file(file_info.file_path, destination=file_buffer)
        file_bytes = file_buffer.getvalue()

        mode_key = sessions.user_modes[user_id]
        contents = [
            genai_types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            genai_types.Part.from_text(text=user_caption),
        ]

        async with concurrency_semaphore:
            reply_text = await generate_smart_response(
                contents=contents,
                mode_key=mode_key,
                allow_search=False,
            )

        sessions.add_exchange(user_id, "[Uploaded Document/Image]", reply_text)
        sessions.last_bot_reply[user_id] = reply_text

        await safe_send_or_edit(
            status_msg,
            reply_text,
            reply_markup=get_response_actions_keyboard(),
            is_edit=True,
        )

        # إرسال ملفي PDF و Word تلقائياً بعد إرسال الحل النصي
        await bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.UPLOAD_DOCUMENT
        )
        await send_generated_documents(message.chat.id, reply_text)

    except Exception as e:
        logging.error(f"Multimodal Error: {e}")
        await status_msg.edit_text(
            f"⚠️ تعذر تحليل الملف المرفق حالياً:\n`{str(e)[:250]}`",
            parse_mode=ParseMode.MARKDOWN,
        )


# ==========================================
# 9. معالجة النصوص
# ==========================================
@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip()

    now = time.time()
    if now - sessions.last_request_time[user_id] < RATE_LIMIT_SECONDS:
        await message.answer("⚡ جاري معالجة طلبك السابق، لحظة من فضلك...")
        return
    sessions.last_request_time[user_id] = now

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    status_msg = await message.answer(
        "💭 *جاري التفكير...*", parse_mode=ParseMode.MARKDOWN
    )

    mode_key = sessions.user_modes[user_id]
    conversation_contents = sessions.build_contents(user_id, user_text)

    try:
        async with concurrency_semaphore:
            full_text = await generate_smart_response(
                contents=conversation_contents,
                mode_key=mode_key,
                allow_search=True,
            )

            sessions.add_exchange(user_id, user_text, full_text)
            sessions.last_bot_reply[user_id] = full_text

            await safe_send_or_edit(
                status_msg,
                full_text,
                reply_markup=get_response_actions_keyboard(),
                is_edit=True,
            )

    except Exception as e:
        logging.error(f"Text Generation Error for user {user_id}: {e}")
        await status_msg.edit_text(
            f"⏳ عذراً، حدث خطأ أثناء الاتصال بالنموذج:\n`{str(e)[:250]}`",
            parse_mode=ParseMode.MARKDOWN,
        )


# ==========================================
# 10. خادم الويب المدمج (Keep-Alive Server لـ Render)
# ==========================================
async def health_check(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "online",
            "service": "NeuralTech AI Global Telegram Bot",
            "api_version": "v1beta",
            "pdf_docx_math_engine": "enabled",
            "discovered_models": DISCOVERED_MODELS[:5],
            "active_users_in_memory": len(sessions.histories),
        }
    )


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logging.info(f"🌐 خادم الويب يعمل بنجاح على المنفذ {PORT} (Render Ready)")


async def setup_bot_commands():
    commands = [
        BotCommand(command="start", description="🚀 القائمة الرئيسية وبدء المحادثة"),
        BotCommand(command="mode", description="⚙️ تغيير وضع الذكاء الاصطناعي"),
        BotCommand(command="clear", description="🗑️ مسح الذاكرة وبدء حوار جديد"),
        BotCommand(command="status", description="📡 فحص حالة الاتصال والخادم"),
    ]
    try:
        await bot.set_my_commands(commands)
        logging.info("✅ تم تحديث قائمة أوامر البوت (Menu) تلقائياً!")
    except Exception as e:
        logging.warning(f"Could not set bot commands: {e}")


# ==========================================
# 11. نقطة التشغيل الرئيسية
# ==========================================
async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    await start_web_server()
    await discover_available_models()
    await setup_bot_commands()
    logging.info("🚀 البوت العالمي يعمل الآن بنجاح وجاهز لاستقبال المستخدمين!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
