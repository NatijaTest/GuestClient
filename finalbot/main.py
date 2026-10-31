# -*- coding: utf-8 -*-
"""
Minecraft chit obunasi sotuvchi Telegram bot
Kutubxonalar: pyTelegramBotAPI (telebot), sqlite3, os, re

O'rnatish:   pip install pyTelegramBotAPI
Ishga tushirish:   python main.py

Ish tartibi:
  1. Foydalanuvchi balansni to'ldiradi va chekni RASM qilib botga yuboradi.
  2. Bot rasmni adminga (send_photo) yuboradi: ostida "❌ Rad etish" tugmasi bor.
  3. Admin rasmga REPLY qilib summani yozadi (masalan: 200000).
  4. Bot caption'dan foydalanuvchi ID sini topadi, balansga summani qo'shadi,
     foydalanuvchiga xabar va GuestLauncher.exe faylini yuboradi.
"""

import html
import logging
import os
import re
import sqlite3
import threading

import telebot
from telebot import types

# =====================================================================
#                          SOZLAMALAR (O'ZGARTIRING)
# =====================================================================
# Token @BotFather dan olinadi. Xavfsizlik uchun muhit o'zgaruvchisidan ham o'qiladi.
BOT_TOKEN = os.getenv("BOT_TOKEN", "9116546041:AAG0yMpoZhoAnrBSKtmXTjdcmzWdxp3SBks")

# Adminlarning Telegram ID raqamlari (ID ni @userinfobot orqali bilib olish mumkin)
ADMIN_IDS = [8048079453]

# To'lov ma'lumotlari ("Balansni to'ldirish" tugmasida chiqadi)
CARD_NUMBER = "8600 0000 0000 0000"
CARD_OWNER = "ISM FAMILIYA"

# Aloqa uchun username (@ belgisiz)
CONTACT_USERNAME = "Guest5214"

# Fayl main.py bilan bir papkada turishi kerak
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCHER_PATH = os.path.join(BASE_DIR, "GuestLauncher.exe")
# Zaxira havola: kompyuterda fayl topilmasa (yoki yuborib bo'lmasa) shu havola yuboriladi
LAUNCHER_URL = "https://github.com/NatijaTest/GuestClient/releases/download/v1.0/GuestLauncher.exe"

DB_PATH = os.path.join(BASE_DIR, "bot_database.db")

# Tariflar: kalit -> ma'lumot
PLANS = {
    "5m": {"title": "5 oy", "price": 130_000, "emoji": "🛒"},
    "lifetime": {"title": "Lifetime", "price": 1_300_000, "emoji": "🔥"},
}

# Bir martalik maksimal summa (xato yozib qo'yishdan himoya)
MAX_AMOUNT = 100_000_000

# Admin /add, /ayir, /allm qilganda foydalanuvchiga xabar yuborilsinmi?
NOTIFY_USER_ON_ADMIN_CHANGE = True

# =====================================================================
#                              TAYYORGARLIK
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("minecraft-shop-bot")

if ":" not in BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN kiritilmagan! main.py ichida BOT_TOKEN ni to'ldiring.")

bot = telebot.TeleBot(BOT_TOKEN)
BOT_ID = None  # ishga tushganda get_me() orqali to'ldiriladi

# =====================================================================
#                          MA'LUMOTLAR BAZASI (SQLite)
# =====================================================================
# Bot bir nechta oqimda ishlaydi, shuning uchun bitta ulanish + Lock ishlatamiz.
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
db_lock = threading.Lock()


def init_db():
    """Jadvallarni yaratadi."""
    with db_lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                balance    INTEGER NOT NULL DEFAULT 0,
                plan       TEXT,
                plan_at    TEXT,
                joined_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Cheklar: bir chekni ikki marta tasdiqlab (ikki marta pul qo'shib) yuborishning oldini oladi
            CREATE TABLE IF NOT EXISTS receipts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / rejected / failed
                amount     INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Chek rasmi har bir adminga alohida xabar bo'lib boradi; holatni hammasida yangilash uchun saqlaymiz
            CREATE TABLE IF NOT EXISTS receipt_msgs (
                receipt_id INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            );
            """
        )
        conn.commit()


def db_query(sql, params=(), one=False):
    """SELECT so'rovi."""
    with db_lock:
        cur = conn.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()


def db_write(sql, params=()):
    """INSERT/UPDATE/DELETE so'rovi. Ta'sirlangan qatorlar sonini qaytaradi."""
    with db_lock:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount


# ----------------------------- Foydalanuvchilar ----------------------
def register_user(tg_user):
    """Foydalanuvchini bazaga qo'shadi yoki username/ismini yangilaydi."""
    username = tg_user.username if tg_user.username else None
    db_write(
        """
        INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
        """,
        (tg_user.id, username, tg_user.first_name or ""),
    )


def get_user(user_id):
    return db_query("SELECT * FROM users WHERE user_id = ?", (user_id,), one=True)


def get_all_users():
    return db_query("SELECT * FROM users ORDER BY joined_at, user_id")


def resolve_user(identifier):
    """ID (raqam) yoki @username bo'yicha foydalanuvchini topadi."""
    ident = identifier.strip()
    if re.fullmatch(r"[0-9]+", ident):
        return get_user(int(ident))
    ident = ident.lstrip("@")
    if not ident:
        return None
    return db_query(
        "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (ident,), one=True
    )


# ------------------------------- Balans ------------------------------
def get_balance(user_id):
    row = get_user(user_id)
    return row["balance"] if row else 0


def set_balance(user_id, amount):
    db_write("UPDATE users SET balance = ? WHERE user_id = ?", (amount, user_id))
    return get_balance(user_id)


def add_balance(user_id, amount):
    db_write("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    return get_balance(user_id)


def subtract_balance(user_id, amount):
    """Balans yetarli bo'lsagina ayiradi. Muvaffaqiyatli bo'lsa True qaytaradi."""
    changed = db_write(
        "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
        (amount, user_id, amount),
    )
    return changed > 0


def purchase_plan(user_id, plan_title, price):
    """Tarifni sotib olish: balans yetarli bo'lsa ayiradi va tarifni yozib qo'yadi."""
    changed = db_write(
        """
        UPDATE users
        SET balance = balance - ?, plan = ?, plan_at = datetime('now')
        WHERE user_id = ? AND balance >= ?
        """,
        (price, plan_title, user_id, price),
    )
    return changed > 0


# -------------------------------- Cheklar ----------------------------
def create_receipt(user_id):
    with db_lock:
        cur = conn.execute("INSERT INTO receipts (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return cur.lastrowid


def get_receipt(receipt_id):
    return db_query("SELECT * FROM receipts WHERE id = ?", (receipt_id,), one=True)


def set_receipt_status(receipt_id, status):
    """Faqat 'pending' holatdagi chekning holatini o'zgartiradi. Muvaffaqiyatli bo'lsa True."""
    return db_write(
        "UPDATE receipts SET status = ? WHERE id = ? AND status = 'pending'",
        (status, receipt_id),
    ) > 0


def save_receipt_msg(receipt_id, chat_id, message_id):
    db_write(
        "INSERT INTO receipt_msgs (receipt_id, chat_id, message_id) VALUES (?, ?, ?)",
        (receipt_id, chat_id, message_id),
    )


def approve_receipt(receipt_id, amount):
    """
    Chekni tasdiqlaydi va balansga pulni qo'shadi (bitta tranzaksiyada).
    Yangi balansni qaytaradi. Chek allaqachon ko'rib chiqilgan bo'lsa None qaytaradi.
    """
    with db_lock:
        try:
            cur = conn.execute(
                "UPDATE receipts SET status = 'approved', amount = ? "
                "WHERE id = ? AND status = 'pending'",
                (amount, receipt_id),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return None
            conn.execute(
                "UPDATE users SET balance = balance + ? "
                "WHERE user_id = (SELECT user_id FROM receipts WHERE id = ?)",
                (amount, receipt_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        row = conn.execute(
            "SELECT balance FROM users "
            "WHERE user_id = (SELECT user_id FROM receipts WHERE id = ?)",
            (receipt_id,),
        ).fetchone()
        return row["balance"] if row else None


# =====================================================================
#                             YORDAMCHI FUNKSIYALAR
# =====================================================================
def is_admin(user_id):
    return user_id in ADMIN_IDS


def fmt(number):
    """130000 -> '130,000'"""
    return f"{int(number):,}"


def parse_amount(text, allow_zero=False):
    """
    Matndan summani ajratib oladi: '200000', '200 000', '200,000', '200000 so'm' -> 200000.
    Noto'g'ri, manfiy yoki juda katta bo'lsa None qaytaradi.
    """
    if not text:
        return None
    cleaned = re.sub(r"(so['’ʻʼ`]?m|sum|uzs)\s*$", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"[\s,._]", "", cleaned)
    if not re.fullmatch(r"[0-9]{1,12}", cleaned):
        return None
    value = int(cleaned)
    if value == 0 and not allow_zero:
        return None
    if value > MAX_AMOUNT:
        return None
    return value


def safe_send(chat_id, text, **kwargs):
    """Xabar yuboradi; xato bo'lsa (masalan, foydalanuvchi botni bloklagan) False qaytaradi."""
    try:
        bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as e:
        log.warning("Xabar yuborilmadi (chat_id=%s): %s", chat_id, e)
        return False


def send_long(chat_id, lines, limit=3800):
    """Uzun ro'yxatni Telegram limitidan oshmasligi uchun bo'laklab yuboradi."""
    chunk = ""
    for line in lines:
        if chunk and len(chunk) + len(line) + 1 > limit:
            safe_send(chat_id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        safe_send(chat_id, chunk)


def send_launcher(chat_id):
    """
    GuestLauncher.exe ni hujjat (send_document) ko'rinishida yuboradi.
    Fayl kompyuterda topilmasa yoki yuborib bo'lmasa, yuklash havolasini yuboradi.
    """
    if os.path.isfile(LAUNCHER_PATH):
        try:
            with open(LAUNCHER_PATH, "rb") as f:
                bot.send_document(
                    chat_id,
                    f,
                    caption="📦 GuestLauncher.exe — yuklab oling va ishga tushiring.",
                    timeout=300,
                )
            return True
        except Exception as e:
            log.error("GuestLauncher.exe ni yuborishda xato (chat_id=%s): %s", chat_id, e)
    else:
        log.error("Fayl topilmadi: %s", LAUNCHER_PATH)

    # Zaxira variant: havola
    return safe_send(
        chat_id,
        f"📦 GuestLauncher.exe ni shu havola orqali yuklab oling:\n{LAUNCHER_URL}",
    )


def main_menu(user_id):
    """Asosiy menyu (inline tugmalar)."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            f"💰 Balans: {fmt(get_balance(user_id))} so'm", callback_data="balance"
        )
    )
    kb.add(types.InlineKeyboardButton("💳 Balansni to'ldirish", callback_data="topup"))
    for key, plan in PLANS.items():
        kb.add(
            types.InlineKeyboardButton(
                f"{plan['emoji']} {plan['title']} ({fmt(plan['price'])} so'm)",
                callback_data=f"buy_{key}",
            )
        )
    kb.add(
        types.InlineKeyboardButton(
            f"💬 Aloqa (@{CONTACT_USERNAME})", url=f"https://t.me/{CONTACT_USERNAME}"
        )
    )
    return kb


def topup_text():
    return (
        "💳 <b>Balansni to'ldirish</b>\n\n"
        f"Karta raqami: <code>{html.escape(CARD_NUMBER)}</code>\n"
        f"Karta egasi: <b>{html.escape(CARD_OWNER)}</b>\n\n"
        "1️⃣ Yuqoridagi kartaga istalgan summani o'tkazing.\n"
        "2️⃣ To'lov chekini (skrinshot) shu yerga <b>rasm</b> ko'rinishida yuboring.\n"
        "3️⃣ Admin tekshirgach, summa balansingizga qo'shiladi va "
        "<b>GuestLauncher.exe</b> fayli yuboriladi.\n\n"
        f"❓ Savollar bo'lsa: @{html.escape(CONTACT_USERNAME)}"
    )


# ----------------------------- Chek caption'i ------------------------
def render_receipt_caption(receipt_id):
    """
    Adminga boradigan rasm ostidagi matn. Foydalanuvchi ID si shu captiondan
    ("🆔 ID: ...") avtomatik topiladi.
    """
    receipt = get_receipt(receipt_id)
    user = get_user(receipt["user_id"])
    name = (user["first_name"] if user and user["first_name"] else "Noma'lum")
    uname = f"@{user['username']}" if user and user["username"] else "username yo'q"

    lines = [
        f"🧾 Chek #{receipt_id}",
        f"👤 {name} ({uname})",
        f"🆔 ID: {receipt['user_id']}",
        "",
    ]
    if receipt["status"] == "approved":
        lines.append(f"✅ TASDIQLANDI — balansga {fmt(receipt['amount'])} so'm qo'shildi")
    elif receipt["status"] == "rejected":
        lines.append("❌ RAD ETILDI — feyk chek")
    else:
        lines.append(
            "Tasdiqlash uchun ushbu rasmga javob tariqasida (reply) "
            "chekdagi summani yozib yuboring (masalan: 200000)"
        )
    return "\n".join(lines)


def refresh_receipt_messages(receipt_id):
    """Chek holati o'zgargach, barcha adminlardagi rasm captionini yangilaydi va tugmani olib tashlaydi."""
    caption = render_receipt_caption(receipt_id)
    rows = db_query(
        "SELECT chat_id, message_id FROM receipt_msgs WHERE receipt_id = ?", (receipt_id,)
    )
    for row in rows:
        try:
            # reply_markup berilmasa, inline tugmalar (Rad etish) olib tashlanadi
            bot.edit_message_caption(
                caption=caption,
                chat_id=row["chat_id"],
                message_id=row["message_id"],
            )
        except Exception as e:
            log.warning("Caption yangilanmadi (%s/%s): %s", row["chat_id"], row["message_id"], e)


# =====================================================================
#                          FOYDALANUVCHI KOMANDALARI
# =====================================================================
@bot.message_handler(commands=["start", "buy"])
def cmd_start(message):
    register_user(message.from_user)
    name = message.from_user.first_name or "do'st"
    bot.send_message(
        message.chat.id,
        f"👋 Salom, {name}!\n\n"
        "🎮 Minecraft chit obunasi do'koniga xush kelibsiz.\n"
        "Kerakli bo'limni tanlang:",
        reply_markup=main_menu(message.from_user.id),
    )


@bot.message_handler(commands=["balance"])
def cmd_balance(message):
    register_user(message.from_user)
    bot.reply_to(
        message, f"💰 Sizning balansingiz: {fmt(get_balance(message.from_user.id))} so'm"
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.reply_to(message, f"@{CONTACT_USERNAME} ga yozing")


# =====================================================================
#                      INLINE TUGMALAR (callback) HANDLERLARI
# =====================================================================
@bot.callback_query_handler(func=lambda c: (c.data or "") == "balance")
def cb_balance(call):
    register_user(call.from_user)
    balance = get_balance(call.from_user.id)
    bot.answer_callback_query(
        call.id, f"💰 Sizning balansingiz: {fmt(balance)} so'm", show_alert=True
    )
    # Menyudagi balans yozuvini yangilab qo'yamiz
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=main_menu(call.from_user.id),
        )
    except Exception:
        pass  # "message is not modified" — xavfli emas


@bot.callback_query_handler(func=lambda c: (c.data or "") == "topup")
def cb_topup(call):
    register_user(call.from_user)
    bot.answer_callback_query(call.id)
    safe_send(call.from_user.id, topup_text(), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("buy_"))
def cb_buy(call):
    register_user(call.from_user)
    user_id = call.from_user.id
    plan = PLANS.get(call.data[len("buy_"):])
    if plan is None:
        bot.answer_callback_query(call.id, "❌ Noma'lum tarif.", show_alert=True)
        return

    price = plan["price"]
    if not purchase_plan(user_id, plan["title"], price):
        balance = get_balance(user_id)
        bot.answer_callback_query(
            call.id,
            f"❌ Balans yetarli emas!\nKerak: {fmt(price)} so'm\nSizda: {fmt(balance)} so'm",
            show_alert=True,
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💳 Balansni to'ldirish", callback_data="topup"))
        safe_send(
            user_id,
            f"❌ «{plan['title']}» tarifi uchun balansingizda "
            f"{fmt(price - balance)} so'm yetishmaydi.",
            reply_markup=kb,
        )
        return

    bot.answer_callback_query(call.id, "✅ Xarid amalga oshirildi!")
    new_balance = get_balance(user_id)
    safe_send(
        user_id,
        f"🎉 Xarid muvaffaqiyatli!\n\n"
        f"📦 Tarif: {plan['title']}\n"
        f"💸 Narxi: {fmt(price)} so'm\n"
        f"💰 Qolgan balans: {fmt(new_balance)} so'm\n\n"
        f"Fayl quyida yuboriladi. Savollar bo'lsa: @{CONTACT_USERNAME}",
    )
    send_launcher(user_id)

    # Adminlarga xabar
    user = call.from_user
    uname = f"@{user.username}" if user.username else "username yo'q"
    for admin_id in ADMIN_IDS:
        safe_send(
            admin_id,
            f"🛒 Yangi xarid!\n"
            f"👤 {user.first_name or 'Noma`lum'} ({uname})\n"
            f"🆔 {user_id}\n"
            f"📦 Tarif: {plan['title']} — {fmt(price)} so'm\n"
            f"💰 Qolgan balans: {fmt(new_balance)} so'm",
        )


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("reject_"))
def cb_reject(call):
    """Admin '❌ Rad etish' tugmasini bosganda."""
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Bu tugma faqat adminlar uchun.", show_alert=True)
        return

    try:
        receipt_id = int(call.data[len("reject_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "❌ Noto'g'ri ma'lumot.", show_alert=True)
        return

    receipt = get_receipt(receipt_id)
    if receipt is None:
        bot.answer_callback_query(call.id, "❌ Chek topilmadi.", show_alert=True)
        return

    if not set_receipt_status(receipt_id, "rejected"):
        bot.answer_callback_query(call.id, "⚠️ Bu chek allaqachon ko'rib chiqilgan.", show_alert=True)
        refresh_receipt_messages(receipt_id)
        return

    # Foydalanuvchiga xabar va rasm holatini yangilash
    safe_send(
        receipt["user_id"],
        "❌ Chekingiz rad etildi.\n"
        "Yuborilgan chek feyk (soxta) deb topildi.\n\n"
        f"Agar xato deb hisoblasangiz, @{CONTACT_USERNAME} ga yozing.",
    )
    refresh_receipt_messages(receipt_id)
    bot.answer_callback_query(call.id, "❌ Chek rad etildi.")


# =====================================================================
#                         CHEK (RASM) QABUL QILISH
# =====================================================================
@bot.message_handler(content_types=["photo"], func=lambda m: m.chat.type == "private")
def handle_receipt_photo(message):
    """Foydalanuvchi yuborgan istalgan rasm — chek deb hisoblanib, adminga yo'naltiriladi."""
    user = message.from_user
    register_user(user)

    file_id = message.photo[-1].file_id  # eng katta o'lchamdagi rasm
    receipt_id = create_receipt(user.id)
    caption = render_receipt_caption(receipt_id)

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_{receipt_id}"))

    delivered = False
    for admin_id in ADMIN_IDS:
        try:
            sent = bot.send_photo(admin_id, file_id, caption=caption, reply_markup=kb)
            save_receipt_msg(receipt_id, admin_id, sent.message_id)
            delivered = True
        except Exception as e:
            log.error("Chek adminga (%s) yuborilmadi: %s", admin_id, e)

    if delivered:
        bot.reply_to(
            message,
            "✅ Chekingiz adminga yuborildi.\n"
            "Tekshirilgach, balansingizga pul qo'shiladi va sizga xabar beriladi.",
        )
    else:
        db_write("UPDATE receipts SET status = 'failed' WHERE id = ?", (receipt_id,))
        bot.reply_to(
            message,
            f"⚠️ Chekni adminga yuborib bo'lmadi. Iltimos, @{CONTACT_USERNAME} ga yozing.",
        )


@bot.message_handler(content_types=["document"], func=lambda m: m.chat.type == "private")
def handle_document(message):
    bot.reply_to(message, "📸 Iltimos, chekni fayl emas, oddiy RASM (skrinshot) ko'rinishida yuboring.")


# =====================================================================
#          ADMIN: CHEK RASMIGA REPLY QILIB SUMMA YOZISH (TASDIQLASH)
# =====================================================================
def _is_admin_reply_to_receipt(m):
    """Admin bot yuborgan rasmga reply qilib oddiy matn yozganmi?"""
    replied = m.reply_to_message
    return (
        is_admin(m.from_user.id)
        and replied is not None
        and bool(replied.photo)
        and replied.from_user is not None
        and replied.from_user.id == BOT_ID
        and not (m.text or "").startswith("/")
    )


@bot.message_handler(content_types=["text"], func=_is_admin_reply_to_receipt)
def handle_admin_reply(message):
    caption = message.reply_to_message.caption or ""

    # Caption'dan chek raqami va foydalanuvchi ID sini ajratib olamiz
    m_receipt = re.search(r"^🧾 Chek #(\d+)", caption, re.MULTILINE)
    m_user = re.search(r"^🆔 ID:\s*(\d+)", caption, re.MULTILINE)
    if not m_receipt or not m_user:
        bot.reply_to(message, "⚠️ Bu rasm ostida foydalanuvchi ID si topilmadi. Chek rasmiga reply qiling.")
        return

    amount = parse_amount(message.text)
    if amount is None:
        bot.reply_to(
            message,
            "⚠️ Summa noto'g'ri. Faqat raqam yozing (masalan: 200000). "
            f"Maksimal summa: {fmt(MAX_AMOUNT)} so'm.",
        )
        return

    receipt_id = int(m_receipt.group(1))
    user_id = int(m_user.group(1))

    receipt = get_receipt(receipt_id)
    if receipt is None or receipt["user_id"] != user_id:
        bot.reply_to(message, "❌ Chek bazadan topilmadi yoki ma'lumotlar mos kelmadi.")
        return

    # Chekni tasdiqlash + balansga qo'shish (atomik)
    new_balance = approve_receipt(receipt_id, amount)
    refresh_receipt_messages(receipt_id)
    if new_balance is None:
        bot.reply_to(message, "⚠️ Bu chek allaqachon ko'rib chiqilgan. Balans qayta qo'shilmadi.")
        return

    # Foydalanuvchiga xabar va fayl
    notified = safe_send(
        user_id,
        f"✅ Chekingiz tasdiqlandi va balansga {fmt(amount)} so'm qo'shildi.\n"
        f"💰 Joriy balans: {fmt(new_balance)} so'm",
    )
    file_sent = send_launcher(user_id) if notified else False

    report = (
        f"✅ Chek #{receipt_id} tasdiqlandi.\n"
        f"👤 ID: {user_id}\n"
        f"➕ Qo'shildi: {fmt(amount)} so'm\n"
        f"💰 Yangi balans: {fmt(new_balance)} so'm\n"
    )
    if notified and file_sent:
        report += "📦 GuestLauncher.exe yuborildi."
    elif notified:
        report += "⚠️ Xabar yuborildi, lekin GuestLauncher.exe ni yuborib bo'lmadi (logni tekshiring)."
    else:
        report += "⚠️ Foydalanuvchiga xabar yuborilmadi (u botni bloklagan bo'lishi mumkin). Balans qo'shildi."
    bot.reply_to(message, report)


# =====================================================================
#                             ADMIN KOMANDALARI
# =====================================================================
def admin_args(message, usage, allow_zero=False):
    """
    Admin komandalarining umumiy tekshiruvi: admin ekanligi, format, foydalanuvchi va summa.
    Muvaffaqiyatli bo'lsa (user_row, amount) qaytaradi, aks holda javob yuborib None qaytaradi.
    """
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Bu komanda faqat adminlar uchun.")
        return None

    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, f"⚠️ Format: {usage}")
        return None

    user = resolve_user(parts[1])
    if user is None:
        bot.reply_to(
            message,
            "❌ Foydalanuvchi bazadan topilmadi. U botga kamida bir marta /start bosgan bo'lishi kerak.",
        )
        return None

    amount = parse_amount(" ".join(parts[2:]), allow_zero=allow_zero)
    if amount is None:
        bot.reply_to(
            message,
            f"⚠️ Summa noto'g'ri. Musbat butun son yozing (maksimal {fmt(MAX_AMOUNT)}).",
        )
        return None
    return user, amount


def display_name(user_row):
    return f"@{user_row['username']}" if user_row["username"] else str(user_row["user_id"])


@bot.message_handler(commands=["people"])
def cmd_people(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Bu komanda faqat adminlar uchun.")
        return

    users = get_all_users()
    if not users:
        bot.reply_to(message, "Hali birorta foydalanuvchi yo'q.")
        return

    lines = [f"👥 Jami foydalanuvchilar: {len(users)}", ""]
    for i, u in enumerate(users, 1):
        uname = f"@{u['username']}" if u["username"] else "—"
        plan = f" | 📦 {u['plan']}" if u["plan"] else ""
        lines.append(f"{i}. ID: {u['user_id']} | {uname} | {fmt(u['balance'])} so'm{plan}")
    send_long(message.chat.id, lines)


@bot.message_handler(commands=["allm"])
def cmd_allm(message):
    """/allm [username] [summa] — balansni to'g'ridan-to'g'ri berilgan summaga o'rnatadi."""
    parsed = admin_args(message, "/allm [username] [summa]", allow_zero=True)
    if parsed is None:
        return
    user, amount = parsed
    new_balance = set_balance(user["user_id"], amount)
    bot.reply_to(
        message, f"✅ {display_name(user)} balansi {fmt(new_balance)} so'm qilib o'rnatildi."
    )
    if NOTIFY_USER_ON_ADMIN_CHANGE:
        safe_send(user["user_id"], f"💰 Balansingiz {fmt(new_balance)} so'm qilib o'rnatildi.")


@bot.message_handler(commands=["add"])
def cmd_add(message):
    """/add [id_yoki_username] [summa] — balansga pul qo'shadi."""
    parsed = admin_args(message, "/add [id_yoki_username] [summa]")
    if parsed is None:
        return
    user, amount = parsed
    new_balance = add_balance(user["user_id"], amount)
    bot.reply_to(
        message,
        f"✅ {display_name(user)} balansiga {fmt(amount)} so'm qo'shildi.\n"
        f"💰 Yangi balans: {fmt(new_balance)} so'm",
    )
    if NOTIFY_USER_ON_ADMIN_CHANGE:
        safe_send(
            user["user_id"],
            f"✅ Balansingizga {fmt(amount)} so'm qo'shildi.\n💰 Joriy balans: {fmt(new_balance)} so'm",
        )


@bot.message_handler(commands=["ayir"])
def cmd_ayir(message):
    """/ayir [id_yoki_username] [summa] — balansdan pul ayiradi."""
    parsed = admin_args(message, "/ayir [id_yoki_username] [summa]")
    if parsed is None:
        return
    user, amount = parsed
    if not subtract_balance(user["user_id"], amount):
        bot.reply_to(
            message,
            f"❌ {display_name(user)} balansida yetarli mablag' yo'q.\n"
            f"Hozirgi balans: {fmt(get_balance(user['user_id']))} so'm",
        )
        return
    new_balance = get_balance(user["user_id"])
    bot.reply_to(
        message,
        f"✅ {display_name(user)} balansidan {fmt(amount)} so'm ayirildi.\n"
        f"💰 Yangi balans: {fmt(new_balance)} so'm",
    )
    if NOTIFY_USER_ON_ADMIN_CHANGE:
        safe_send(
            user["user_id"],
            f"➖ Balansingizdan {fmt(amount)} so'm ayirildi.\n💰 Joriy balans: {fmt(new_balance)} so'm",
        )


# =====================================================================
#                      QOLGAN BARCHA MATNLAR (zaxira handler)
# =====================================================================
@bot.message_handler(content_types=["text"], func=lambda m: m.chat.type == "private")
def fallback_text(message):
    if is_admin(message.from_user.id):
        bot.reply_to(
            message,
            "ℹ️ Chekni tasdiqlash uchun summani chek RASMIGA reply qilib yozing (masalan: 200000).",
        )
    else:
        register_user(message.from_user)
        bot.reply_to(message, "Menyuni ochish uchun /start bosing.")


# =====================================================================
#                                ISHGA TUSHIRISH
# =====================================================================
if __name__ == "__main__":
    init_db()

    me = bot.get_me()
    BOT_ID = me.id
    log.info("Bot ishga tushdi: @%s (id=%s)", me.username, me.id)

    if not os.path.isfile(LAUNCHER_PATH):
        log.warning(
            "DIQQAT: %s topilmadi! Fayl o'rniga yuklash havolasi yuboriladi.", LAUNCHER_PATH
        )

    # Foydalanuvchilar uchun komandalar menyusi
    try:
        bot.set_my_commands(
            [
                types.BotCommand("start", "Asosiy menyu"),
                types.BotCommand("buy", "Obuna sotib olish"),
                types.BotCommand("balance", "Balansni ko'rish"),
                types.BotCommand("help", "Yordam"),
            ]
        )
    except Exception as e:
        log.warning("Komandalar menyusi o'rnatilmadi: %s", e)

    # skip_pending=False: bot o'chiq paytida kelgan cheklar yo'qolmaydi
    bot.infinity_polling(skip_pending=False)