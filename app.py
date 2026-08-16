"""
ربات چت ناشناس تلگرام - نسخه پیشرفته (Webhook / PythonAnywhere)
------------------------------------------------------------------
قابلیت‌های این نسخه نسبت به قبل:
    - پروفایل کامل: نام مستعار، عکس، سن، شهر، استان
    - کد کاربری اختصاصی برای هرکس (#1000, #1001, ...)
    - امکان ویرایش پروفایل از منو
    - پیام «به فلان کاربر وصل شدی» + دکمه نمایش پروفایل طرف مقابل
    - دکمه‌ی «گزارش کاربر» - بعد از گزارش، دو نفر دیگه هیچوقت به هم وصل نمیشن
    - سیستم دعوت دوستان (لینک رفرال + شمارش دعوت‌های موفق)
    - منوی اصلی جذاب‌تر
"""

import logging
import os
import random
import sqlite3
import string
import asyncio
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anon_bot.db")

db = sqlite3.connect(DB_PATH, check_same_thread=False)

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    gender TEXT,
    age INTEGER,
    city TEXT,
    state TEXT NOT NULL DEFAULT 'new',
    partner_id INTEGER,
    waiting_for TEXT,
    anon_target INTEGER,
    anon_code TEXT UNIQUE
)
""")
db.execute("""
CREATE TABLE IF NOT EXISTS anon_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_tg_id INTEGER NOT NULL,
    from_chat_id INTEGER NOT NULL,
    from_message_id INTEGER NOT NULL,
    revealed INTEGER DEFAULT 0
)
""")
db.execute("""
CREATE TABLE IF NOT EXISTS blocked_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    reported_id INTEGER NOT NULL
)
""")
db.commit()


def ensure_column(table, col, coltype):
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        db.commit()


ensure_column("users", "nickname", "TEXT")
ensure_column("users", "photo_file_id", "TEXT")
ensure_column("users", "province", "TEXT")
ensure_column("users", "user_code", "INTEGER")
ensure_column("users", "referred_by", "INTEGER")


def backfill_user_codes():
    rows = db.execute("SELECT tg_id FROM users WHERE user_code IS NULL ORDER BY tg_id").fetchall()
    if not rows:
        return
    start = db.execute("SELECT COALESCE(MAX(user_code), 999) FROM users").fetchone()[0]
    for i, (tg_id,) in enumerate(rows, start=1):
        db.execute("UPDATE users SET user_code = ? WHERE tg_id = ?", (start + i, tg_id))
    db.commit()


backfill_user_codes()


def get_user(tg_id):
    cur = db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def gen_user_code():
    cnt = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return 1000 + cnt


def ensure_user(tg_id):
    user = get_user(tg_id)
    if user:
        return user
    code = gen_user_code()
    db.execute("INSERT INTO users (tg_id, state, user_code) VALUES (?, 'new', ?)", (tg_id, code))
    db.commit()
    return get_user(tg_id)


def update_user(tg_id, **fields):
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [tg_id]
    db.execute(f"UPDATE users SET {set_clause} WHERE tg_id = ?", values)
    db.commit()


def find_match(my_tg_id, my_gender, want_gender):
    cur = db.execute(
        """
        SELECT * FROM users
        WHERE state = 'waiting' AND gender = ? AND waiting_for = ?
          AND tg_id NOT IN (
              SELECT reported_id FROM blocked_pairs WHERE reporter_id = ?
              UNION
              SELECT reporter_id FROM blocked_pairs WHERE reported_id = ?
          )
        LIMIT 1
        """,
        (want_gender, my_gender, my_tg_id, my_tg_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def gen_code():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def format_profile(user, show_code=True):
    gender_fa = "پسر" if user["gender"] == "male" else "دختر" if user["gender"] == "female" else "-"
    lines = [f"👤 {user.get('nickname') or 'بدون نام'}"]
    if show_code:
        lines.append(f"🆔 کد کاربری: #{user.get('user_code')}")
    lines.append(f"⚧ جنسیت: {gender_fa}")
    lines.append(f"🎂 سن: {user.get('age') or '-'}")
    lines.append(f"🏙 شهر: {user.get('city') or '-'}")
    lines.append(f"🗺 استان: {user.get('province') or '-'}")
    return "\n".join(lines)


PROVINCES = [
    "آذربایجان شرقی", "آذربایجان غربی", "اردبیل", "اصفهان", "البرز",
    "ایلام", "بوشهر", "تهران", "چهارمحال و بختیاری", "خراسان جنوبی",
    "خراسان رضوی", "خراسان شمالی", "خوزستان", "زنجان", "سمنان",
    "سیستان و بلوچستان", "فارس", "قزوین", "قم", "کردستان",
    "کرمان", "کرمانشاه", "کهگیلویه و بویراحمد", "گلستان", "گیلان",
    "لرستان", "مازندران", "مرکزی", "هرمزگان", "همدان", "یزد",
]
OTHER_PROVINCE = "🌍 خارج از ایران"


def chunk(lst, n):
    return [lst[i:i + n] for i in range(0, len(lst), n)]


PROVINCE_MENU = ReplyKeyboardMarkup(
    chunk(PROVINCES, 3) + [[OTHER_PROVINCE]],
    resize_keyboard=True,
)

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["👧 چت با دختر 💕", "👦 چت با پسر 🔥"],
        ["🔗 پیام ناشناس", "🎁 دعوت دوستان"],
        ["👤 پروفایل من", "🆘 پشتیبانی"],
    ],
    resize_keyboard=True,
)

CHATTING_MENU = ReplyKeyboardMarkup(
    [["⏭ نفر بعدی"], ["🚩 گزارش کاربر"], ["⛔️ پایان چت"]],
    resize_keyboard=True,
)

GENDER_MENU = ReplyKeyboardMarkup(
    [["👦 پسر", "👧 دختر"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

PROFILE_EDIT_MENU = ReplyKeyboardMarkup(
    [
        ["✏️ ویرایش نام", "✏️ ویرایش عکس"],
        ["✏️ ویرایش سن", "✏️ ویرایش شهر"],
        ["✏️ ویرایش استان"],
        ["🔙 بازگشت به منو"],
    ],
    resize_keyboard=True,
)

SKIP_MENU = ReplyKeyboardMarkup([["⏭ رد کردن"]], resize_keyboard=True, one_time_keyboard=True)
REMOVE_MENU = ReplyKeyboardRemove()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tg_id = update.effective_user.id

    if context.args:
        param = context.args[0]

        if param.startswith("anon_"):
            code = param[5:]
            cur = db.execute("SELECT * FROM users WHERE anon_code = ?", (code,))
            row = cur.fetchone()
            ensure_user(tg_id)

            if not row:
                await update.message.reply_text("❌ این لینک معتبر نیست.")
                return
            cols = [d[0] for d in cur.description]
            owner = dict(zip(cols, row))

            if owner["tg_id"] == tg_id:
                await update.message.reply_text("نمی‌توانید برای خودتان پیام ناشناس ارسال کنید.")
                return

            update_user(tg_id, state="sending_anon", anon_target=owner["tg_id"])
            await update.message.reply_text(
                "✉️ پیامی که می‌خواهید به‌صورت ناشناس ارسال شود را بفرستید (متن، عکس، ویس و ...)."
            )
            return

        if param.startswith("ref_"):
            try:
                referrer_id = int(param[4:])
            except ValueError:
                referrer_id = None
            existed_before = get_user(tg_id) is not None
            user = ensure_user(tg_id)
            if referrer_id and not existed_before and referrer_id != tg_id and not user.get("referred_by"):
                update_user(tg_id, referred_by=referrer_id)
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text="🎉 یک نفر با لینک دعوت شما وارد ربات شد!",
                    )
                except Exception:
                    pass

    user = ensure_user(tg_id)
    if not user["gender"]:
        update_user(tg_id, state="ask_gender")
        await update.message.reply_text(
            "👋 سلام و خوش اومدی به ربات چت ناشناس!\n"
            "اینجا می‌تونی بدون اینکه هویتت مشخص بشه با آدمای جدید در ارتباط باشی 🎭\n\n"
            "بریم پروفایلت رو بسازیم. اول جنسیتت رو انتخاب کن:",
            reply_markup=GENDER_MENU,
        )
        return

    await update.message.reply_text(
        f"سلام {user.get('nickname') or ''} 👋 خوش اومدی به منوی اصلی 🙌",
        reply_markup=MAIN_MENU,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    tg_id = update.effective_user.id
    text = message.text or ""

    user = ensure_user(tg_id)
    state = user["state"]

    if state == "ask_gender":
        if text in ("👦 پسر", "👧 دختر"):
            gender = "male" if "پسر" in text else "female"
            update_user(tg_id, gender=gender, state="ask_nickname")
            await message.reply_text("چه اسمی (مستعار) دوست داری بین ۲ تا ۲۰ حرف صدات کنیم؟", reply_markup=REMOVE_MENU)
        else:
            await message.reply_text("لطفاً یکی از دکمه‌ها رو انتخاب کن.", reply_markup=GENDER_MENU)
        return

    if state == "ask_nickname":
        name = text.strip()
        if not name or len(name) < 2 or len(name) > 20:
            await message.reply_text("یه اسم بین ۲ تا ۲۰ حرف بنویس:")
            return
        update_user(tg_id, nickname=name, state="ask_age")
        await message.reply_text("چند سالته؟ (بین ۱۰ تا ۱۰۰)")
        return

    if state == "ask_age":
        if not text.isdigit() or not (10 <= int(text) <= 100):
            await message.reply_text("سن نامعتبره؛ یه عدد بین 10 تا 100 بفرست.")
            return
        update_user(tg_id, age=int(text), state="ask_city")
        await message.reply_text("اسم شهرت رو بنویس:")
        return

    if state == "ask_city":
        if not text.strip():
            await message.reply_text("لطفاً اسم شهر رو بنویس.")
            return
        update_user(tg_id, city=text.strip(), state="ask_province")
        await message.reply_text("استانت رو انتخاب کن:", reply_markup=PROVINCE_MENU)
        return

    if state == "ask_province":
        if text not in PROVINCES and text != OTHER_PROVINCE:
            await message.reply_text("لطفاً از دکمه‌ها انتخاب کن.", reply_markup=PROVINCE_MENU)
            return
        update_user(tg_id, province=text, state="idle", anon_code=gen_code())
        await message.reply_text("✅ پروفایلت کامل شد! خوش اومدی 🎉", reply_markup=MAIN_MENU)
        return

    if state == "profile_menu":
        if text == "✏️ ویرایش نام":
            update_user(tg_id, state="edit_nickname")
            await message.reply_text("اسم جدیدت رو بنویس:", reply_markup=REMOVE_MENU)
            return
        if text == "✏️ ویرایش عکس":
            update_user(tg_id, state="edit_photo")
            await message.reply_text("عکس جدیدت رو بفرست (یا بزن رد کردن):", reply_markup=SKIP_MENU)
            return
        if text == "✏️ ویرایش سن":
            update_user(tg_id, state="edit_age")
            await message.reply_text("سن جدیدت رو بفرست:", reply_markup=REMOVE_MENU)
            return
        if text == "✏️ ویرایش شهر":
            update_user(tg_id, state="edit_city")
            await message.reply_text("اسم شهر جدید رو بنویس:", reply_markup=REMOVE_MENU)
            return
        if text == "✏️ ویرایش استان":
            update_user(tg_id, state="edit_province")
            await message.reply_text("استان جدیدت رو انتخاب کن:", reply_markup=PROVINCE_MENU)
            return
        if text == "🔙 بازگشت به منو":
            update_user(tg_id, state="idle")
            await message.reply_text("برگشتی به منوی اصلی 👇", reply_markup=MAIN_MENU)
            return
        await message.reply_text("یکی از گزینه‌ها رو انتخاب کن 👇", reply_markup=PROFILE_EDIT_MENU)
        return

    if state == "edit_nickname":
        name = text.strip()
        if not name or len(name) < 2 or len(name) > 20:
            await message.reply_text("یه اسم بین ۲ تا ۲۰ حرف بنویس:")
            return
        update_user(tg_id, nickname=name, state="idle")
        await message.reply_text("✅ اسمت آپدیت شد.", reply_markup=MAIN_MENU)
        return

    if state == "edit_photo":
        if text == "⏭ رد کردن":
            update_user(tg_id, state="idle")
            await message.reply_text("باشه، عکس عوض نشد.", reply_markup=MAIN_MENU)
            return
        if message.photo:
            file_id = message.photo[-1].file_id
            update_user(tg_id, photo_file_id=file_id, state="idle")
            await message.reply_text("✅ عکس پروفایلت آپدیت شد.", reply_markup=MAIN_MENU)
            return
        await message.reply_text("یه عکس بفرست یا بزن رد کردن.", reply_markup=SKIP_MENU)
        return

    if state == "edit_age":
        if not text.isdigit() or not (10 <= int(text) <= 100):
            await message.reply_text("سن نامعتبره؛ یه عدد بین 10 تا 100 بفرست.")
            return
        update_user(tg_id, age=int(text), state="idle")
        await message.reply_text("✅ سنت آپدیت شد.", reply_markup=MAIN_MENU)
        return

    if state == "edit_city":
        if not text.strip():
            await message.reply_text("اسم شهر رو بنویس.")
            return
        update_user(tg_id, city=text.strip(), state="idle")
        await message.reply_text("✅ شهرت آپدیت شد.", reply_markup=MAIN_MENU)
        return

    if state == "edit_province":
        if text not in PROVINCES and text != OTHER_PROVINCE:
            await message.reply_text("لطفاً از دکمه‌ها انتخاب کن.", reply_markup=PROVINCE_MENU)
            return
        update_user(tg_id, province=text, state="idle")
        await message.reply_text("✅ استانت آپدیت شد.", reply_markup=MAIN_MENU)
        return

    if state == "sending_anon" and user["anon_target"]:
        cur = db.execute(
            "INSERT INTO anon_messages (to_tg_id, from_chat_id, from_message_id) VALUES (?, ?, ?)",
            (user["anon_target"], chat_id, message.message_id),
        )
        db.commit()
        row_id = cur.lastrowid

        await context.bot.send_message(
            chat_id=user["anon_target"],
            text="📩 یک پیام ناشناس دارید!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("👁 نمایش پیام", callback_data=f"reveal_{row_id}")]]
            ),
        )
        update_user(tg_id, state="idle", anon_target=None)
        await message.reply_text("✅ پیام شما به‌صورت ناشناس ارسال شد.")
        return

    if state == "support":
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"🆘 پیام پشتیبانی از کاربر با آیدی {tg_id}:")
        await context.bot.copy_message(chat_id=ADMIN_ID, from_chat_id=chat_id, message_id=message.message_id)
        update_user(tg_id, state="idle")
        await message.reply_text("✅ پیامت برای پشتیبانی ارسال شد. به‌زودی جواب می‌گیری.", reply_markup=MAIN_MENU)
        return

    if text in ("👧 چت با دختر 💕", "👦 چت با پسر 🔥"):
        if not user["gender"]:
            await message.reply_text("اول باید پروفایلت رو تکمیل کنی؛ /start رو بزن.")
            return
        if state == "chatting":
            await message.reply_text("شما الان توی یک چت هستید.", reply_markup=CHATTING_MENU)
            return

        want_gender = "female" if "دختر" in text else "male"
        match = find_match(tg_id, user["gender"], want_gender)

        if match:
            update_user(tg_id, state="chatting", partner_id=match["tg_id"], waiting_for=None)
            update_user(match["tg_id"], state="chatting", partner_id=tg_id, waiting_for=None)

            me_fresh = get_user(tg_id)
            partner_fresh = get_user(match["tg_id"])

            await message.reply_text(
                f"✅ به «{partner_fresh.get('nickname') or 'کاربر'}» (#{partner_fresh.get('user_code')}) وصل شدی!\n"
                "گفتگو رو شروع کن 💬",
                reply_markup=CHATTING_MENU,
            )
            await context.bot.send_message(
                chat_id=match["tg_id"],
                text=f"✅ به «{me_fresh.get('nickname') or 'کاربر'}» (#{me_fresh.get('user_code')}) وصل شدی!\n"
                     "گفتگو رو شروع کن 💬",
                reply_markup=CHATTING_MENU,
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text="می‌خوای پروفایل طرف مقابل رو ببینی؟",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("👤 نمایش پروفایل", callback_data=f"viewprofile_{match['tg_id']}")]]
                ),
            )
            await context.bot.send_message(
                chat_id=match["tg_id"],
                text="می‌خوای پروفایل طرف مقابل رو ببینی؟",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("👤 نمایش پروفایل", callback_data=f"viewprofile_{tg_id}")]]
                ),
            )
        else:
            update_user(tg_id, state="waiting", waiting_for=want_gender)
            await message.reply_text("⏳ در حال جستجوی همراه چت... یکم صبر کن! 🔍")
        return

    if text == "🔗 پیام ناشناس":
        code = user["anon_code"]
        if not code:
            code = gen_code()
            update_user(tg_id, anon_code=code)
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=anon_{code}"
        await message.reply_text(
            f"🔗 لینک پیام ناشناس شما:\n{link}\n\n"
            "این لینک رو برای دوستات بفرست؛ هر کی روش بزنه و پیام بده، به‌صورت ناشناس برات ارسال میشه."
        )
        return

    if text == "🎁 دعوت دوستان":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{tg_id}"
        count = db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (tg_id,)).fetchone()[0]
        await message.reply_text(
            f"🎁 لینک دعوت اختصاصی شما:\n{link}\n\n"
            f"👥 تعداد دعوت‌های موفق: {count} نفر\n\n"
            "این لینک رو برای دوستات بفرست؛ هر کسی باهاش وارد ربات بشه، به حساب دعوت‌های تو ثبت میشه!"
        )
        return

    if text == "👤 پروفایل من":
        referrals = db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (tg_id,)).fetchone()[0]
        caption = format_profile(user) + f"\n🎁 دعوت‌های موفق: {referrals}"
        if user.get("photo_file_id"):
            await context.bot.send_photo(chat_id=chat_id, photo=user["photo_file_id"], caption=caption)
        else:
            await message.reply_text(caption)
        update_user(tg_id, state="profile_menu")
        await message.reply_text("می‌خوای چیزی رو ویرایش کنی؟", reply_markup=PROFILE_EDIT_MENU)
        return

    if text == "🆘 پشتیبانی":
        update_user(tg_id, state="support")
        await message.reply_text("✍️ پیامتو برای پشتیبانی بنویس (متن، عکس و ...):")
        return

    if text in ("⏭ نفر بعدی", "⛔️ پایان چت"):
        if state != "chatting" or not user["partner_id"]:
            await message.reply_text("شما الان توی چتی نیستید.", reply_markup=MAIN_MENU)
            return
        partner_id = user["partner_id"]
        update_user(tg_id, state="idle", partner_id=None)
        update_user(partner_id, state="idle", partner_id=None)
        await context.bot.send_message(chat_id=partner_id, text="⚠️ طرف مقابل چت رو ترک کرد.", reply_markup=MAIN_MENU)

        if text == "⏭ نفر بعدی":
            await message.reply_text("🔁 برای پیدا کردن نفر بعدی، یکی از گزینه‌های چت رو بزن.", reply_markup=MAIN_MENU)
        else:
            await message.reply_text("چت پایان یافت.", reply_markup=MAIN_MENU)
        return

    if text == "🚩 گزارش کاربر":
        if state != "chatting" or not user[
