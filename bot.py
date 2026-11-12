import asyncio
from collections import defaultdict, deque
import io
import logging
import os
import time
from typing import Dict, List

# إجبار مكتبة google-genai (الإصدار 3+) على استخدام واجهة v1beta التي تدعم
# جميع نماذج Gemini الحديثة وخاصية system_instruction والبحث المباشر
os.environ["GOOGLE_GENAI_API_VERSION"] = "v1beta"

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from google import genai
from google.genai import types as genai_types

# ==========================================
# 1. إعداد المفاتيح وتنظيف المسافات الزائدة تلقائياً
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ضع_مفتاح_تيليجرام_هنا").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "ضع_مفتاح_جيميني_هنا").strip()
PORT = int(os.getenv("PORT", "8080"))

MAX_CONCURRENT_REQUESTS = 50
MAX_HISTORY_MESSAGES = 10
RATE_LIMIT_SECONDS = 1.0

# النماذج الحديثة المعتمدة في واجهة v1beta (بدون gemini-1.5-flash المتوقف)
FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

# قائمة النماذج المكتشفة تلقائياً من حساب المستخدم عند التشغيل
DISCOVERED_MODELS: List[str] = []

# ==========================================
# 2. أنماط الذكاء الاصطناعي (AI Personas & Modes)
# ==========================================
MODES = {
    "fast": {
        "name": "⚡ السريع والذكي (Flash)",
        "model": "gemini-2.5-flash",
        "temp": 0.3,
        "search": True,
        "instruction": (
            "أنت مساعد ذكي عالمي فائق السرعة والدقة تعمل داخل تيليجرام.\n"
            "القواعد الصارمة:\n"
            "1. أجب بلغة المستخدم (العربية، الفرنسية، أو الإنجليزية) بأسلوب منظم وواضح باستخدام النقاط والرموز التعبيرية المناسبة.\n"
            "2. الدقة المتناهية: اعتمد على الحقائق المؤكدة، وإذا لم تتوفر معلومة مؤكدة قل بوضوح 'لا أملك معلومة مؤكدة حول هذا الأمر' دون تخمين.\n"
            "3. اجعل الإجابات مباشرة، غنية بالمعلومات، ومثالية للقراءة على شاشة الهاتف."
        ),
    },
    "deep": {
        "name": "🧠 التفكير العميق (Pro)",
        "model": "gemini-2.5-pro",
        "temp": 0.2,
        "search": True,
        "instruction": (
            "أنت خبير استشاري وباحث تحليلي فائق الذكاء.\n"
            "القواعد الصارمة:\n"
            "1. قم بتحليل الأسئلة المعقدة خطوة بخطوة بعمق ومنطقية عالية.\n"
            "2. قدم إجابات شاملة، موثقة، ومقسمة إلى فقرات وعناوين واضحة.\n"
            "3. التزم بالدقة العلمية والمنهجية التامة وامتنع كلياً عن الهلوسة أو التخمين."
        ),
    },
    "academic": {
        "name": "🎓 المعلم والأكاديمي",
        "model": "gemini-2.5-flash",
        "temp": 0.25,
        "search": True,
        "instruction": (
            "أنت أستاذ ومعلم خبير في البيداغوجيا والتدريس الصريح وتبسيط العلوم والرياضيات.\n"
            "القواعد الصارمة:\n"
            "1. اشرح المفاهيم خطوة بخطوة بأسلوب سلس يناسب المتعلم، مع تقديم أمثلة تطبيقية واضحة.\n"
            "2. عند حل المسائل الرياضية أو العلمية (بالعربية أو الفرنسية)، اعرض خطوات البرهان والحل بالتفصيل المنهجي."
        ),
    },
    "coder": {
        "name": "💻 خبير البرمجة",
        "model": "gemini-2.5-flash",
        "temp": 0.1,
        "search": False,
        "instruction": (
            "أنت مهندس برمجيات أول (Senior Software Architect).\n"
            "القواعد الصارمة:\n"
            "1. اكتب أكواداً نظيفة، آمنة، خالية من الأخطاء، وجاهزة للإنتاج (Production-Ready).\n"
            "2. اشرح سبب المشكلة البرمجية باختصار وقدم الحل الأمثل مع التعليقات التوضيحية داخل الكود."
        ),
    },
}

# ==========================================
# 3. إدارة حالة المستخدمين والذاكرة الآمنة
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

# تهيئة عميل Gemini مع فرض استخدام v1beta لضمان عمل كافة النماذج والخصائص
ai_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={"api_version": "v1beta"},
)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


# ==========================================
# 4. الاكتشاف التلقائي للنماذج المتاحة في حساب المستخدم
# ==========================================
async def discover_available_models():
    """فحص النماذج المتاحة فعلياً لمفتاح API الخاص بالمستخدم لتجنب خطأ 404 نهائياً."""
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
            logging.info(f"✅ النماذج المكتشفة في الحساب: {DISCOVERED_MODELS[:6]}")
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


# ==========================================
# 5. محرك الذكاء المتسامح مع الأخطاء (Smart Fallback Engine)
# ==========================================
async def generate_smart_response(
    contents,
    mode_key: str,
    allow_search: bool = True,
) -> str:
    mode = MODES.get(mode_key, MODES["fast"])
    candidates = get_candidate_models(mode["model"])
    errors_log = []

    for model_name in candidates:
        # المحاولة 1: مع البحث المباشر في جوجل
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

        # المحاولة 2: بدون أداة البحث لضمان السرعة وتفادي قيود الحصة المجانية
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
# 6. لوحات المفاتيح التفاعلية (Inline Keyboards)
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
                    text="📝 تلخيص", callback_data="quick:summarize"
                ),
                InlineKeyboardButton(
                    text="💡 تبسيط بأمثلة", callback_data="quick:simplify"
                ),
                InlineKeyboardButton(
                    text="🌐 ترجمة (EN/FR)", callback_data="quick:translate"
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
    if not text or not text.strip():
        text = "عذراً، لم أتمكن من استخراج إجابة واضحة. يرجى إعادة صياغة السؤال."

    max_len = 4000
    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]

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


# ==========================================
# 7. معالجة الأوامر
# ==========================================
@dp.message(CommandStart())
@dp.message(Command("command1"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    mode_name = MODES[sessions.user_modes[user_id]]["name"]
    welcome_text = (
        f"أهلاً بك يا **{message.from_user.first_name}** في المساعد الذكي المتطور! 🌟\n\n"
        "أنا بوت عالمي متعدد الوسائط أعمل بأحدث نماذج **Gemini** مع اتصال مباشر بمحرك البحث وميزة الذاكرة التفاعلية.\n\n"
        "✨ **ماذا يمكنك أن ترسل لي؟**\n"
        "• 💬 **أسئلة ومحادثات نصية:** مع حفظ سياق حوارك بالكامل.\n"
        "• 🖼️ **صور ووثائق (PDF):** لتحليلها، استخراج النصوص، أو حل التمارين المصورة.\n"
        "• 🎙️ **رسائل صوتية:** سأستمع إليها وأجيبك فوراً.\n\n"
        f"🧭 **الوضع الحالي:** `{mode_name}`\n\n"
        "⚠️ *تنبيه: رغم الدقة العالية والربط بالبحث المباشر، يرجى التحقق دائماً في القرارات الطبية أو القانونية الحساسة.*\n\n"
        "👇 **اختر وضع الذكاء المفضل لديك أو أرسل سؤالك مباشرة:**"
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
        models_preview = ", ".join(DISCOVERED_MODELS[:4]) if DISCOVERED_MODELS else "gemini-2.5-flash"
        await status_msg.edit_text(
            f"✅ **حالة النظام ممتازة!**\n"
            f"• الاتصال بـ Gemini API (v1beta): `نشط ({reply.strip()})`\n"
            f"• النماذج المتاحة: `{models_preview}`\n"
            f"• الوضع الحالي: `{MODES[sessions.user_modes[message.from_user.id]]['name']}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await status_msg.edit_text(
            f"⚠️ **تفاصيل الخطأ التقني:**\n`{str(e)[:350]}`",
            parse_mode=ParseMode.MARKDOWN,
        )


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
            "🚀 **مميزات البوت الاحترافية:**\n\n"
            "1. **تعدد المستخدمين الفوري (Async Concurrency):** يدعم عشرات المستخدمين في نفس الثانية بذاكرة مستقلة لكل مستخدم.\n"
            "2. **الاكتشاف الذكي للنماذج (v1beta Auto-Discovery):** يضمن استمرار الردود بأقصى سرعة ودقة.\n"
            "3. **دعم الوسائط المتعددة (Multimodal):** يقرأ الصور، التمارين الرياضية، ملفات PDF، والرسائل الصوتية.\n"
            "4. **متصل بمحرك البحث (Google Search Grounding):** للوصول لأحدث المعلومات الموثقة.\n\n"
            "📌 **الأوامر السريعة:**\n"
            "`/start` - القائمة الرئيسية\n"
            "`/mode` - تغيير وضع الذكاء\n"
            "`/clear` - مسح ذاكرة الحوار\n"
            "`/status` - فحص حالة النظام"
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

    prompts = {
        "summarize": f"قم بتلخيص هذا النص في نقاط مركزة ومختصرة جداً:\n\n{last_reply}",
        "simplify": f"اشرح هذا الكلام بأسلوب مبسط جداً مع إعطاء مثال عملي واضح:\n\n{last_reply}",
        "translate": f"قم بترجمة هذا النص بدقة احترافية إلى اللغتين الإنجليزية والفرنسية:\n\n{last_reply}",
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
# 8. معالجة الوسائط المتعددة (صور التمارين، ملفات PDF، صوتيات)
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
        "🔍 *جاري قراءة وتحليل الملف المرفق...*", parse_mode=ParseMode.MARKDOWN
    )
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        file_id = None
        mime_type = "image/jpeg"
        default_prompt = (
            "قم بقراءة وتحليل هذه الصورة بدقة عالية. إذا كانت تحتوي على تمارين أو مسائل رياضية أو علمية، "
            "فقدّم الحل المفصل والمنهجي خطوة بخطوة لكل سؤال بوضوح تام."
        )

        if message.photo:
            file_id = message.photo[-1].file_id
            mime_type = "image/jpeg"
        elif message.document:
            file_id = message.document.file_id
            mime_type = message.document.mime_type or "application/pdf"
            default_prompt = "قم بقراءة هذا المستند وتلخيص أهم النقاط أو حل الأسئلة الواردة فيه بدقة."
        elif message.voice:
            file_id = message.voice.file_id
            mime_type = message.voice.mime_type or "audio/ogg"
            default_prompt = "استمع إلى هذه الرسالة الصوتية وأجب عن السؤال أو الطلب الموجود فيها بدقة."
        elif message.audio:
            file_id = message.audio.file_id
            mime_type = message.audio.mime_type or "audio/mpeg"
            default_prompt = "قم بتحليل هذا الملف الصوتي وتلخيص محتواه."

        user_caption = message.caption or default_prompt

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

        sessions.add_exchange(user_id, f"[ملف مرفق: {user_caption}]", reply_text)
        sessions.last_bot_reply[user_id] = reply_text

        await safe_send_or_edit(
            status_msg,
            reply_text,
            reply_markup=get_response_actions_keyboard(),
            is_edit=True,
        )

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
