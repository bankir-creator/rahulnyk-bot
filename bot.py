import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================
DB_PATH = Path("rahulnyk.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не заданий. Додай його в Railway -> Variables як BOT_TOKEN=...")

# Категорії витрат (можеш змінити список як хочеш)
EXPENSE_CATEGORIES = [
    "🍔 Їжа",
    "🚕 Транспорт",
    "🏠 Дім/комуналка",
    "🛒 Покупки",
    "💊 Здоровʼя",
    "🎁 Подарунки",
    "🎮 Розваги",
    "📚 Освіта",
    "🧾 Підписки",
    "🐾 Тварини",
    "✈️ Подорожі",
    "📌 Інше (ввести свою)",
]

# Кнопки меню
BTN_INCOME = "➕ Додати дохід"
BTN_EXPENSE = "➖ Додати витрату"
BTN_SAVE = "🏦 Відкласти гроші"
BTN_BALANCE = "💰 Баланс"
BTN_MONTH = "📊 Статистика за місяць"
BTN_BACKUP = "💾 Резервна копія"
BTN_HELP = "ℹ️ Допомога"
BTN_CANCEL = "❌ Скасувати"
BTN_MENU = "📌 Меню"

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

# =========================
# DB
# =========================
def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db() -> None:
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tx (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL,
                tg_user_name TEXT NOT NULL,
                tx_type TEXT NOT NULL CHECK(tx_type IN ('income','expense','saved')),
                amount REAL NOT NULL CHECK(amount > 0),
                category TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_created_at ON tx(created_at);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_user ON tx(tg_user_id);"
        )

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def month_range(dt: datetime) -> Tuple[str, str]:
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))

def insert_tx(user_id: int, user_name: str, tx_type: str, amount: float,
              category: Optional[str] = None, note: Optional[str] = None) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO tx (tg_user_id, tg_user_name, tx_type, amount, category, note, created_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, user_name, tx_type, amount, category, note, now_iso())
        )

def get_balances() -> Dict:
    """
    Повертає:
    totals: {'income':..., 'expense':..., 'saved':..., 'net':...}
    by_user: {user_id: {'name':..., 'income':..., 'expense':..., 'saved':..., 'net':...}}
    """
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT tg_user_id, tg_user_name, tx_type, SUM(amount) FROM tx GROUP BY tg_user_id, tg_user_name, tx_type"
        ).fetchall()

    by_user: Dict[int, Dict] = {}
    for uid, uname, ttype, s in rows:
        if uid not in by_user:
            by_user[uid] = {"name": uname, "income": 0.0, "expense": 0.0, "saved": 0.0, "net": 0.0}
        by_user[uid][ttype] = float(s or 0.0)

    # net = income - expense - saved (відкладене вважаємо “виведеним з доступного”)
    for uid in by_user:
        by_user[uid]["net"] = by_user[uid]["income"] - by_user[uid]["expense"] - by_user[uid]["saved"]

    totals = {"income": 0.0, "expense": 0.0, "saved": 0.0, "net": 0.0}
    for uid in by_user:
        totals["income"] += by_user[uid]["income"]
        totals["expense"] += by_user[uid]["expense"]
        totals["saved"] += by_user[uid]["saved"]
    totals["net"] = totals["income"] - totals["expense"] - totals["saved"]

    return {"totals": totals, "by_user": by_user}

def month_stats(dt: datetime) -> Dict:
    start, end = month_range(dt)
    with db_conn() as conn:
        # по типах загалом
        total_rows = conn.execute(
            """
            SELECT tx_type, SUM(amount)
            FROM tx
            WHERE created_at >= ? AND created_at < ?
            GROUP BY tx_type
            """,
            (start, end)
        ).fetchall()

        # по користувачах і типах
        user_rows = conn.execute(
            """
            SELECT tg_user_id, tg_user_name, tx_type, SUM(amount)
            FROM tx
            WHERE created_at >= ? AND created_at < ?
            GROUP BY tg_user_id, tg_user_name, tx_type
            """,
            (start, end)
        ).fetchall()

        # категорії витрат
        cat_rows = conn.execute(
            """
            SELECT COALESCE(category,'(без категорії)') as cat, SUM(amount)
            FROM tx
            WHERE created_at >= ? AND created_at < ? AND tx_type='expense'
            GROUP BY cat
            ORDER BY SUM(amount) DESC
            """,
            (start, end)
        ).fetchall()

    totals = {"income": 0.0, "expense": 0.0, "saved": 0.0, "net": 0.0}
    for ttype, s in total_rows:
        totals[ttype] = float(s or 0.0)
    totals["net"] = totals["income"] - totals["expense"] - totals["saved"]

    by_user: Dict[int, Dict] = {}
    for uid, uname, ttype, s in user_rows:
        if uid not in by_user:
            by_user[uid] = {"name": uname, "income": 0.0, "expense": 0.0, "saved": 0.0, "net": 0.0}
        by_user[uid][ttype] = float(s or 0.0)
    for uid in by_user:
        by_user[uid]["net"] = by_user[uid]["income"] - by_user[uid]["expense"] - by_user[uid]["saved"]

    cats = [(c, float(s or 0.0)) for c, s in cat_rows]

    return {"start": start, "end": end, "totals": totals, "by_user": by_user, "cats": cats}

# =========================
# HELPERS
# =========================
def format_money(x: float) -> str:
    # без копійок якщо .00
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x))} грн"
    return f"{x:.2f} грн"

def get_user_display(update: Update) -> str:
    u = update.effective_user
    name = (u.full_name or "").strip()
    if not name:
        name = (u.username or "Користувач")
    return name

def parse_amount_and_note(text: str) -> Tuple[Optional[float], str]:
    """
    Очікуємо: "500 їжа" або "500" або "500 зарплата"
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
        return None, note

# =========================
# CONVERSATION STATES
# =========================
(
    CHOOSING,
    INCOME_INPUT,
    SAVE_INPUT,
    EXPENSE_CAT,
    EXPENSE_CUSTOM_CAT,
    EXPENSE_INPUT,
) = range(6)

# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    await update.message.reply_text(
        "👋 Привіт! Я *Рахульник*.\n"
        "Я допоможу рахувати доходи, витрати та відкладені гроші.\n\n"
        "Натисни кнопку з меню 👇",
        reply_markup=MAIN_KB,
        parse_mode=ParseMode.MARKDOWN
    )
    return CHOOSING

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📌 Меню:", reply_markup=MAIN_KB)
    return CHOOSING

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Як користуватись:*\n"
        "• Натискай кнопки в меню.\n"
        "• Коли бот просить суму — пиши, наприклад: `500` або `500 зарплата`.\n"
        "• Витрати мають категорії (можна ввести свою).\n"
        "• Баланс рахується окремо для кожного та загалом.\n"
        "• Резервна копія надішле файл бази.\n\n"
        "Спробуй написати *привіт* — я теж прокинуся 🙂",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSING

async def greet_wakeup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привіт! Я на звʼязку 🙂\nНатисни кнопку з меню 👇",
        reply_markup=MAIN_KB
    )
    return CHOOSING

async def choose_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == BTN_INCOME:
        await update.message.reply_text(
            "➕ *Дохід*\nВведи суму і (за бажанням) примітку.\nНаприклад: `5000 зарплата`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return INCOME_INPUT

    if text == BTN_SAVE:
        await update.message.reply_text(
            "🏦 *Відкладені гроші*\nВведи суму і (за бажанням) примітку.\nНаприклад: `1000 на відпустку`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return SAVE_INPUT

    if text == BTN_EXPENSE:
        # вибір категорії
        rows = []
        row = []
        for i, cat in enumerate(EXPENSE_CATEGORIES, start=1):
            row.append(KeyboardButton(cat))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([KeyboardButton(BTN_CANCEL), KeyboardButton(BTN_MENU)])
        kb = ReplyKeyboardMarkup(rows, resize_keyboard=True)

        await update.message.reply_text(
            "➖ *Витрата*\nОбери категорію 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
        return EXPENSE_CAT

    if text == BTN_BALANCE:
        data = get_balances()
        totals = data["totals"]
        by_user = data["by_user"]

        lines = []
        lines.append("💰 *Баланс (загальний)*")
        lines.append(f"• Дохід: *{format_money(totals['income'])}*")
        lines.append(f"• Витрати: *{format_money(totals['expense'])}*")
        lines.append(f"• Відкладено: *{format_money(totals['saved'])}*")
        lines.append(f"• Доступно: *{format_money(totals['net'])}*")
        lines.append("")
        lines.append("👤 *По людям*")
        if not by_user:
            lines.append("• Поки що немає записів.")
        else:
            for uid, info in by_user.items():
                lines.append(
                    f"• *{info['name']}*: дохід {format_money(info['income'])}, "
                    f"витрати {format_money(info['expense'])}, "
                    f"відкладено {format_money(info['saved'])}, "
                    f"доступно *{format_money(info['net'])}*"
                )

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)
        return CHOOSING

    if text == BTN_MONTH:
        dt = datetime.now()
        st = month_stats(dt)
        totals = st["totals"]
        by_user = st["by_user"]
        cats = st["cats"]
        month_title = dt.strftime("%m.%Y")

        lines = []
        lines.append(f"📊 *Статистика за місяць {month_title}*")
        lines.append(f"• Дохід: *{format_money(totals['income'])}*")
        lines.append(f"• Витрати: *{format_money(totals['expense'])}*")
        lines.append(f"• Відкладено: *{format_money(totals['saved'])}*")
        lines.append(f"• Доступно: *{format_money(totals['net'])}*")
        lines.append("")
        lines.append("👤 *По людям*")
        if not by_user:
            lines.append("• Немає записів за цей місяць.")
        else:
            for uid, info in by_user.items():
                lines.append(
                    f"• *{info['name']}*: дохід {format_money(info['income'])}, "
                    f"витрати {format_money(info['expense'])}, "
                    f"відкладено {format_money(info['saved'])}, "
                    f"доступно *{format_money(info['net'])}*"
                )

        lines.append("")
        lines.append("📁 *Категорії витрат*")
        if not cats:
            lines.append("• Немає витрат за цей місяць.")
        else:
            # топ-10
            for cat, s in cats[:10]:
                lines.append(f"• {cat}: *{format_money(s)}*")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KB)
        return CHOOSING

    if text == BTN_BACKUP:
        # Надсилаємо файл бази як документ
        init_db()
        if not DB_PATH.exists():
            await update.message.reply_text("База ще не створена. Додай спочатку хоч один запис 🙂", reply_markup=MAIN_KB)
            return CHOOSING

        await update.message.reply_text("💾 Готую резервну копію…", reply_markup=MAIN_KB)
        await update.message.reply_document(document=DB_PATH.open("rb"), filename=DB_PATH.name, caption="Ось резервна копія бази rahulnyk.db")
        return CHOOSING

    if text == BTN_HELP:
        return await help_cmd(update, context)

    # дефолт: показати меню
    await update.message.reply_text("Натисни кнопку з меню 👇", reply_markup=MAIN_KB)
    return CHOOSING

async def income_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        return await menu(update, context)

    amount, note = parse_amount_and_note(text)
    if amount is None:
        await update.message.reply_text("Не бачу суму 😅 Напиши, наприклад: `5000 зарплата`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
        return INCOME_INPUT

    user = update.effective_user
    insert_tx(user.id, get_user_display(update), "income", amount, category=None, note=note or None)

    await update.message.reply_text(
        f"✅ Додано дохід: *{format_money(amount)}*"
        + (f"\n📝 {note}" if note else ""),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSING

async def save_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        return await menu(update, context)

    amount, note = parse_amount_and_note(text)
    if amount is None:
        await update.message.reply_text("Не бачу суму 😅 Напиши, наприклад: `1000 на відпустку`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
        return SAVE_INPUT

    user = update.effective_user
    insert_tx(user.id, get_user_display(update), "saved", amount, category=None, note=note or None)

    await update.message.reply_text(
        f"✅ Відкладено: *{format_money(amount)}*"
        + (f"\n📝 {note}" if note else ""),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    return CHOOSING

async def expense_choose_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        return await menu(update, context)

    if text == "📌 Інше (ввести свою)":
        await update.message.reply_text(
            "📁 Введи свою категорію (наприклад: `Кафе`, `Ремонт`, `Дитина`):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )
        return EXPENSE_CUSTOM_CAT

    # якщо обрали існуючу категорію
    context.user_data["expense_category"] = text
    await update.message.reply_text(
        f"➖ Категорія: *{text}*\nТепер введи суму і (за бажанням) примітку.\nНаприклад: `350 кава`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=CANCEL_KB
    )
    return EXPENSE_INPUT

async def expense_custom_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        return await menu(update, context)

    # беремо як категорію
    context.user_data["expense_category"] = text
    await update.message.reply_text(
        f"➖ Категорія: *{text}*\nТепер введи суму і (за бажанням) примітку.\nНаприклад: `350 кава`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=CANCEL_KB
    )
    return EXPENSE_INPUT

async def expense_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in (BTN_CANCEL, BTN_MENU):
        return await menu(update, context)

    amount, note = parse_amount_and_note(text)
    if amount is None:
        await update.message.reply_text("Не бачу суму 😅 Напиши, наприклад: `500 їжа`", parse_mode=ParseMode.MARKDOWN, reply_markup=CANCEL_KB)
        return EXPENSE_INPUT

    cat = context.user_data.get("expense_category") or "(без категорії)"
    user = update.effective_user
    insert_tx(user.id, get_user_display(update), "expense", amount, category=cat, note=note or None)

    await update.message.reply_text(
        f"✅ Додано витрату: *{format_money(amount)}*\n📁 {cat}"
        + (f"\n📝 {note}" if note else ""),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_KB
    )
    context.user_data.pop("expense_category", None)
    return CHOOSING

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано ✅", reply_markup=MAIN_KB)
    return CHOOSING

# =========================
# MAIN
# =========================
def build_app() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_MENU)}$"), menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, choose_action),
        ],
        states={
            CHOOSING: [
                MessageHandler(filters.Regex(rf"^{re.escape(BTN_MENU)}$"), menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, choose_action),
            ],
            INCOME_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, income_input)],
            SAVE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_input)],
            EXPENSE_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, expense_choose_cat)],
            EXPENSE_CUSTOM_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, expense_custom_cat)],
            EXPENSE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, expense_input)],
        },
        fallbacks=[
            MessageHandler(filters.Regex(rf"^{re.escape(BTN_CANCEL)}$"), cancel),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
    )

    # /help як команда
    app.add_handler(CommandHandler("help", help_cmd))

    # “Прокидається” на привітання
    app.add_handler(
        MessageHandler(
            filters.Regex(r"(?i)^(привіт|привет|хай|здрастуй|доброго дня|добрий день|hello|hi)\b"),
            greet_wakeup
        )
    )

    app.add_handler(conv)
    return app

def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
