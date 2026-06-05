"""
Telegram-бот «TRND Redux» — выдаёт доступ к Редуксу.

Два режима (переключают админы):
1. "subscription" — доступ за подписку на каналы (бот проверяет подписку).
2. "promocode"    — пользователь присылает скриншот ввода личного промокода,
                    админы одобряют (да/нет), и тогда выдаётся доступ.

Админы (@ceoprmanager, @igoreshqa_w) командой /mode переключают режим.
"""

import json
import logging
import os

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# Чат/канал, куда приходят заявки (скриншоты) на проверку. Если пусто —
# заявки уходят в личку админам, которые запускали бота.
# Задаётся через переменную окружения REVIEW_CHAT_ID (в .env или в панели хостинга).
REVIEW_CHAT_ID = os.environ.get("REVIEW_CHAT_ID", "").strip()

# Сообщение со ссылкой/доступом к Редуксу. Задаётся через переменную окружения
# REDUX_ACCESS_TEXT (в .env или в панели хостинга) — в коде ссылку не храним.
REDUX_ACCESS_TEXT = os.environ.get(
    "REDUX_ACCESS_TEXT",
    "🔗 Доступ к TRND Redux: (ссылка не настроена — задайте REDUX_ACCESS_TEXT)",
).strip()

# Админы по username (без @, в нижнем регистре)
ADMIN_USERNAMES = {"ceoprmanager", "igoreshqa_w"}

# Каналы, на которые нужно подписаться
REQUIRED_CHANNELS = [
    {"username": "@ceogtamedia", "url": "https://t.me/ceogtamedia"},
    {"username": "@igoresh1x", "url": "https://t.me/igoresh1x"},
]

# Папка для данных (на BotHost /app/data сохраняется при перезапуске)
DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(DATA_DIR, "state.json")
ADMINS_PATH = os.path.join(DATA_DIR, "admins.json")

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Хранилище (режим + известные ID админов)
# ---------------------------------------------------------------------------
def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: str, data) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_mode() -> str:
    return _read_json(STATE_PATH, {"mode": "subscription"}).get("mode", "subscription")


def set_mode(mode: str) -> None:
    _write_json(STATE_PATH, {"mode": mode})


def remember_admin(user) -> None:
    """Запоминает ID админа, чтобы слать ему заявки на проверку."""
    if not is_admin(user):
        return
    admins = _read_json(ADMINS_PATH, {})
    if admins.get(user.username.lower()) != user.id:
        admins[user.username.lower()] = user.id
        _write_json(ADMINS_PATH, admins)


def is_admin(user) -> bool:
    return bool(user and user.username and user.username.lower() in ADMIN_USERNAMES)


def review_targets() -> set[int]:
    """Кому слать заявки на проверку (скриншоты)."""
    # Если задан общий чат проверки — шлём только туда (без дублей в личку).
    if REVIEW_CHAT_ID:
        try:
            return {int(REVIEW_CHAT_ID)}
        except ValueError:
            pass
    # Иначе — в личку известным админам.
    return {uid for uid in _read_json(ADMINS_PATH, {}).values()}


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
def subscribe_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"📢 {ch['username']}", url=ch["url"])]
        for ch in REQUIRED_CHANNELS
    ]
    rows.append([InlineKeyboardButton("✅ Я подписался — проверить", callback_data="check_subs")])
    return InlineKeyboardMarkup(rows)


async def missing_subscriptions(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> list[dict]:
    """Возвращает список каналов, на которые пользователь НЕ подписан."""
    missing = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(ch["username"], user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            logger.exception(
                "Не удалось проверить подписку на %s (бот добавлен админом в канал?)",
                ch["username"],
            )
            missing.append(ch)
    return missing


async def grant_access(context: ContextTypes.DEFAULT_TYPE, chat_id: int, intro: str) -> bool:
    try:
        await context.bot.send_message(chat_id=chat_id, text=f"{intro}\n\n{REDUX_ACCESS_TEXT}")
        return True
    except Exception:
        logger.exception("Не удалось выдать доступ пользователю %s", chat_id)
        return False


# ---------------------------------------------------------------------------
# Хендлеры пользователя
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    remember_admin(user)
    mode = get_mode()

    if mode == "promocode":
        await update.message.reply_text(
            "👋 Привет! Чтобы получить доступ к <b>TRND Redux</b>, пришли "
            "скриншот ввода личного промокода. Заявку проверит администратор.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "👋 Привет! Чтобы получить доступ к <b>TRND Redux</b>, подпишись на "
            "оба канала ниже и нажми «Проверить».",
            parse_mode="HTML",
            reply_markup=subscribe_keyboard(),
        )

    if is_admin(user):
        await update.message.reply_text(
            f"🔧 Ты админ. Текущий режим: <b>{mode_label(mode)}</b>.\n"
            "Команда /mode — переключить режим.",
            parse_mode="HTML",
        )


async def on_check_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if get_mode() != "subscription":
        await query.edit_message_text("Сейчас доступ выдаётся по личному промокоду. Пришли скриншот.")
        return

    missing = await missing_subscriptions(context, query.from_user.id)
    if missing:
        lines = "\n".join(f"• {ch['username']}" for ch in missing)
        await query.answer(
            "Ты ещё не подписан на: " + ", ".join(ch["username"] for ch in missing),
            show_alert=True,
        )
        await query.edit_message_text(
            "❌ Похоже, ты подписан не на все каналы.\n\n"
            f"Подпишись на:\n{lines}\n\nЗатем нажми «Проверить» снова.",
            reply_markup=subscribe_keyboard(),
        )
        return

    await query.edit_message_text("✅ Подписка подтверждена!")
    await grant_access(context, query.from_user.id, "✅ Подписка подтверждена!")


async def on_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь прислал скриншот промокода (режим promocode)."""
    if get_mode() != "promocode":
        # В режиме подписки скриншоты не нужны
        await update.message.reply_text(
            "Сейчас доступ выдаётся за подписку на каналы. Нажми /start.",
        )
        return

    message = update.message
    user = message.from_user
    username = f"@{user.username}" if user.username else "—"
    caption = (
        "🧾 Заявка на TRND Redux (промокод)\n\n"
        f"👤 Пользователь: {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📝 Имя: {user.full_name}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{user.id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"deny:{user.id}"),
            ]
        ]
    )

    targets = review_targets()
    if not targets:
        logger.warning("Нет получателей заявок: админы ещё не запускали бота и REVIEW_CHAT_ID пуст")
        await message.reply_text(
            "⚠️ Заявку временно некому проверить. Попробуй позже."
        )
        return

    sent_any = False
    for target in targets:
        try:
            await context.bot.send_photo(
                chat_id=target,
                photo=message.photo[-1].file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            sent_any = True
        except Exception:
            logger.exception("Не удалось отправить заявку на проверку в %s", target)

    if sent_any:
        await message.reply_text("📨 Заявка отправлена на проверку. Ожидай решения администратора.")
    else:
        await message.reply_text("⚠️ Не получилось отправить заявку. Попробуй позже.")


async def on_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ нажал «Одобрить»/«Отклонить» под заявкой."""
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("Только администратор может проверять заявки.", show_alert=True)
        return

    action, _, uid_str = (query.data or "").partition(":")
    try:
        user_id = int(uid_str)
    except ValueError:
        await query.answer("Некорректные данные заявки.", show_alert=True)
        return

    if action == "approve":
        ok = await grant_access(context, user_id, "✅ Заявка одобрена!")
        status = "✅ ОДОБРЕНО" + ("" if ok else " (⚠️ игрок не получил сообщение)")
    else:
        status = "❌ ОТКЛОНЕНО"
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Заявка отклонена. Попробуй ещё раз.",
            )
        except Exception:
            logger.exception("Не удалось написать игроку %s", user_id)
            status += " (⚠️ игрок не получил сообщение)"

    note = f"\n\n{status} — @{query.from_user.username}"
    try:
        await query.edit_message_caption(
            caption=(query.message.caption_html or query.message.caption or "") + note,
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await query.answer("Готово.")


# ---------------------------------------------------------------------------
# Админские хендлеры
# ---------------------------------------------------------------------------
def mode_label(mode: str) -> str:
    return "проверка подписки" if mode == "subscription" else "личный промокод"


def mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Проверка подписки", callback_data="mode:subscription")],
            [InlineKeyboardButton("🧾 Личный промокод", callback_data="mode:promocode")],
        ]
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    remember_admin(user)
    if not is_admin(user):
        await update.message.reply_text("Эта команда только для администраторов.")
        return
    await update.message.reply_text(
        f"Текущий режим: <b>{mode_label(get_mode())}</b>.\nВыбери новый:",
        parse_mode="HTML",
        reply_markup=mode_keyboard(),
    )


async def on_set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("Только для администраторов.", show_alert=True)
        return
    _, _, mode = (query.data or "").partition(":")
    if mode not in ("subscription", "promocode"):
        await query.answer("Неизвестный режим.", show_alert=True)
        return
    set_mode(mode)
    await query.answer("Режим переключён.")
    await query.edit_message_text(
        f"✅ Режим переключён на: <b>{mode_label(mode)}</b>.",
        parse_mode="HTML",
    )


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Укажи его в файле .env")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CallbackQueryHandler(on_check_subs, pattern=r"^check_subs$"))
    application.add_handler(CallbackQueryHandler(on_set_mode, pattern=r"^mode:"))
    application.add_handler(CallbackQueryHandler(on_review, pattern=r"^(approve|deny):"))
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_screenshot)
    )

    logger.info("Redux-бот запущен. Ожидаю сообщения...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
