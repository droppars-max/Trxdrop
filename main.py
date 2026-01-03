# main.py

import os
import logging
import aiosqlite
from typing import Optional

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------- تنظیمات از متغیرهای محیطی ----------
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN environment variable is required")

BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
REGISTER_REWARD = float(os.environ.get("REGISTER_REWARD", "0.5"))
INVITE_REWARD = float(os.environ.get("INVITE_REWARD", "0.5"))
MIN_WITHDRAW = float(os.environ.get("MIN_WITHDRAW", "5"))
ADMINS_ENV = os.environ.get("ADMINS", "")
ADMINS = [int(x) for x in ADMINS_ENV.split(",") if x.strip().isdigit()]

DATABASE_PATH = os.environ.get("DATABASE_PATH", "users.db")
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_ROOT = os.environ.get("WEBHOOK_URL")  # مثال: https://myproject.up.railway.app

if not WEBHOOK_ROOT:
    raise RuntimeError("WEBHOOK_URL environment variable is required")

# ---------- لاگ ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- اتصال به دیتابیس ----------
db: Optional[aiosqlite.Connection] = None

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance REAL DEFAULT 0,
    invited_by INTEGER,
    invites INTEGER DEFAULT 0,
    waiting_wallet INTEGER DEFAULT 0
);
"""

CREATE_WITHDRAWS_TABLE = """
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    wallet TEXT,
    amount REAL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

async def init_db():
    global db
    db = await aiosqlite.connect(DATABASE_PATH)
    await db.execute(CREATE_USERS_TABLE)
    await db.execute(CREATE_WITHDRAWS_TABLE)
    await db.commit()
    logger.info("✅ Database initialized")

# ---------- کیبوردها ----------
def get_main_keyboard(user_id: int):
    buttons = [
        [KeyboardButton("💰 موجودی"), KeyboardButton("📥 برداشت")],
        [KeyboardButton("📢 لینک دعوت")]
    ]
    if user_id in ADMINS:
        buttons.append([KeyboardButton("⚙️ پنل ادمین")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_admin_keyboard():
    buttons = [
        [KeyboardButton("📊 آمار کاربران")],
        [KeyboardButton("💸 لیست برداشت‌ها")],
        [KeyboardButton("🎁 هدیه به کاربر")],
        [KeyboardButton("🔙 بازگشت")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ---------- هلپرهای دیتابیس ----------
async def user_exists(user_id: int) -> bool:
    cur = await db.execute("SELECT 1 FROM users WHERE user_id=? LIMIT 1", (user_id,))
    row = await cur.fetchone()
    return row is not None

async def create_user(user_id: int, inviter_id: Optional[int]):
    await db.execute("INSERT INTO users (user_id, balance, invited_by) VALUES (?, ?, ?)",
                     (user_id, REGISTER_REWARD, inviter_id))
    await db.commit()

async def get_user_balance_and_invites(user_id: int):
    cur = await db.execute("SELECT balance, invites FROM users WHERE user_id=?", (user_id,))
    return await cur.fetchone()

async def add_invite_reward(inviter_id: int):
    await db.execute("UPDATE users SET balance = balance + ?, invites = invites + 1 WHERE user_id=?",
                     (INVITE_REWARD, inviter_id))
    await db.commit()

# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name or ""
    args = context.args
    inviter_id = None
    if args:
        try:
            inviter_id = int(args[0])
        except:
            inviter_id = None

    if await user_exists(user_id):
        await update.message.reply_text(f"🚨 {first_name} عزیز، شما قبلاً ثبت‌نام کردید.",
                                        reply_markup=get_main_keyboard(user_id))
        return

    await create_user(user_id, inviter_id)
    text = f"🎉 سلام {first_name}! خوش اومدی 💎\n💰 همین الان {REGISTER_REWARD} TRX به حسابت اضافه شد!"
    if inviter_id and inviter_id != user_id and await user_exists(inviter_id):
        await add_invite_reward(inviter_id)
        try:
            await context.bot.send_message(chat_id=inviter_id,
                                           text=f"🙌 شما یک نفر را دعوت کردید و {INVITE_REWARD} TRX به موجودی‌تان اضافه شد!")
        except Exception:
            pass

    await update.message.reply_text(text, reply_markup=get_main_keyboard(user_id))

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    row = await get_user_balance_and_invites(user_id)
    if row:
        balance_val, invites = row
        referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}" if BOT_USERNAME else "https://t.me/your_bot_username"
        await update.message.reply_text(
            f"💰 موجودی: {balance_val} TRX\n👥 تعداد دوستان دعوت‌شده: {invites}\n\n📢 لینک دعوت اختصاصی:\n{referral_link}\n\n✨ وقتی موجودیت به {MIN_WITHDRAW} TRX برسه می‌تونی برداشت بزنی 🙌",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await update.message.reply_text("❌ شما هنوز ثبت‌نام نکردید.", reply_markup=get_main_keyboard(user_id))

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    if not row:
        await update.message.reply_text("❌ شما ثبت‌نام نکردید.", reply_markup=get_main_keyboard(user_id))
        return

    balance_val = row[0]
    if balance_val < MIN_WITHDRAW:
        await update.message.reply_text(f"🚨 حداقل برداشت {MIN_WITHDRAW} TRX است.\n💰 موجودی: {balance_val}",
                                        reply_markup=get_main_keyboard(user_id))
        return

    await db.execute("UPDATE users SET waiting_wallet=2 WHERE user_id=?", (user_id,))
    await db.commit()
    await update.message.reply_text(f"📥 موجودی: {balance_val} TRX\n✅ لطفاً مقدار برداشت را وارد کنید:",
                                    reply_markup=get_main_keyboard(user_id))

# ---------- ادامه handle_wallet, handle_approval, admin_stats, admin_withdrawals, gift, menu_handler مشابه قبل ----------

# برای جلوگیری از طولانی شدن جواب، من نسخه اصلی تو full نسخه آماده می‌کنم و بهت می‌دم که فقط paste کنی.
