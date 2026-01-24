Bankir, [2026-01-24 19:05]
import os
import re
import sqlite3
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# =======================
# CONFIG
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "rahulnyk.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не заданий. Додай його в Railway -> Variables.")

BTN_INCOME  = "➕ Дохід"
BTN_EXPENSE = "➖ Витрата"
BTN_SAVE    = "🏦 Відкладено"
BTN_BALANCE = "💰 Баланс"
BTN_MONTH   = "📊 Статистика (місяць)"
BTN_BACKUP  = "💾 Бекап бази"
BTN_HELP    = "ℹ️ Допомога"
BTN_MENU    = "📌 Меню"
BTN_CANCEL  = "❌ Скасувати"
BTN_OTHER_CAT = "📌 Інше (ввести свою)"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_INCOME), KeyboardButton(BTN_EXPENSE)],
        [KeyboardButton(BTN_SAVE), KeyboardButton(BTN_BALANCE)],
        [KeyboardButton(BTN_MONTH), KeyboardButton(BTN_BACKUP)],
        [KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True
)
CANCEL_KB = ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)], [KeyboardButton(BTN_MENU)]], resize_keyboard=True)

EXPENSE_CATS = [
    "🍔 Їжа",
    "🚗 Транспорт",
    "🏠 Дім/комуналка",
    "🛒 Покупки",
    "💊 Здоровʼя",
    "🎮 Розваги",
    "🎁 Подарунки",
    "📚 Освіта",
    "🧾 Підписки",
    "🐾 Тварини",
    "✈️ Подорожі",
    BTN_OTHER_CAT,
]

def expense_cats_kb():
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
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tx (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_id INTEGER NOT NULL,         -- спільний (group chat_id) або особистий (user_id в private)
                wallet_type TEXT NOT NULL,          -- 'group' або 'private'
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                tx_type TEXT NOT NULL CHECK(tx_type IN ('income','expense','saved')),
                amount REAL NOT NULL CHECK(amount > 0),
                category TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_wallet_time ON tx(wallet_id, created_at);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_wallet_user ON tx(wallet_id, user_id);")

def wallet_key(update: Update):
    """
    ДВА РЕЖИМИ:
    - у групі: wallet_id = chat_id (спільний сімейний гаманець)
    - у приваті: wallet_id = user_id (особистий гаманець кожного)
    """
    chat = update.effective_chat
    user = update.effective_user
    if chat and chat.type in ("group", "supergroup"):
        return chat.id, "group"
    return user.id, "private"

def user_display(update: Update) -> str:
    u = update.effective_user
    name = (u.full_name or "").strip()
    return name if name else (u.username or f"ID:{u.id}")

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")

def parse_amount_note(text: str):
    """
    Приймає: "500", "500 кава", "500,50 їжа"
    """
    text = (text or "").strip()
    m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(.*)$", text)
    if not m:
        return None, ""
    amount_raw = m.group(1).replace(",", ".")
    note = (m.group(2) or "").strip()
    try:
        amount = float(amount_raw)
        if amount <= 0:
            return None, note
        return amount, note
    except ValueError:

Bankir, [2026-01-24 19:05]
return None, note

def add_tx(wallet_id: int, wallet_type: str, user_id: int, user_name: str,
           tx_type: str, amount: float, category: str | None, note: str | None):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO tx (wallet_id,wallet_type,user_id,user_name,tx_type,amount,category,note,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (wallet_id, wallet_type, user_id, user_name, tx_type, amount, category, note, now_iso())
        )

def totals(wallet_id: int):
    with db_conn() as conn:
        r = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN tx_type='income' THEN amount END), 0),
              COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount END), 0),
              COALESCE(SUM(CASE WHEN tx_type='saved' THEN amount END), 0)
            FROM tx
            WHERE wallet_id=?
            """,
            (wallet_id,)
        ).fetchone()
    inc, exp, sav = float(r[0]), float(r[1]), float(r[2])
    return inc, exp, sav, inc - exp - sav

def totals_by_user(wallet_id: int):
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT user_id, user_name,
              COALESCE(SUM(CASE WHEN tx_type='income' THEN amount END), 0) AS inc,
              COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount END), 0) AS exp,
              COALESCE(SUM(CASE WHEN tx_type='saved' THEN amount END), 0) AS sav
            FROM tx
            WHERE wallet_id=?
            GROUP BY user_id, user_name
            ORDER BY user_name COLLATE NOCASE
            """,
            (wallet_id,)
        ).fetchall()
    out = []
    for uid, name, inc, exp, sav in rows:
        inc, exp, sav = float(inc), float(exp), float(sav)
        out.append((uid, name, inc, exp, sav, inc-exp-sav))
    return out

def month_stats(wallet_id: int, year: int, month: int):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year+1, 1, 1)
    else:
        end = datetime(year, month+1, 1)
    start_iso = start.isoformat(timespec="seconds")
    end_iso = end.isoformat(timespec="seconds")

    with db_conn() as conn:
        t = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN tx_type='income' THEN amount END), 0),
              COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount END), 0),
              COALESCE(SUM(CASE WHEN tx_type='saved' THEN amount END), 0)
            FROM tx
            WHERE wallet_id=? AND created_at>=? AND created_at<?
            """,
            (wallet_id, start_iso, end_iso)
        ).fetchone()
        cats = conn.execute(
            """
            SELECT COALESCE(category,'(без категорії)') as cat, SUM(amount)
            FROM tx
            WHERE wallet_id=? AND created_at>=? AND created_at<? AND tx_type='expense'
            GROUP BY cat
            ORDER BY SUM(amount) DESC
            """,
            (wallet_id, start_iso, end_iso)
        ).fetchall()
    inc, exp, sav = float(t[0]), float(t[1]), float(t[2])
    net = inc - exp - sav
    cats = [(c, float(s)) for c, s in cats]
    return inc, exp, sav, net, cats

def fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x))} грн"
    return f"{x:.2f} грн"

# =======================
# Conversation states
# =======================
(
    CHOOSING,
    INCOME_AMOUNT,
    SAVE_AMOUNT,
    EXPENSE_CATEGORY,
    EXPENSE_CUSTOM_CATEGORY,
    EXPENSE_AMOUNT,
) = range(6)

# =======================
# Handlers
# =======================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    await update.message.reply_text(
        "👋 Привіт! Я *Рахульник*.\n\n"
        "✅ У *групі* ведемо *спільний* бюджет.\n"
        "✅ У *приваті* — *особистий* бюджет кожного.\n\n"
        "Натисни кнопку 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSING

async def wake_greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.

Bankir, [2026-01-24 19:05]
reply_text("👋 Я на звʼязку 🙂", reply_markup=MAIN_KB)
    return CHOOSING

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Як користуватись*\n"
        "• Натискай кнопки.\n"
        "• Суму можна з приміткою: 500 кава.\n"
        "• Витрати — з категоріями (можна ввести свою).\n"
        "• Баланс показує загальний і по кожному.\n"
        "• Бекап надсилає файл бази.\n\n"
        "Напиши *привіт* — я прокинуся 🙂",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSING

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet_id, wtype = wallet_key(update)
    inc, exp, sav, net = totals(wallet_id)
    per = totals_by_user(wallet_id)

    title = "💰 *Баланс (спільний чат)*" if wtype == "group" else "💰 *Баланс (особистий)*"
    lines = [
        title,
        f"• Дохід: *{fmt(inc)}*",
        f"• Витрати: *{fmt(exp)}*",
        f"• Відкладено: *{fmt(sav)}*",
        f"• Доступно: *{fmt(net)}*",
        "",
        "👤 *По людях:*" if wtype == "group" else "👤 *Деталізація:*"
    ]

    if not per:
        lines.append("• Поки немає записів.")
    else:
        for _, name, i, e, s, n in per:
            lines.append(f"• *{name}*: дохід {fmt(i)}, витрати {fmt(e)}, відкладено {fmt(s)}, доступно *{fmt(n)}*")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)

async def show_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet_id, wtype = wallet_key(update)
    dt = datetime.now()
    inc, exp, sav, net, cats = month_stats(wallet_id, dt.year, dt.month)
    title = f"📊 *Статистика за {dt.strftime('%m.%Y')}*"
    scope = " (спільний чат)" if wtype == "group" else " (особисто)"
    lines = [
        title + scope,
        f"• Дохід: *{fmt(inc)}*",
        f"• Витрати: *{fmt(exp)}*",
        f"• Відкладено: *{fmt(sav)}*",
        f"• Доступно: *{fmt(net)}*",
        "",
        "📁 *Категорії витрат:*"
    ]
    if not cats:
        lines.append("• Немає витрат за цей місяць.")
    else:
        for cat, s in cats[:12]:
            lines.append(f"• {cat}: *{fmt(s)}*")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)

async def send_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    if not os.path.exists(DB_PATH):
        await update.message.reply_text("База ще не створена. Додай хоча б один запис 🙂", reply_markup=MAIN_KB)
        return
    await update.message.reply_text("💾 Надсилаю резервну копію…", reply_markup=MAIN_KB)
    await update.message.reply_document(document=open(DB_PATH, "rb"), filename="rahulnyk.db")

async def choosing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text in (BTN_MENU,):
        await update.message.reply_text("📌 Меню:", reply_markup=MAIN_KB)
        return CHOOSING

    if text == BTN_HELP:
        return await cmd_help(update, context)

    if text == BTN_BALANCE:
        await show_balance(update, context)
        return CHOOSING

    if text == BTN_MONTH:
        await show_month(update, context)
        return CHOOSING

    if text == BTN_BACKUP:
        await send_backup(update, context)
        return CHOOSING

    if text == BTN_INCOME:
        await update.message.reply_text(
            "➕ *Дохід*\nВведи суму (можна з приміткою): `5000 зарплата`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return INCOME_AMOUNT

    if text == BTN_SAVE:
        await update.message.reply_text(
            "🏦 *Відкладено*\nВведи суму (можна з приміткою): `1000 на відпустку`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return SAVE_AMOUNT

    if text == BTN_EXPENSE:
        await update.message.reply_text(
            "➖ *Витрата*\nОбери категорію 👇",
            parse_mode=ParseMode.MARKDOWN,

Bankir, [2026-01-24 19:05]
reply_markup=expense_cats_kb()
        )
        return EXPENSE_CATEGORY

    # будь-який інший текст: покажемо меню (бот “прокинувся”)
    await update.message.reply_text("Натисни кнопку з меню 👇", reply_markup=MAIN_KB)
    return CHOOSING

async def income_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSING

    amount, note = parse_amount_note(text)
    if amount is None:
        await update.message.reply_text("Не бачу суму 😅 Напиши: `5000 зарплата`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
        return INCOME_AMOUNT

    wallet_id, wtype = wallet_key(update)
    user = update.effective_user
    add_tx(wallet_id, wtype, user.id, user_display(update), "income", amount, None, note or None)

    await update.message.reply_text(
        f"✅ Дохід додано: *{fmt(amount)}*"
        + (f"\n📝 {note}" if note else ""),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSING

async def save_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSING

    amount, note = parse_amount_note(text)
    if amount is None:
        await update.message.reply_text("Не бачу суму 😅 Напиши: `1000 на відпустку`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
        return SAVE_AMOUNT

    wallet_id, wtype = wallet_key(update)
    user = update.effective_user
    add_tx(wallet_id, wtype, user.id, user_display(update), "saved", amount, None, note or None)

    await update.message.reply_text(
        f"✅ Відкладено: *{fmt(amount)}*"
        + (f"\n📝 {note}" if note else ""),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSING

async def expense_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSING

    if text == BTN_OTHER_CAT:
        await update.message.reply_text(
            "Введи свою категорію (наприклад: Кафе, `Ремонт`):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return EXPENSE_CUSTOM_CATEGORY

    context.user_data["expense_cat"] = text
    await update.message.reply_text(
        f"➖ Категорія: *{text}*\nТепер введи суму (можна з приміткою): `350 кава`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=CANCEL_KB
    )
    return EXPENSE_AMOUNT

async def expense_custom_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSING

    context.user_data["expense_cat"] = text
    await update.message.reply_text(
        f"➖ Категорія: *{text}*\nТепер введи суму (можна з приміткою): `350 кава`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=CANCEL_KB
    )
    return EXPENSE_AMOUNT

async def expense_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        await update.message.reply_text("Ок.", reply_markup=MAIN_KB)
        return CHOOSING

    amount, note = parse_amount_note(text)
    if amount is None:
        await update.message.reply_text("Не бачу суму 😅 Напиши: `500 їжа`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
        return EXPENSE_AMOUNT

    cat = context.user_data.get("expense_cat") or "(без категорії)"
    wallet_id, wtype = wallet_key(update)
    user = update.effective_user
    add_tx(wallet_id, wtype, user.id, user_display(update), "expense", amount, cat, note or None)
    context.

Bankir, [2026-01-24 19:05]
user_data.pop("expense_cat", None)

    await update.message.reply_text(
        f"✅ Витрата додана: *{fmt(amount)}*\n📁 {cat}"
        + (f"\n📝 {note}" if note else ""),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSING

# =======================
# Build app
# =======================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # “прокидається” на привітання
    app.add_handler(
        MessageHandler(
            filters.Regex(r"(?i)^(привіт|привет|хай|hello|hi)\b"),
            wake_greet
        )
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.ALL, choosing),  # меню керується кнопками/текстом
        ],
        states={
            CHOOSING: [MessageHandler(filters.ALL, choosing)],
            INCOME_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, income_amount)],
            SAVE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_amount)],
            EXPENSE_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, expense_category)],
            EXPENSE_CUSTOM_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, expense_custom_category)],
            EXPENSE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, expense_amount)],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(rf"^{re.escape(BTN_CANCEL)}$"), choosing),
        ],
        allow_reentry=True
    )

    app.add_handler(conv)

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if name == "__main__":
    main()
