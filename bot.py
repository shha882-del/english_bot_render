import asyncio
import logging
import os
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple
import random

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, ReplyKeyboardMarkup, KeyboardButton
from gtts import gTTS

# ===========================
# إعدادات عامة
# ===========================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # يقرأ التوكن من متغير البيئة (بيئة Render)
DB_PATH = "eng_bot.db"
AUDIO_DIR = Path("audio")
AUDIO_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO)

# ===========================
# الجمل التدريبية
# ===========================
SENTENCES = [
    ("I collect data every day.", "أجمع البيانات كل يوم."),
    ("The report is due tomorrow morning.", "التقرير مستحق صباح الغد."),
    ("Our dashboard updates every hour.", "لوحة المعلومات تتحدث كل ساعة."),
    ("We found a key insight in the sales.", "وجدنا استنتاجًا مهمًا في المبيعات."),
    ("Please follow the meeting agenda.", "رجاءً اتبع جدول أعمال الاجتماع."),
    ("Customers are unhappy with logistics.", "العملاء غير راضين عن الخدمات اللوجستية."),
    ("The sales trend is positive this quarter.", "اتجاه المبيعات إيجابي هذا الربع.")
]

# ===========================
# تعريف الجلسة
# ===========================
@dataclass
class Session:
    expected: str
    arabic_hint: str
    started_at: datetime

sessions: Dict[int, Session] = {}

# ===========================
# قاعدة البيانات
# ===========================
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    last_active TEXT
);
CREATE TABLE IF NOT EXISTS history(
    user_id INTEGER,
    ts TEXT,
    expected TEXT,
    user_text TEXT,
    accuracy REAL
);
"""

# ===========================
# أدوات مساعدة
# ===========================
def tts_to_file(text: str) -> Path:
    """تحويل النص إلى صوت وحفظه"""
    fname = AUDIO_DIR / f"tts_{hash(text)}.mp3"
    if not fname.exists():
        gTTS(text, lang="en").save(fname)
    return fname

def normalize(s: str) -> str:
    """تنظيف النص من الرموز وحروف كبيرة"""
    return " ".join("".join(ch.lower() for ch in s if ch.isalnum() or ch.isspace()).split())

def accuracy_score(expected: str, got: str) -> float:
    """حساب نسبة الدقة بين الجملتين"""
    return round(SequenceMatcher(None, normalize(expected), normalize(got)).ratio() * 100, 1)

async def ensure_db():
    """تأكيد إنشاء الجداول"""
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript(CREATE_TABLES_SQL)
    await db.commit()
    return db

async def log_attempt(db, user_id: int, expected: str, user_text: str, acc: float):
    """تسجيل المحاولات"""
    await db.execute(
        "INSERT INTO history(user_id, ts, expected, user_text, accuracy) VALUES(?,?,?,?,?)",
        (user_id, datetime.utcnow().isoformat(), expected, user_text, acc)
    )
    await db.commit()

async def weekly_report(db, user_id: int) -> str:
    """تقرير أسبوعي بالأداء"""
    week_ago = datetime.utcnow() - timedelta(days=7)
    async with db.execute(
        "SELECT ts, expected, accuracy FROM history WHERE user_id=? AND ts>?",
        (user_id, week_ago.isoformat())
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return "لا توجد محاولات خلال الأسبوع الماضي."
    avg_acc = sum(r[2] for r in rows) / len(rows)
    report_lines = [f"{r[0][:10]}: {r[1]} — {r[2]:.1f}%" for r in rows[-5:]]
    return f"📊 تقريرك الأسبوعي:\nعدد المحاولات: {len(rows)}\nالمتوسط: {avg_acc:.1f}%\n\nآخر الجمل:\n" + "\n".join(report_lines)

# ===========================
# البوت
# ===========================
async def main():
    db = await ensure_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    # لوحة الأوامر السريعة
    KB = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/coach"), KeyboardButton(text="/quiz_choice")],
            [KeyboardButton(text="/report")],
        ],
        resize_keyboard=True
    )

    # أمر البدء
    @dp.message(Command("start"))
    async def start(message: Message):
        await message.answer("👋 مرحبًا! اكتب /coach للبدء أو /quiz_choice لتمرين الاختيار.", reply_markup=KB)

    # تمرين الكتابة والنطق
    @dp.message(Command("coach"))
    async def coach(message: Message):
        eng, ar = random.choice(SENTENCES)
        sessions[message.from_user.id] = Session(expected=eng, arabic_hint=ar, started_at=datetime.utcnow())
        audio_file = FSInputFile(tts_to_file(eng))
        await message.answer(f"🔊 Say or type this:\n**{eng}**\n💡 {ar}", parse_mode="Markdown")
        await message.answer_voice(audio_file)

    # التحقق من الإجابة الكتابية
    @dp.message(F.text)
    async def check_text(message: Message):
        sess = sessions.get(message.from_user.id)
        if not sess:
            return
        acc = accuracy_score(sess.expected, message.text)
        await log_attempt(db, message.from_user.id, sess.expected, message.text, acc)
        await message.answer(f"✅ You wrote: {message.text}\nExpected: {sess.expected}\nAccuracy: {acc:.1f}%")

    # تمرين اختيار من متعدد
    @dp.message(Command("quiz_choice"))
    async def quiz_choice(message: Message):
        q = random.choice(SENTENCES)
        eng, correct = q
        wrongs = random.sample([m[1] for m in SENTENCES if m[1] != correct], 3)
        options = wrongs + [correct]
        random.shuffle(options)
        txt = f"🧠 What does **{eng}** mean in Arabic?\n" + "\n".join(f"{i+1}. {opt}" for i,opt in enumerate(options))
        await message.answer(txt, parse_mode="Markdown")

    # تقرير أسبوعي
    @dp.message(Command("report"))
    async def report(message: Message):
        rep = await weekly_report(db, message.from_user.id)
        await message.answer(rep)

    logging.info("Bot polling started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
