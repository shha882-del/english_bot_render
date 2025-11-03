# bot.py
# English Trainer Bot (Data/BI focused) – Aiogram v3
# Features: levels (beginner/intermediate/advanced), translation EN<->AR,
# grading with fuzzy match, examples, TTS (gTTS) audio, hints/skip/next.
# Dataset loaded from data.json (see structure note below).

import os
import json
import re
import difflib
from io import BytesIO
from typing import Dict, Any, List, Tuple
from contextlib import suppress

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from gtts import gTTS

# -----------------------------
# Config: read token safely
# -----------------------------
TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("BOT_TOKEN")
    or None
)
if not TOKEN:
    # fallback: token.txt in the same folder (optional)
    if os.path.exists("token.txt"):
        with open("token.txt", "r", encoding="utf-8") as f:
            TOKEN = f.read().strip()
if not TOKEN:
    raise RuntimeError(
        "No bot token found. Set TELEGRAM_TOKEN env var on Render, "
        "or place it in token.txt."
    )

# -----------------------------
# Load dataset (data.json)
# Expected structure:
# {
#   "beginner": [
#     {"en": "data", "ar": "بيانات", "examples": ["We collect data to analyze trends."]},
#     {"en": "chart", "ar": "مخطط", "examples": ["This chart shows monthly sales."]},
#     ...
#   ],
#   "intermediate": [ ... ],
#   "advanced": [ ... ]
# }
# -----------------------------

DATA_FILE = "data.json"

FALLBACK_DATA = {
    "beginner": [
        {"en": "data", "ar": "بيانات",
         "examples": ["We collect data to analyze trends."]},
        {"en": "report", "ar": "تقرير",
         "examples": ["Please share yesterday’s sales report."]},
        {"en": "chart", "ar": "مخطط",
         "examples": ["This chart shows monthly revenue."]}
    ],
    "intermediate": [
        {"en": "insight", "ar": "رؤية/نتيجة تحليل",
         "examples": ["Turn raw data into actionable insights."]},
        {"en": "trend", "ar": "اتجاه/ميل",
         "examples": ["We observed a positive trend in Q4."]},
        {"en": "dashboard", "ar": "لوحة معلومات",
         "examples": ["The dashboard helps track KPIs daily."]}
    ],
    "advanced": [
        {"en": "standard deviation", "ar": "الانحراف المعياري",
         "examples": ["We used standard deviation to measure variability."]},
        {"en": "hypothesis testing", "ar": "اختبار الفرضيات",
         "examples": ["Hypothesis testing validates our assumptions."]},
        {"en": "time series", "ar": "سلاسل زمنية",
         "examples": ["Time series models forecast monthly demand."]}
    ]
}

def load_data() -> Dict[str, List[Dict[str, Any]]]:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                # quick sanity check
                if not all(k in data for k in ("beginner", "intermediate", "advanced")):
                    return FALLBACK_DATA
                return data
            except Exception:
                return FALLBACK_DATA
    return FALLBACK_DATA

DATA = load_data()
LEVELS = ["beginner", "intermediate", "advanced"]

# -----------------------------
# Helpers
# -----------------------------

def normalize_ar(s: str) -> str:
    s = s.strip().lower()
    # remove tashkeel and common punctuation
    s = re.sub(r"[^\u0600-\u06FF0-9\s]", " ", s)
    # normalize alef/yaa/taa marbuta
    s = re.sub("[إأآا]", "ا", s)
    s = re.sub("ى", "ي", s)
    s = re.sub("ة", "ه", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def normalize_en(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def fuzzy_equal(a: str, b: str, lang: str) -> Tuple[bool, float]:
    if lang == "ar":
        a, b = normalize_ar(a), normalize_ar(b)
    else:
        a, b = normalize_en(a), normalize_en(b)
    if not a or not b:
        return False, 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return (ratio >= 0.85), ratio

def tts_bytes(text: str, lang: str = "en") -> BytesIO:
    tts = gTTS(text=text, lang=lang)
    bio = BytesIO()
    tts.write_to_fp(bio)
    bio.seek(0)
    return bio

def pick_item(level: str, idx: int) -> Dict[str, Any]:
    items = DATA.get(level, [])
    if not items:
        return {}
    idx = idx % len(items)
    return items[idx]

# -----------------------------
# FSM
# -----------------------------

class Train(StatesGroup):
    waiting_answer = State()

# Per-user session (in-memory; Render free dyno is ephemeral but ok)
SESS: Dict[int, Dict[str, Any]] = {}

def default_session() -> Dict[str, Any]:
    return {
        "level": "beginner",
        "mode": "en2ar",   # en→ar (translate to Arabic). other mode: ar2en
        "index": 0,        # pointer in dataset
        "current": None,   # current QA item
    }

# -----------------------------
# Bot + Router
# -----------------------------

bot = Bot(token=TOKEN)
dp = Dispatcher()
rt = Router()
dp.include_router(rt)

# -----------------------------
# Commands
# -----------------------------

@rt.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    SESS[m.from_user.id] = SESS.get(m.from_user.id, default_session())
    await state.clear()
    txt = (
        "أهلًا أبو راية 👋\n"
        "بوت تدريب إنجليزي مخصص لتحليل البيانات والبزنس.\n\n"
        "الأوامر:\n"
        "/level – اختيار المستوى (beginner | intermediate | advanced)\n"
        "/mode – اتجاه الترجمة (en2ar | ar2en)\n"
        "/train – ابدأ التمرين\n"
        "/next – سؤال تالي  |  /skip – تخطي  |  /hint – تلميح\n"
        "/voice – نطق الصوت للجملة الحالية\n"
        "/help – المساعدة\n"
    )
    await m.answer(txt)

@rt.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(
        "— طريقة العمل —\n"
        "١) اختر /level و /mode\n"
        "٢) /train لبدء التمرين\n"
        "٣) أجب بترجمة الجملة.\n"
        "✔️ البوت يصحّح بفحص تقارب المعنى (fuzzy)."
    )

@rt.message(Command("level"))
async def cmd_level(m: Message):
    sess = SESS.setdefault(m.from_user.id, default_session())
    parts = m.text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].lower() in LEVELS:
        sess["level"] = parts[1].lower()
        sess["index"] = 0
        await m.answer(f"تم ضبط المستوى إلى: {sess['level']}")
    else:
        await m.answer("اكتب مثلًا:\n`/level beginner`\n`/level intermediate`\n`/level advanced`", parse_mode="Markdown")

@rt.message(Command("mode"))
async def cmd_mode(m: Message):
    sess = SESS.setdefault(m.from_user.id, default_session())
    parts = m.text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].lower() in ("en2ar", "ar2en"):
        sess["mode"] = parts[1].lower()
        await m.answer(f"تم ضبط اتجاه التمرين إلى: {sess['mode']}")
    else:
        await m.answer("اختر:\n`/mode en2ar` (ترجم من إنجليزي لعربي)\n`/mode ar2en` (ترجم من عربي لإنجليزي)", parse_mode="Markdown")

@rt.message(Command("train"))
async def cmd_train(m: Message, state: FSMContext):
    sess = SESS.setdefault(m.from_user.id, default_session())
    item = pick_item(sess["level"], sess["index"])
    if not item:
        await m.answer("البيانات غير متاحة لهذا المستوى.")
        return
    sess["current"] = item
    prompt = (
        f"المستوى: *{sess['level']}* | الوضع: *{sess['mode']}*\n"
    )
    if sess["mode"] == "en2ar":
        prompt += f"🔹 ترجم إلى العربية:\n`{item['en']}`"
    else:
        prompt += f"🔹 ترجم إلى الإنجليزية:\n`{item['ar']}`"
    await m.answer(prompt, parse_mode="Markdown")
    await state.set_state(Train.waiting_answer)

@rt.message(Command("next"))
@rt.message(Command("skip"))
async def cmd_next(m: Message, state: FSMContext):
    sess = SESS.setdefault(m.from_user.id, default_session())
    sess["index"] += 1
    await cmd_train(m, state)

@rt.message(Command("hint"))
async def cmd_hint(m: Message):
    sess = SESS.setdefault(m.from_user.id, default_session())
    item = sess.get("current")
    if not item:
        await m.answer("ابدأ أولًا بـ /train")
        return
    if sess["mode"] == "en2ar":
        # hint from Arabic answer first 2–3 letters
        ans = normalize_ar(item["ar"])
        hint = ans[:3] + "..."
        await m.answer(f"تلميح (بالعربي): {hint}")
    else:
        ans = normalize_en(item["en"])
        hint = ans.split(" ")[0][:3] + "..."
        await m.answer(f"Hint (EN): {hint}")

@rt.message(Command("voice"))
async def cmd_voice(m: Message):
    sess = SESS.setdefault(m.from_user.id, default_session())
    item = sess.get("current")
    if not item:
        await m.answer("ابدأ أولًا بـ /train")
        return
    # speak according to mode (speak the prompt sentence)
    speak_text = item["en"] if sess["mode"] == "en2ar" else item["ar"]
    lang = "en" if sess["mode"] == "en2ar" else "ar"
    mp3 = tts_bytes(speak_text, lang=lang)
    await m.answer_audio(audio=mp3, title=f"TTS ({lang})", caption=speak_text)

# -----------------------------
# Answer handler
# -----------------------------

@rt.message(Train.waiting_answer, F.text)
async def handle_answer(m: Message, state: FSMContext):
    sess = SESS.setdefault(m.from_user.id, default_session())
    item = sess.get("current")
    if not item:
        await m.answer("ابدأ أولًا بـ /train")
        return

    user_text = m.text.strip()
    if sess["mode"] == "en2ar":
        correct = item["ar"]
        ok, score = fuzzy_equal(user_text, correct, lang="ar")
    else:
        correct = item["en"]
        ok, score = fuzzy_equal(user_text, correct, lang="en")

    if ok:
        reply = "✅ صحيح! ممتاز."
        with suppress(Exception):
            # also send TTS of the correct answer (always EN audio for practice)
            mp3 = tts_bytes(item["en"], lang="en")
            await m.answer_audio(audio=mp3, title="Correct – Listen (EN)", caption=item["en"])
        # show example sentence(s)
        ex = item.get("examples") or []
        if ex:
            reply += "\n\nمثال:\n• " + "\n• ".join(ex[:2])
        await m.answer(reply)
        # next
        sess["index"] += 1
        await cmd_train(m, state)
    else:
        sim = int(score * 100)
        await m.answer(
            f"❌ مو دقيق ({sim}%).\n"
            f"الإجابة الصحيحة:\n• {correct}\n"
            "جرب السؤال التالي بـ /next أو تلميح بـ /hint"
        )

# Catch-all for other messages while not in training
@rt.message()
async def idle(m: Message):
    await m.answer("استخدم /train للبدء، أو /help للمساعدة.")

# -----------------------------
# Run
# -----------------------------

if __name__ == "__main__":
    # Long polling (works fine على Render Web Service)
    import asyncio
    async def main():
        print("Bot is running...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

    asyncio.run(main())
# --- existing bot code above ---
if __name__ == "__main__":
    print("Bot is running...")

# keep render happy
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running fine!"

app.run(host="0.0.0.0", port=10000)
