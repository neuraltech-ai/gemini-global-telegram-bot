import asyncio
from collections import defaultdict, deque
import io
import logging
import os
import time
from typing import Dict, List

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from google import genai
from google.genai import types as genai_types

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ضع_مفتاح_تيليجرام_هنا")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "ضع_مفتاح_جيميني_هنا")
PORT = int(os.getenv("PORT", "8080"))

MAX_CONCURRENT_REQUESTS = 50
MAX_HISTORY_MESSAGES = 12
RATE_LIMIT_SECONDS = 1.5

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
            "2. الدقة المتناهية: اعتمد على الحقائق المؤكدة ونتائج البحث، وإذا لم تتوفر معلومة مؤكدة قل بوضوح 'لا أملك معلومة مؤكدة حول هذا الأمر' دون تخمين.\n"
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
            "أنت أستاذ ومعلم خبير في البيداغوجيا والتدريس الصريح وتبسيط العلوم.\n"
            "القواعد الصارمة:\n"
            "1. اشرح المفاهيم خطوة بخطوة بأسلوب سلس يناسب المتعلم، مع تقديم أمثلة تطبيقية واضحة.\n"
            "2. عند حل المسائل الرياضية أو العلمية، اعرض خطوات البرهان والحل بالتفصيل المنهجي."
        ),
    },
    "coder": {
        "name": "💻 خبير البرمجة",
        "model": "gemini-2.5-pro",
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

class UserSessionManager:
    def __init__(self):
        self.histories: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=MAX_HISTORY_MESSAGES)
        )
        self.user_modes: Dict[int, str] = defaultdict(lambda: "fast")
        self.last_request_time: Dict[int, float] = defaultdict(float)
        self.last_bot_reply: Dict[int, str] = {}

    def add_message(self, user_id: int, role: str, text: str):
        self.histories[user_id].append(
            genai_types.Content(
                role=role,
                parts=[genai_types.Part.from_text(text=text)],
            )
        )

    def get_history(self, user_id: int) -> List[genai_types.Content]:
        return list(self.histories[user_id])

    def clear_history(self, user_id: int):
        self.histories[user_id].clear()
        self.last_bot_reply.pop(user_id, None)

sessions = UserSessionManager()
concurrency_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

ai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

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

def build_genai_config(mode_key: str) -> genai_types.GenerateContentConfig:
    mode = MODES.get(mode_key, MODES["fast"])
    tools = [{"google_search": {}}] if mode["search"] else None
    return genai_types.GenerateContentConfig(
        system_instruction=mode["instruction"],
        temperature=mode["temp"],
        tools=tools,
    )

@dp.message(CommandStart())
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

@dp.message(Command("clear", "reset"))
async def cmd_clear(message: types.Message):
    sessions.clear_history(message.from_user.id)
    await message.answer(
        "✨ تم مسح ذاكرة المحادثة بنجاح! يمكنك البدء في موضوع جديد الآن."
    )

@dp.message(Command("mode", "settings"))
async def cmd_mode(message: types.Message):
    user_id = message.from_user.id
    await message.answer(
        "⚙️ **إعدادات وضع الذكاء الاصطناعي:**\nاختر النمط الأنسب لطلبك:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard(user_id),
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
            "2. **البث الحي للإجابات (Live Streaming):** تظهر الإجابة تدريجياً بسرعة فائقة دون انتظار طويل.\n"
            "3. **دعم الوسائط المتعددة (Multimodal):** يقرأ الصور، ملفات PDF، والرسائل الصوتية.\n"
            "4. **متصل بمحرك البحث (Google Search Grounding):** للوصول لأحدث المعلومات الموثقة.\n\n"
            "📌 **الأوامر السريعة:**\n"
            "`/start` - القائمة الرئيسية\n"
            "`/mode` - تغيير وضع الذكاء\n"
            "`/clear` - مسح ذاكرة الحوار"
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
    status_msg = await callback.message.answer("⏳ *جاري تنفيذ طلبك...*", parse_mode=ParseMode.MARKDOWN)

    mode_key = sessions.user_modes[user_id]
    try:
        async with concurrency_semaphore:
            response = await ai_client.aio.models.generate_content(
                model=MODES[mode_key]["model"],
                contents=prompts[q_type],
                config=build_genai_config(mode_key),
            )
            reply_text = response.text or "لم أتمكن من المعالجة."
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
        default_prompt = "قم بتحليل هذه الصورة بدقة واشرح ما فيها أو أجب عن الأسئلة الموجودة بداخلها."

        if message.photo:
            file_id = message.photo[-1].file_id
            mime_type = "image/jpeg"
        elif message.document:
            file_id = message.document.file_id
            mime_type = message.document.mime_type or "application/pdf"
            default_prompt = "قم بقراءة هذا المستند وتلخيص أهم النقاط الواردة فيه بدقة."
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
            response = await ai_client.aio.models.generate_content(
                model=MODES[mode_key]["model"],
                contents=contents,
                config=build_genai_config(mode_key),
            )

        reply_text = response.text or "لم أتمكن من استخراج نص من المرفق."
        sessions.add_message(user_id, "user", f"[مرفق: {user_caption}]")
        sessions.add_message(user_id, "model", reply_text)
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
            "⚠️ تعذر تحليل الملف المرفق حالياً. تأكد من أن حجم الملف مناسب وحاول مجدداً."
        )

@dp.message(F.text)
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip()

    now = time.time()
    if now - sessions.last_request_time[user_id] < RATE_LIMIT_SECONDS:
        await message.answer("⚡ أرسل رسائلك بهدوء، جاري معالجة طلبك السابق...")
        return
    sessions.last_request_time[user_id] = now

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    status_msg = await message.answer("💭 *جاري التفكير...*", parse_mode=ParseMode.MARKDOWN)

    mode_key = sessions.user_modes[user_id]
    model_name = MODES[mode_key]["model"]

    sessions.add_message(user_id, "user", user_text)
    conversation_contents = sessions.get_history(user_id)

    try:
        async with concurrency_semaphore:
            stream = await ai_client.aio.models.generate_content_stream(
                model=model_name,
                contents=conversation_contents,
                config=build_genai_config(mode_key),
            )

            full_text = ""
            last_edit_time = time.time()

            async for chunk in stream:
                if chunk.text:
                    full_text += chunk.text
                    if time.time() - last_edit_time >= 1.2 and len(full_text) < 3900:
                        try:
                            await status_msg.edit_text(
                                full_text + " ▌", parse_mode=None
                            )
                            last_edit_time = time.time()
                        except Exception:
                            pass

            if not full_text:
                full_text = "لا أملك معلومة مؤكدة حول هذا الأمر."

            sessions.add_message(user_id, "model", full_text)
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
            "⏳ عذراً، يواجه الخادم ضغطاً كبيراً في الطلبات حالياً أو حدث خطأ مؤقت.\n"
            "يرجى المحاولة مرة أخرى بعد لحظات."
        )

async def health_check(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "online",
            "service": "Global Gemini Telegram Bot",
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

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    await start_web_server()
    logging.info("🚀 البوت العالمي يعمل الآن بنجاح وجاهز لاستقبال المستخدمين!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
