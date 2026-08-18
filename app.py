import os
import random
import string
import sqlite3
import asyncio
import logging

from flask import Flask, request
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
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

TOKEN = os.environ.get(
    "BOT_TOKEN",
    "PUT_YOUR_TOKEN_HERE"
)

ADMIN_ID = int(
    os.environ.get(
        "ADMIN_ID",
        "123456789"
    )
)

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DB_PATH = os.path.join(
    BASE_DIR,
    "anon_bot.db"
)

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    gender TEXT,
    age INTEGER,
    city TEXT,
    state TEXT DEFAULT 'new',
    partner_id INTEGER,
    waiting_for TEXT,
    anon_target INTEGER,
    anon_code TEXT UNIQUE,
    nickname TEXT,
    photo_file_id TEXT,
    province TEXT,
    user_code INTEGER,
    referred_by INTEGER
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


def get_user(tg_id):
    cur = db.execute(
        "SELECT * FROM users WHERE tg_id=?",
        (tg_id,)
    )
    row = cur.fetchone()

    if not row:
        return None

    cols = [
        x[0]
        for x in cur.description
    ]

    return dict(zip(cols, row))


def update_user(tg_id, **fields):
    if not fields:
        return

    keys = ", ".join(
        f"{k}=?"
        for k in fields
    )

    values = list(fields.values())
    values.append(tg_id)

    db.execute(
        f"UPDATE users SET {keys} WHERE tg_id=?",
        values
    )

    db.commit()


def ensure_user(tg_id):
    user = get_user(tg_id)

    if user:
        return user

    max_code = db.execute(
        "SELECT COALESCE(MAX(user_code),999) FROM users"
    ).fetchone()[0]

    code = max_code + 1

    db.execute(
        """
        INSERT INTO users
        (tg_id,state,user_code)
        VALUES (?, 'new', ?)
        """,
        (tg_id, code)
    )

    db.commit()

    return get_user(tg_id)


def anon_code():
    while True:
        code = "".join(
            random.choices(
                string.ascii_lowercase +
                string.digits,
                k=8
            )
        )

        exists = db.execute(
            "SELECT 1 FROM users WHERE anon_code=?",
            (code,)
        ).fetchone()

        if not exists:
            return code


def blocked(a, b):
    return db.execute(
        """
        SELECT 1 FROM blocked_pairs
        WHERE
        (reporter_id=? AND reported_id=?)
        OR
        (reporter_id=? AND reported_id=?)
        """,
        (a, b, b, a)
    ).fetchone() is not None


def find_match(my_id, gender, wanted):
    cur = db.execute(
        """
        SELECT * FROM users
        WHERE state='waiting'
        AND gender=?
        AND waiting_for=?
        AND tg_id!=?
        """,
        (wanted, gender, my_id)
    )

    cols = [
        x[0]
        for x in cur.description
    ]

    for row in cur.fetchall():
        user = dict(zip(cols, row))

        if not blocked(my_id, user["tg_id"]):
            return user

    return None


def profile(user):
    gender = {
        "male": "پسر",
        "female": "دختر"
    }.get(user.get("gender"), "-")

    return (
        f"👤 {user.get('nickname') or '-'}\n"
        f"🆔 #{user.get('user_code') or '-'}\n"
        f"⚧ جنسیت: {gender}\n"
        f"🎂 سن: {user.get('age') or '-'}\n"
        f"🏙 شهر: {user.get('city') or '-'}\n"
        f"🗺 استان: {user.get('province') or '-'}"
    )
PROVINCES = [
    "آذربایجان شرقی", "آذربایجان غربی", "اردبیل",
    "اصفهان", "البرز", "ایلام", "بوشهر", "تهران",
    "چهارمحال و بختیاری", "خراسان جنوبی", "خراسان رضوی",
    "خراسان شمالی", "خوزستان", "زنجان", "سمنان",
    "سیستان و بلوچستان", "فارس", "قزوین", "قم",
    "کردستان", "کرمان", "کرمانشاه",
    "کهگیلویه و بویراحمد", "گلستان", "گیلان",
    "لرستان", "مازندران", "مرکزی", "هرمزگان",
    "همدان", "یزد"
]

OTHER_PROVINCE = "🌍 خارج از ایران"


def chunks(items, size):
    return [
        items[i:i + size]
        for i in range(0, len(items), size)
    ]


PROVINCE_MENU = ReplyKeyboardMarkup(
    chunks(PROVINCES, 3) + [[OTHER_PROVINCE]],
    resize_keyboard=True
)

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["👧 چت با دختر 💕", "👦 چت با پسر 🔥"],
        ["🔗 پیام ناشناس", "🎁 دعوت دوستان"],
        ["👤 پروفایل من", "🆘 پشتیبانی"]
    ],
    resize_keyboard=True
)

CHAT_MENU = ReplyKeyboardMarkup(
    [
        ["⏭ نفر بعدی"],
        ["🚩 گزارش کاربر"],
        ["⛔️ پایان چت"]
    ],
    resize_keyboard=True
)

GENDER_MENU = ReplyKeyboardMarkup(
    [
        ["👦 پسر", "👧 دختر"]
    ],
    resize_keyboard=True
)

PROFILE_MENU = ReplyKeyboardMarkup(
    [
        ["✏️ ویرایش نام", "✏️ ویرایش عکس"],
        ["✏️ ویرایش سن", "✏️ ویرایش شهر"],
        ["✏️ ویرایش استان"],
        ["🔙 بازگشت به منو"]
    ],
    resize_keyboard=True
)

SKIP_MENU = ReplyKeyboardMarkup(
    [["⏭ رد کردن"]],
    resize_keyboard=True
)

REMOVE_MENU = ReplyKeyboardRemove()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    tg_id = update.effective_user.id

    user = ensure_user(tg_id)

    if context.args:

        param = context.args[0]

        # پیام ناشناس
        if param.startswith("anon_"):

            code = param[5:]

            row = db.execute(
                "SELECT * FROM users WHERE anon_code=?",
                (code,)
            ).fetchone()

            if not row:
                await update.message.reply_text(
                    "❌ این لینک معتبر نیست."
                )
                return

            cols = [
                x[0]
                for x in db.execute(
                    "SELECT * FROM users LIMIT 1"
                ).description
            ]

            owner = dict(zip(cols, row))

            if owner["tg_id"] == tg_id:
                await update.message.reply_text(
                    "❌ نمی‌تونی برای خودت پیام ناشناس بفرستی."
                )
                return

            update_user(
                tg_id,
                state="sending_anon",
                anon_target=owner["tg_id"]
            )

            await update.message.reply_text(
                "✉️ پیام ناشناست رو بفرست.\n"
                "متن، عکس، ویس و فایل هم می‌تونی بفرستی."
            )
            return

        # دعوت دوستان
        if param.startswith("ref_"):

            try:
                referrer = int(param[4:])
            except ValueError:
                referrer = None

            if (
                referrer
                and referrer != tg_id
                and not user.get("referred_by")
            ):
                update_user(
                    tg_id,
                    referred_by=referrer
                )

                try:
                    await context.bot.send_message(
                        referrer,
                        "🎉 یک نفر با لینک دعوتت وارد ربات شد!"
                    )
                except Exception:
                    pass

    user = ensure_user(tg_id)

    if not user.get("gender"):

        update_user(
            tg_id,
            state="ask_gender"
        )

        await update.message.reply_text(
            "👋 سلام و خوش اومدی!\n\n"
            "اول جنسیتت رو انتخاب کن:",
            reply_markup=GENDER_MENU
        )

        return

    update_user(
        tg_id,
        state="idle"
    )

    await update.message.reply_text(
        f"سلام {user.get('nickname') or ''} 👋\n"
        "به منوی اصلی خوش اومدی.",
        reply_markup=MAIN_MENU
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message

    if not message:
        return

    tg_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = message.text or ""

    user = ensure_user(tg_id)
    state = user.get("state")

    # انتخاب جنسیت
    if state == "ask_gender":

        if text not in ["👦 پسر", "👧 دختر"]:
            await message.reply_text(
                "لطفاً یکی از دکمه‌ها رو انتخاب کن.",
                reply_markup=GENDER_MENU
            )
            return

        gender = "male" if "پسر" in text else "female"

        update_user(
            tg_id,
            gender=gender,
            state="ask_nickname"
        )

        await message.reply_text(
            "اسم مستعارت رو بنویس.\n"
            "بین ۲ تا ۲۰ حرف:",
            reply_markup=REMOVE_MENU
        )
        return

    # نام
    if state == "ask_nickname":

        name = text.strip()

        if not 2 <= len(name) <= 20:
            await message.reply_text(
                "اسم باید بین ۲ تا ۲۰ حرف باشه."
            )
            return

        update_user(
            tg_id,
            nickname=name,
            state="ask_age"
        )

        await message.reply_text(
            "🎂 چند سالته؟\n"
            "بین ۱۰ تا ۱۰۰"
        )
        return

    # سن
    if state == "ask_age":

        if not text.isdigit() or not 10 <= int(text) <= 100:
            await message.reply_text(
                "❌ سن نامعتبره. عددی بین ۱۰ تا ۱۰۰ بفرست."
            )
            return

        update_user(
            tg_id,
            age=int(text),
            state="ask_city"
        )

        await message.reply_text(
            "🏙 اسم شهرت رو بنویس:"
        )
        return

    # شهر
    if state == "ask_city":

        if not text.strip():
            await message.reply_text(
                "اسم شهر رو بنویس."
            )
            return

        update_user(
            tg_id,
            city=text.strip(),
            state="ask_province"
        )

        await message.reply_text(
            "🗺 استانت رو انتخاب کن:",
            reply_markup=PROVINCE_MENU
        )
        return

    # استان
    if state == "ask_province":

        if text not in PROVINCES and text != OTHER_PROVINCE:
            await message.reply_text(
                "لطفاً از دکمه‌های استان انتخاب کن.",
                reply_markup=PROVINCE_MENU
            )
            return

        update_user(
            tg_id,
            province=text,
            state="idle",
            anon_code=anon_code()
        )

        await message.reply_text(
            "✅ پروفایلت کامل شد!\n"
            "حالا می‌تونی چت رو شروع کنی 🎉",
            reply_markup=MAIN_MENU
        )
        return

    # ارسال پیام ناشناس
    if state == "sending_anon" and user.get("anon_target"):

        cur = db.execute(
            """
            INSERT INTO anon_messages
            (to_tg_id, from_chat_id, from_message_id)
            VALUES (?, ?, ?)
            """,
            (
                user["anon_target"],
                chat_id,
                message.message_id
            )
        )

        db.commit()

        mid = cur.lastrowid

        try:

            await context.bot.send_message(
                user["anon_target"],
                "📩 یک پیام ناشناس داری!",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            "👁 نمایش پیام",
                            callback_data=f"reveal_{mid}"
                        )
                    ]]
                )
            )

            update_user(
                tg_id,
                state="idle",
                anon_target=None
            )

            await message.reply_text(
                "✅ پیام ناشناس ارسال شد."
            )

        except Exception:

            await message.reply_text(
                "❌ ارسال پیام انجام نشد."
            )

        return
    # =========================
    # SUPPORT
    # =========================

    if state == "support":

        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"🆘 پیام پشتیبانی\n"
                f"👤 کاربر: #{user.get('user_code')}\n"
                f"🆔 ID: {tg_id}"
            )

            await context.bot.copy_message(
                ADMIN_ID,
                chat_id,
                message.message_id
            )

            update_user(
                tg_id,
                state="idle"
            )

            await message.reply_text(
                "✅ پیامت برای پشتیبانی ارسال شد.",
                reply_markup=MAIN_MENU
            )

        except Exception:
            await message.reply_text(
                "❌ ارسال به پشتیبانی انجام نشد."
            )

        return


    # =========================
    # CHAT SEARCH
    # =========================

    if text in (
        "👧 چت با دختر 💕",
        "👦 چت با پسر 🔥"
    ):

        if not user.get("gender"):
            await message.reply_text(
                "اول پروفایلت رو کامل کن."
            )
            return

        if state == "chatting":
            await message.reply_text(
                "⚠️ الان داخل یک چت هستی.",
                reply_markup=CHAT_MENU
            )
            return

        wanted = (
            "female"
            if "دختر" in text
            else "male"
        )

        match = find_match(
            tg_id,
            user["gender"],
            wanted
        )

        if not match:

            update_user(
                tg_id,
                state="waiting",
                waiting_for=wanted
            )

            await message.reply_text(
                "⏳ در حال پیدا کردن همراه چت...\n"
                "یکم صبر کن 🔍"
            )

            return

        partner_id = match["tg_id"]

        update_user(
            tg_id,
            state="chatting",
            partner_id=partner_id,
            waiting_for=None
        )

        update_user(
            partner_id,
            state="chatting",
            partner_id=tg_id,
            waiting_for=None
        )

        me = get_user(tg_id)
        partner = get_user(partner_id)

        await message.reply_text(
            f"✅ به «{partner.get('nickname') or 'کاربر'}» "
            f"(#{partner.get('user_code')}) وصل شدی!\n\n"
            "💬 گفتگو رو شروع کن.",
            reply_markup=CHAT_MENU
        )

        await context.bot.send_message(
            partner_id,
            f"✅ به «{me.get('nickname') or 'کاربر'}» "
            f"(#{me.get('user_code')}) وصل شدی!\n\n"
            "💬 گفتگو رو شروع کن.",
            reply_markup=CHAT_MENU
        )

        # دکمه نمایش پروفایل
        try:

            await message.reply_text(
                "👤 می‌خوای پروفایل طرف مقابل رو ببینی؟",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            "👤 نمایش پروفایل",
                            callback_data=f"viewprofile_{partner_id}"
                        )
                    ]]
                )
            )

            await context.bot.send_message(
                partner_id,
                "👤 می‌خوای پروفایل طرف مقابل رو ببینی؟",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            "👤 نمایش پروفایل",
                            callback_data=f"viewprofile_{tg_id}"
                        )
                    ]]
                )
            )

        except Exception:
            pass

        return


    # =========================
    # ANONYMOUS LINK
    # =========================

    if text == "🔗 پیام ناشناس":

        code = user.get("anon_code")

        if not code:
            code = anon_code()

            update_user(
                tg_id,
                anon_code=code
            )

        me = await context.bot.get_me()

        link = (
            f"https://t.me/{me.username}"
            f"?start=anon_{code}"
        )

        await message.reply_text(
            "🔗 لینک پیام ناشناس شما:\n\n"
            f"{link}\n\n"
            "این لینک رو برای دوستات بفرست."
        )

        return


    # =========================
    # REFERRAL
    # =========================

    if text == "🎁 دعوت دوستان":

        me = await context.bot.get_me()

        link = (
            f"https://t.me/{me.username}"
            f"?start=ref_{tg_id}"
        )

        count = db.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE referred_by=?
            """,
            (tg_id,)
        ).fetchone()[0]

        await message.reply_text(
            "🎁 لینک دعوت اختصاصی شما:\n\n"
            f"{link}\n\n"
            f"👥 دعوت‌های موفق: {count} نفر"
        )

        return


    # =========================
    # MY PROFILE
    # =========================

    if text == "👤 پروفایل من":

        count = db.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE referred_by=?
            """,
            (tg_id,)
        ).fetchone()[0]

        caption = (
            profile(user)
            + f"\n🎁 دعوت موفق: {count}"
        )

        if user.get("photo_file_id"):

            await context.bot.send_photo(
                chat_id,
                user["photo_file_id"],
                caption=caption
            )

        else:

            await message.reply_text(
                caption
            )

        update_user(
            tg_id,
            state="profile_menu"
        )

        await message.reply_text(
            "✏️ چی رو می‌خوای ویرایش کنی؟",
            reply_markup=PROFILE_MENU
        )

        return


    # =========================
    # SUPPORT BUTTON
    # =========================

    if text == "🆘 پشتیبانی":

        update_user(
            tg_id,
            state="support"
        )

        await message.reply_text(
            "✍️ پیامت رو برای پشتیبانی بفرست.\n"
            "متن، عکس، ویس و فایل هم میشه."
        )

        return


    # =========================
    # PROFILE EDIT MENU
    # =========================

    if state == "profile_menu":

        if text == "✏️ ویرایش نام":

            update_user(
                tg_id,
                state="edit_nickname"
            )

            await message.reply_text(
                "اسم جدیدت رو بنویس:",
                reply_markup=REMOVE_MENU
            )

            return

        if text == "✏️ ویرایش عکس":

            update_user(
                tg_id,
                state="edit_photo"
            )

            await message.reply_text(
                "عکس جدیدت رو بفرست:",
                reply_markup=SKIP_MENU
            )

            return

        if text == "✏️ ویرایش سن":

            update_user(
                tg_id,
                state="edit_age"
            )

            await message.reply_text(
                "سن جدیدت رو بفرست:",
                reply_markup=REMOVE_MENU
            )

            return

        if text == "✏️ ویرایش شهر":

            update_user(
                tg_id,
                state="edit_city"
            )

            await message.reply_text(
                "شهر جدیدت رو بنویس:",
                reply_markup=REMOVE_MENU
            )

            return

        if text == "✏️ ویرایش استان":

            update_user(
                tg_id,
                state="edit_province"
            )

            await message.reply_text(
                "استان جدیدت رو انتخاب کن:",
                reply_markup=PROVINCE_MENU
            )

            return

        if text == "🔙 بازگشت به منو":

            update_user(
                tg_id,
                state="idle"
            )

            await message.reply_text(
                "برگشتی به منوی اصلی 👇",
                reply_markup=MAIN_MENU
            )

            return

        await message.reply_text(
            "یکی از گزینه‌ها رو انتخاب کن.",
            reply_markup=PROFILE_MENU
        )

        return


    # =========================
    # EDIT NICKNAME
    # =========================

    if state == "edit_nickname":

        name = text.strip()

        if not 2 <= len(name) <= 20:
            await message.reply_text(
                "اسم باید بین ۲ تا ۲۰ حرف باشه."
            )
            return

        update_user(
            tg_id,
            nickname=name,
            state="idle"
        )

        await message.reply_text(
            "✅ اسم آپدیت شد.",
            reply_markup=MAIN_MENU
        )

        return


    # =========================
    # EDIT AGE
    # =========================

    if state == "edit_age":

        if not text.isdigit() or not 10 <= int(text) <= 100:

            await message.reply_text(
                "سن باید بین ۱۰ تا ۱۰۰ باشه."
            )

            return

        update_user(
            tg_id,
            age=int(text),
            state="idle"
        )

        await message.reply_text(
            "✅ سن آپدیت شد.",
            reply_markup=MAIN_MENU
        )

        return


    # =========================
    # EDIT CITY
    # =========================

    if state == "edit_city":

        if not text.strip():

            await message.reply_text(
                "اسم شهر رو بنویس."
            )

            return

        update_user(
            tg_id,
            city=text.strip(),
            state="idle"
        )

        await message.reply_text(
            "✅ شهر آپدیت شد.",
            reply_markup=MAIN_MENU
        )

        return
    # =========================
    # EDIT PHOTO
    # =========================

    if state == "edit_photo":

        if text == "⏭ رد کردن":

            update_user(
                tg_id,
                state="idle"
            )

            await message.reply_text(
                "باشه، عکس عوض نشد.",
                reply_markup=MAIN_MENU
            )

            return

        if message.photo:

            update_user(
                tg_id,
                photo_file_id=message.photo[-1].file_id,
                state="idle"
            )

            await message.reply_text(
                "✅ عکس پروفایل آپدیت شد.",
                reply_markup=MAIN_MENU
            )

            return

        await message.reply_text(
            "📷 یک عکس بفرست.",
            reply_markup=SKIP_MENU
        )

        return


    # =========================
    # EDIT PROVINCE
    # =========================

    if state == "edit_province":

        if (
            text not in PROVINCES
            and text != OTHER_PROVINCE
        ):

            await message.reply_text(
                "لطفاً استان رو از دکمه‌ها انتخاب کن.",
                reply_markup=PROVINCE_MENU
            )

            return

        update_user(
            tg_id,
            province=text,
            state="idle"
        )

        await message.reply_text(
            "✅ استان آپدیت شد.",
            reply_markup=MAIN_MENU
        )

        return


    # =========================
    # CHAT CONTROLS
    # =========================

    if text in (
        "⏭ نفر بعدی",
        "⛔️ پایان چت",
        "🚩 گزارش کاربر"
    ):

        if (
            state != "chatting"
            or not user.get("partner_id")
        ):

            await message.reply_text(
                "❌ شما الان داخل چتی نیستید.",
                reply_markup=MAIN_MENU
            )

            return

        partner_id = user["partner_id"]


        # گزارش
        if text == "🚩 گزارش کاربر":

            if not blocked(tg_id, partner_id):

                db.execute(
                    """
                    INSERT INTO blocked_pairs
                    (reporter_id, reported_id)
                    VALUES (?, ?)
                    """,
                    (
                        tg_id,
                        partner_id
                    )
                )

                db.commit()

            reply = (
                "🚩 کاربر گزارش شد.\n"
                "دیگه هیچ‌وقت به هم وصل نمی‌شید."
            )


        # نفر بعدی
        elif text == "⏭ نفر بعدی":

            reply = (
                "🔁 چت قبلی تموم شد.\n"
                "از منوی اصلی یک نفر جدید پیدا کن."
            )


        # پایان
        else:

            reply = "⛔️ چت پایان یافت."


        update_user(
            tg_id,
            state="idle",
            partner_id=None,
            waiting_for=None
        )

        update_user(
            partner_id,
            state="idle",
            partner_id=None,
            waiting_for=None
        )

        try:

            await context.bot.send_message(
                partner_id,
                "⚠️ طرف مقابل چت رو ترک کرد.",
                reply_markup=MAIN_MENU
            )

        except Exception:

            pass


        await message.reply_text(
            reply,
            reply_markup=MAIN_MENU
        )

        return


    # =========================
    # FORWARD CHAT
    # =========================

    if (
        state == "chatting"
        and user.get("partner_id")
    ):

        try:

            await context.bot.copy_message(
                chat_id=user["partner_id"],
                from_chat_id=chat_id,
                message_id=message.message_id
            )

        except Exception:

            await message.reply_text(
                "❌ ارسال پیام انجام نشد."
            )

        return


    # =========================
    # DEFAULT
    # =========================

    await message.reply_text(
        "از منوی زیر یکی رو انتخاب کن 👇",
        reply_markup=MAIN_MENU
    )


# =========================================================
# CALLBACK BUTTONS
# =========================================================

async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    tg_id = query.from_user.id

    chat_id = query.message.chat_id


    # =========================
    # REVEAL ANONYMOUS MESSAGE
    # =========================

    if data.startswith("reveal_"):

        try:

            message_id = int(
                data.replace(
                    "reveal_",
                    "",
                    1
                )
            )

        except ValueError:

            await query.answer(
                "❌ پیام نامعتبره.",
                show_alert=True
            )

            return


        cur = db.execute(
            """
            SELECT *
            FROM anon_messages
            WHERE id=?
            """,
            (message_id,)
        )

        row = cur.fetchone()

        if not row:

            await query.answer(
                "❌ پیام پیدا نشد.",
                show_alert=True
            )

            return


        cols = [
            x[0]
            for x in cur.description
        ]

        msg = dict(
            zip(cols, row)
        )


        if msg["to_tg_id"] != tg_id:

            await query.answer(
                "❌ این پیام برای شما نیست.",
                show_alert=True
            )

            return


        try:

            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=msg["from_chat_id"],
                message_id=msg["from_message_id"]
            )

            db.execute(
                """
                UPDATE anon_messages
                SET revealed=1
                WHERE id=?
                """,
                (message_id,)
            )

            db.commit()

            await query.answer()

        except Exception:

            await query.answer(
                "❌ پیام دیگر در دسترس نیست.",
                show_alert=True
            )

        return


    # =========================
    # VIEW PROFILE
    # =========================

    if data.startswith("viewprofile_"):

        try:

            target_id = int(
                data.replace(
                    "viewprofile_",
                    "",
                    1
                )
            )

        except ValueError:

            await query.answer(
                "❌ پروفایل نامعتبره.",
                show_alert=True
            )

            return


        viewer = get_user(tg_id)


        if (
            not viewer
            or viewer.get("partner_id") != target_id
        ):

            await query.answer(
                "❌ این پروفایل دیگر در دسترس نیست.",
                show_alert=True
            )

            return


        target = get_user(target_id)

        if not target:

            await query.answer(
                "❌ کاربر پیدا نشد.",
                show_alert=True
            )

            return


        text_profile = profile(target)


        if target.get("photo_file_id"):

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=target["photo_file_id"],
                caption=text_profile
            )

        else:

            await context.bot.send_message(
                chat_id=chat_id,
                text=text_profile
            )


        await query.answer()

        return


    await query.answer()


# =========================================================
# ADMIN STATS
# =========================================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return


    total = db.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]


    profiles = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE gender IS NOT NULL
        """
    ).fetchone()[0]


    chatting = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE state='chatting'
        """
    ).fetchone()[0]


    waiting = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE state='waiting'
        """
    ).fetchone()[0]


    males = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE gender='male'
        """
    ).fetchone()[0]


    females = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE gender='female'
        """
    ).fetchone()[0]


    referrals = db.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE referred_by IS NOT NULL
        """
    ).fetchone()[0]


    await update.message.reply_text(
        "📊 آمار ربات\n\n"
        f"👥 کل کاربران: {total}\n"
        f"✅ پروفایل کامل: {profiles}\n"
        f"💬 در حال چت: {chatting // 2}\n"
        f"⏳ منتظر: {waiting}\n\n"
        f"👦 پسر: {males}\n"
        f"👧 دختر: {females}\n"
        f"🎁 دعوت‌شده: {referrals}"
    )
# =========================================================
# TELEGRAM APPLICATION
# =========================================================

telegram_app = (
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)

telegram_app.add_handler(
    CommandHandler("start", start)
)

telegram_app.add_handler(
    CommandHandler("stats", stats)
)

telegram_app.add_handler(
    CallbackQueryHandler(handle_callback)
)

telegram_app.add_handler(
    MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_message
    )
)


# =========================================================
# FLASK APP
# =========================================================

app = Flask(__name__)

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)


def run_async(coro):
    return loop.run_until_complete(coro)


# Initialize Telegram
run_async(
    telegram_app.initialize()
)


# =========================================================
# HOME
# =========================================================

@app.route("/")
def index():

    return (
        "🤖 ربات چت ناشناس فعاله ✅"
    )


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    return "OK"


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    f"/webhook/{TOKEN}",
    methods=["POST"]
)
def webhook():

    if not request.is_json:
        return "Bad Request", 400

    try:

        data = request.get_json()

        update = Update.de_json(
            data,
            telegram_app.bot
        )

        run_async(
            telegram_app.process_update(
                update
            )
        )

        return "OK", 200

    except Exception as e:

        logger.exception(
            "Webhook error"
        )

        return "ERROR", 500


# =========================================================
# SET WEBHOOK
# =========================================================

@app.route("/set_webhook")
def set_webhook():

    base_url = request.args.get(
        "url",
        ""
    ).strip()

    if not base_url:

        return (
            "❌ آدرس سایت رو وارد نکردی.<br><br>"
            "مثال:<br>"
            "?url=https://USERNAME.pythonanywhere.com"
        ), 400


    webhook_url = (
        base_url.rstrip("/")
        + f"/webhook/{TOKEN}"
    )


    try:

        result = run_async(
            telegram_app.bot.set_webhook(
                webhook_url,
                allowed_updates=Update.ALL_TYPES
            )
        )

        return (
            "✅ Webhook با موفقیت تنظیم شد!<br><br>"
            f"🔗 {webhook_url}<br><br>"
            f"Telegram: {result}"
        )

    except Exception as e:

        logger.exception(
            "Webhook setup error"
        )

        return (
            f"❌ خطا در تنظیم Webhook:<br>{e}"
        ), 500