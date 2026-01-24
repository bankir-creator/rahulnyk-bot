import os
import re
import sqlite3
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

# =======================
# CONFIG
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "rahulnyk.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не заданий. Railway -> Variables -> BOT_TOKEN")

# ====== BUTTONS ======
BTN_INCOME  = "➕ Дохід"
BTN_EXPENSE = "➖ Витрата"
BTN_SAVE    = "🏦 Відкладено"
BTN_BALANCE = "💰 Баланс"
BTN_MONTH   = "📊 Місяць"
BTN_BACKUP  = "💾 Бекап"
BTN_HELP    = "ℹ️ Допомога"
BTN_CANCEL  = "❌ Скасувати"
BTN_MENU    = "📌 Меню"
BTN_OTHER   = "📌 Інше (своя категорія)"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_INCOME), KeyboardButton(BTN_EXPENSE)],
        [KeyboardButton(BTN_SAVE), KeyboardButton(BTN_BALANCE)],
        [KeyboardButton(BTN_MONTH), KeyboardButton(BTN_BACKUP)],
        [KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True
)
CANCEL_KB = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_CANCEL)], [KeyboardButton(BTN_MENU)]],
    resize_keyboard=True
)

EXPENSE_CATS = [
    "🍔 Їжа", "🚗 Транспорт", "🏠 Комуналка", "🛒 Покупки",
    "💊 Здоровʼя", "🎮 Розваги", "🎁 Подарунки", "📚 Освіта",
    "🧾 Підписки", "✈️ Подорожі", BTN_OTHER
]

def expense_kb():
    rows, row = [], []
    for c in EXPENSE_CATS:
        row.append(KeyboardButton(c))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(BTN_CANCEL), KeyboardButton(BTN_MENU)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# =======================
# DB
# =======================
def conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA journal_mode=WAL;")
    return c

def init_db():
    with conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tx (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_id INTEGER NOT NULL,
                wallet_type TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_wallet_time ON tx(wallet_id, created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wallet_user ON tx(wallet_id, user_id)")

def wallet(update: Update):
    chat = update.effective_chat
    user = update.effective_user
    if chat and chat.type in ("group", "supergroup"):
        return chat.id, "group"
    return user.id, "private"

def uname(update: Update):
    u = update.effective_user
    return (u.full_name or u.username or f"ID:{u.id}")

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")

def parse_amount_note(text: str):
    """
    "500", "500 кава", "500,50 їжа"
    """
    text = (text or "").strip()
    m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(.*)$", text)
    if not m:
        return None, ""
    amount_raw = m.group(1).replace(",", ".")
    note = (m.group(2) or "").strip()
    try:
        val = float(amount_raw)
    except ValueError:
        return None, note
    if val <= 0:
        return None, note
    return val, note

def add_tx(wallet_id, wallet_type, user_id, user_name, tx_type, amount, category=None, note=None):
    with conn() as c:
        c.execute(
            "INSERT INTO tx(wallet_id,wallet_type,user_id,user_name,tx_type,amount,category,note,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (wallet_id, wallet_type, user_id, user_name, tx_type, float(amount), category, note, now_iso())
        )

def totals(wallet_id):
    with conn() as c:
        inc, exp, sav = c.execute("""
            SELECT
              COALESCE(SUM(CASE WHEN tx_type='income' THEN amount END),0),
              COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount END),0),
              COALESCE(SUM(CASE WHEN tx_type='saved' THEN amount END),0)
            FROM tx WHERE wallet_id=?
        """, (wallet_id,)).fetchone()
    inc, exp, sav = float(inc), float(exp), float(sav)
    return inc, exp, sav, inc-exp-sav

def totals_by_user(wallet_id):
    with conn() as c:
        rows = c.execute("""
            SELECT user_name,
              COALESCE(SUM(CASE WHEN tx_type='income' THEN amount END),0) inc,
              COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount END),0) exp,
              COALESCE(SUM(CASE WHEN tx_type='saved' THEN amount END),0) sav
            FROM tx WHERE wallet_id=?
            GROUP BY user_name
            ORDER BY user_name COLLATE NOCASE
        """, (wallet_id,)).fetchall()
    out = []
    for name, inc, exp, sav in rows:
        inc, exp, sav = float(inc), float(exp), float(sav)
        out.append((name, inc, exp, sav, inc-exp-sav))
    return out

def month_stats(wallet_id, year, month):
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    s, e = start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")
    with conn() as c:
        inc, exp, sav = c.execute("""
            SELECT
              COALESCE(SUM(CASE WHEN tx_type='income' THEN amount END),0),
              COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount END),0),
              COALESCE(SUM(CASE WHEN tx_type='saved' THEN amount END),0)
            FROM tx
            WHERE wallet_id=? AND created_at>=? AND created_at<?
        """, (wallet_id, s, e)).fetchone()

        cats = c.execute("""
            SELECT COALESCE(category,'(без категорії)') cat, SUM(amount) s
            FROM tx
            WHERE wallet_id=? AND created_at>=? AND created_at<? AND tx_type='expense'
            GROUP BY cat
            ORDER BY SUM(amount) DESC
        """, (wallet_id, s, e)).fetchall()

    inc, exp, sav = float(inc), float(exp), float(sav)
    return inc, exp, sav, inc-exp-sav, [(cat, float(v)) for cat, v in cats]

def fmt(x: float) -> str:
    return f"{int(round(x))} грн" if abs(x - round(x)) < 1e-9 else f"{x:.2f} грн"

# =======================
# STATES
# =======================
CHOOSE, INCOME, SAVE, EXP_CAT, EXP_CUSTOM, EXP_AMOUNT = range(6)

# =======================
# HANDLERS
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    await update.message.reply_text(
        "👋 Я *Рахульник*.\n"
        "✅ У групі — *спільний* бюджет.\n"
        "✅ У приваті — *особистий* бюджет.\n\n"
        "Натисни кнопку 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSE

async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Я тут 🙂", reply_markup=MAIN_KB)
    return CHOOSE

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Натискай кнопки.\n"
        "Суму можна писати з приміткою: `350 кава`.\n"
        "У групі — спільно, у приваті — окремо.\n",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSE

async def where_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wid, wtype = wallet(update)
    await update.message.reply_text(f"📍 Режим: {wtype} | wallet_id={wid}", reply_markup=MAIN_KB)

async def choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()

    if t == BTN_MENU:
        await update.message.reply_text("📌 Меню:", reply_markup=MAIN_KB)
        return CHOOSE

    if t == BTN_HELP:
        return await help_cmd(update, context)

    if t == BTN_BALANCE:
        wid, wtype = wallet(update)
        inc, exp, sav, net = totals(wid)
        per = totals_by_user(wid)
        title = "💰 *Баланс (спільний)*" if wtype == "group" else "💰 *Баланс (особистий)*"
        lines = [
            title,
            f"• Дохід: *{fmt(inc)}*",
            f"• Витрати: *{fmt(exp)}*",
            f"• Відкладено: *{fmt(sav)}*",
            f"• Доступно: *{fmt(net)}*",
            "",
            "👤 *По людях:*"
        ]
        if not per:
            lines.append("• Поки немає записів.")
        else:
            for name, i, e, s, n in per:
                lines.append(f"• *{name}*: дохід {fmt(i)}, витрати {fmt(e)}, відкладено {fmt(s)}, доступно *{fmt(n)}*")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)
        return CHOOSE

    if t == BTN_MONTH:
        wid, wtype = wallet(update)
        dt = datetime.now()
        inc, exp, sav, net, cats = month_stats(wid, dt.year, dt.month)
        lines = [
            f"📊 *{dt.strftime('%m.%Y')}* ({'спільний' if wtype=='group' else 'особистий'})",
            f"• Дохід: *{fmt(inc)}*",
            f"• Витрати: *{fmt(exp)}*",
            f"• Відкладено: *{fmt(sav)}*",
            f"• Доступно: *{fmt(net)}*",
            "",
            "📁 *Категорії витрат:*"
        ]
        if not cats:
            lines.append("• Немає витрат.")
        else:
            for cat, v in cats[:12]:
                lines.append(f"• {cat}: *{fmt(v)}*")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)
        return CHOOSE

    if t == BTN_BACKUP:
        if not os.path.exists(DB_PATH):
            await update.message.reply_text("База ще порожня.", reply_markup=MAIN_KB)
            return CHOOSE
        await update.message.reply_text("💾 Надсилаю файл бази…", reply_markup=MAIN_KB)
        await update.message.reply_document(open(DB_PATH, "rb"), filename="rahulnyk.db")
        return CHOOSE

    if t == BTN_INCOME:
        await update.message.reply_text(
            "➕ Введи суму (можна з приміткою): `5000 зарплата`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return INCOME

    if t == BTN_SAVE:
        await update.message.reply_text(
            "🏦 Введи суму (можна з приміткою): `1000 на відпустку`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return SAVE

    if t == BTN_EXPENSE:
        await update.message.reply_text("➖ Обери категорію 👇", reply_markup=expense_kb())
        return EXP_CAT

    await update.message.reply_text("Натисни кнопку з меню 👇", reply_markup=MAIN_KB)
    return CHOOSE

async def income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSE

    amount, note = parse_amount_note(t)
    if amount is None:
        await update.message.reply_text(
            "Не бачу суму. Напиши: `5000 зарплата`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return INCOME

    wid, wtype = wallet(update)
    u = update.effective_user
    add_tx(wid, wtype, u.id, uname(update), "income", amount, None, note or None)

    await update.message.reply_text(f"✅ Збережено дохід: *{fmt(amount)}*", parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)
    return CHOOSE

async def save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSE

    amount, note = parse_amount_note(t)
    if amount is None:
        await update.message.reply_text(
            "Не бачу суму. Напиши: `1000 на відпустку`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return SAVE

    wid, wtype = wallet(update)
    u = update.effective_user
    add_tx(wid, wtype, u.id, uname(update), "saved", amount, None, note or None)

    await update.message.reply_text(f"✅ Збережено відкладене: *{fmt(amount)}*", parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)
    return CHOOSE

async def exp_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSE

    if t == BTN_OTHER:
        await update.message.reply_text("Введи свою категорію (напр. `Кафе`):", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
        return EXP_CUSTOM

    context.user_data["cat"] = t
    await update.message.reply_text(f"Категорія: *{t}*\nВведи суму: `350 кава`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
    return EXP_AMOUNT

async def exp_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSE

    context.user_data["cat"] = t
    await update.message.reply_text(f"Категорія: *{t}*\nВведи суму: `350 кава`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
    return EXP_AMOUNT

async def exp_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSE

    amount, note = parse_amount_note(t)
    if amount is None:
        await update.message.reply_text("Не бачу суму. Напиши: `500 їжа`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
        return EXP_AMOUNT

    cat = context.user_data.get("cat") or "(без категорії)"
    wid, wtype = wallet(update)
    u = update.effective_user
    add_tx(wid, wtype, u.id, uname(update), "expense", amount, cat, note or None)
    context.user_data.pop("cat", None)

    await update.message.reply_text(f"✅ Збережено витрату: *{fmt(amount)}*\n📁 {cat}", parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)
    return CHOOSE

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("where", where_cmd))

    app.add_handler(MessageHandler(filters.Regex(r"(?i)^(привіт|привет|хай|hello|hi)\b"), wake))

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.ALL, choose)],
        states={
            CHOOSE: [MessageHandler(filters.ALL, choose)],
            INCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, income)],
            SAVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save)],
            EXP_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_cat)],
            EXP_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_custom)],
            EXP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, exp_amount)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )
    app.add_handler(conv)

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
