import os
import sqlite3
from datetime import datetime
from typing import Optional

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "rahulnyk.db")
CURRENCY = "грн"

if not BOT_TOKEN:
    raise RuntimeError("❌ Не задано BOT_TOKEN")

# ================= КАТЕГОРІЇ =================
INCOME_CATS = ["💼 Зарплата", "🧑‍💻 Підробіток", "🎁 Подарунок", "➕ Інше"]
EXPENSE_CATS = ["🍔 Їжа", "🚗 Транспорт", "🏠 Комуналка", "🧹 Побут", "🎮 Розваги", "❤️ Здоров'я", "👕 Одяг", "➖ Інше"]
SAVE_CATS = ["🛟 Подушка", "✈️ Відпустка", "🎯 Ціль", "🏦 Інше"]

# ================= КНОПКИ =================
BTN_INCOME = "➕ Додати дохід"
BTN_EXPENSE = "➖ Додати витрату"
BTN_SAVE = "🏦 Відкласти гроші"
BTN_BALANCE = "📊 Баланс"
BTN_HISTORY = "📜 Історія"
BTN_CANCEL = "❌ Скасувати"
BTN_SKIP = "↩️ Без примітки"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [BTN_INCOME, BTN_EXPENSE],
        [BTN_SAVE, BTN_BALANCE],
        [BTN_HISTORY],
    ],
    resize_keyboard=True,
)

# ================= СТАНИ =================
ASK_AMOUNT, ASK_CATEGORY, ASK_NOTE = range(3)

# ================= БАЗА ДАНИХ =================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_name TEXT,
            type TEXT,
            amount REAL,
            category TEXT,
            note TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn

def add_record(chat_id, user_name, rtype, amount, category, note):
    conn = db()
    conn.execute(
        "INSERT INTO records VALUES (NULL,?,?,?,?,?,?,?)",
        (
            chat_id,
            user_name,
            rtype,
            amount,
            category,
            note,
            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        ),
    )
    conn.commit()
    conn.close()

def get_totals(chat_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM records WHERE chat_id=? AND type='income'", (chat_id,))
    income = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM records WHERE chat_id=? AND type='expense'", (chat_id,))
    expense = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM records WHERE chat_id=? AND type='save'", (chat_id,))
    save = cur.fetchone()[0]
    conn.close()
    return income, expense, save

def get_history(chat_id):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT type, amount, category, user_name, created_at FROM records WHERE chat_id=? ORDER BY id DESC LIMIT 10",
        (chat_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows

# ================= ДОПОМІЖНІ =================
def username(update: Update):
    u = update.effective_user
    return u.first_name or u.username or "Невідомо"

def parse_amount(text: str) -> Optional[float]:
    try:
        value = float(text.replace(",", "."))
        return value if value > 0 else None
    except:
        return None

# ================= ХЕНДЛЕРИ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привіт! Я *Рахульник* — ваш сімейний фінансовий бот.\n\n"
        "Натискай кнопки нижче 👇",
        reply_markup=MAIN_KB,
        parse_mode="Markdown",
    )

async def choose_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == BTN_INCOME:
        context.user_data["type"] = "income"
        await update.message.reply_text("💰 Введи суму доходу:", reply_markup=ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True))
        return ASK_AMOUNT
    if text == BTN_EXPENSE:
        context.user_data["type"] = "expense"
        await update.message.reply_text("💸 Введи суму витрати:", reply_markup=ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True))
        return ASK_AMOUNT
    if text == BTN_SAVE:
        context.user_data["type"] = "save"
        await update.message.reply_text("🏦 Введи суму відкладених:", reply_markup=ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True))
        return ASK_AMOUNT
    if text == BTN_BALANCE:
        inc, exp, sav = get_totals(update.effective_chat.id)
        await update.message.reply_text(
            f"📊 Баланс:\n"
            f"💰 Доходи: {inc:.2f} {CURRENCY}\n"
            f"💸 Витрати: {exp:.2f} {CURRENCY}\n"
            f"🏦 Відкладено: {sav:.2f} {CURRENCY}\n\n"
            f"✅ Доступно: {inc-exp-sav:.2f} {CURRENCY}",
            reply_markup=MAIN_KB,
        )
    if text == BTN_HISTORY:
        rows = get_history(update.effective_chat.id)
        if not rows:
            await update.message.reply_text("Історія поки порожня.", reply_markup=MAIN_KB)
        else:
            msg = "📜 Останні записи:\n"
            for r in rows:
                msg += f"{r[4]} | {r[3]} | {r[2]} | {r[1]} {CURRENCY}\n"
            await update.message.reply_text(msg, reply_markup=MAIN_KB)
    return ConversationHandler.END

async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == BTN_CANCEL:
        await update.message.reply_text("Скасовано.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    amount = parse_amount(update.message.text)
    if not amount:
        await update.message.reply_text("❗ Введи коректне число.")
        return ASK_AMOUNT

    context.user_data["amount"] = amount
    t = context.user_data["type"]

    cats = INCOME_CATS if t == "income" else EXPENSE_CATS if t == "expense" else SAVE_CATS
    kb = ReplyKeyboardMarkup([[c] for c in cats] + [[BTN_CANCEL]], resize_keyboard=True)

    await update.message.reply_text("Обери категорію:", reply_markup=kb)
    return ASK_CATEGORY

async def ask_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == BTN_CANCEL:
        await update.message.reply_text("Скасовано.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    context.user_data["category"] = update.message.text
    await update.message.reply_text(
        "Додай примітку або натисни «Без примітки»",
        reply_markup=ReplyKeyboardMarkup([[BTN_SKIP], [BTN_CANCEL]], resize_keyboard=True),
    )
    return ASK_NOTE

async def ask_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == BTN_CANCEL:
        await update.message.reply_text("Скасовано.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    note = "" if update.message.text == BTN_SKIP else update.message.text

    add_record(
        update.effective_chat.id,
        username(update),
        context.user_data["type"],
        context.user_data["amount"],
        context.user_data["category"],
        note,
    )

    await update.message.reply_text("✅ Запис додано!", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, choose_action)],
        states={
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_amount)],
            ASK_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_category)],
            ASK_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_note)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    app.run_polling()

if __name__ == "__main__":
    main()
