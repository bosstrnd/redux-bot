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
import re

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

# Каналы по умолчанию (используются при первом запуске; дальше список
# редактируется командами /addchannel, /removechannel и хранится в файле).
DEFAULT_CHANNELS = [
    {"username": "@ceogtamedia", "url": "https://t.me/ceogtamedia"},
    {"username": "@igoresh1x", "url": "https://t.me/igoresh1x"},
]

# Папка для данных (на BotHost /app/data сохраняется при перезапуске)
DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(DATA_DIR, "state.json")
ADMINS_PATH = os.path.join(DATA_DIR, "admins.json")
CHANNELS_PATH = os.path.join(DATA_DIR, "channels.json")
PENDING_PATH = os.path.join(DATA_DIR, "pending.json")

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
# Каналы-условия (динамический список)
# ---------------------------------------------------------------------------
def load_channels() -> list[dict]:
    """Текущий список обязательных каналов (при первом запуске — из defaults)."""
    data = _read_json(CHANNELS_PATH, None)
    if data is None:
        _write_json(CHANNELS_PATH, DEFAULT_CHANNELS)
        return list(DEFAULT_CHANNELS)
    return data


def save_channels(channels: list[dict]) -> None:
    _write_json(CHANNELS_PATH, channels)


def parse_channel(text: str) -> dict | None:
    """Принимает @name, https://t.me/name или name -> {username, url}."""
    text = (text or "").strip()
    m = re.search(r"(?:https?://)?(?:t\.me/|@)?([A-Za-z0-9_]{4,32})", text)
    if not m:
        return None
    name = m.group(1)
    return {"username": f"@{name}", "url": f"https://t.me/{name}"}


def add_channel(channel: dict) -> bool:
    channels = load_channels()
    if any(c["username"].lower() == channel["username"].lower() for c in channels):
        return False
    channels.append(channel)
    save_channels(channels)
    return True


def remove_channel(username: str) -> bool:
    username = username.lower()
    channels = load_channels()
    new = [c for c in channels if c["username"].lower() != username]
    if len(new) == len(channels):
        return False
    save_channels(new)
    return True


# Заявки на совместный розыгрыш: {channel_username: {"requester_id":..., "requester":...}}
def load_pending() -> dict:
    return _read_json(PENDING_PATH, {})


def save_pending(data: dict) -> None:
    _write_json(PENDING_PATH, data)


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
def subscribe_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"📢 {ch['username']}", url=ch["url"])]
        for ch in load_channels()
    ]
    rows.append([InlineKeyboardButton("✅ Я подписался — проверить", callback_data="check_subs")])
    return InlineKeyboardMarkup(rows)


async def missing_subscriptions(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> list[dict]:
    """Возвращает список каналов, на которые пользователь НЕ подписан."""
    missing = []
    for ch in load_channels():
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
            f"🔧 Ты админ. Текущий режим: <b>{mode_label(mode)}</b>.\n\n"
            "Команды:\n"
            "/mode — переключить режим\n"
            "/channels — список каналов-условий\n"
            "/addchannel @канал — совместный розыгрыш (запрос согласия партнёра)\n"
            "/removechannel @канал — убрать канал",
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


# ---------------------------------------------------------------------------
# Управление каналами-условиями и совместные розыгрыши
# ---------------------------------------------------------------------------
async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    remember_admin(user)
    if not is_admin(user):
        await update.message.reply_text("Эта команда только для администраторов.")
        return
    channels = load_channels()
    if channels:
        lines = "\n".join(f"• {c['username']}" for c in channels)
    else:
        lines = "(пусто)"
    await update.message.reply_text(
        "📋 Обязательные каналы для доступа:\n" + lines + "\n\n"
        "Добавить (совместный розыгрыш): /addchannel @канал\n"
        "Убрать: /removechannel @канал"
    )


async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускает согласование совместного розыгрыша с владельцем другого канала."""
    user = update.effective_user
    remember_admin(user)
    if not is_admin(user):
        await update.message.reply_text("Эта команда только для администраторов.")
        return

    if not context.args:
        await update.message.reply_text("Укажи канал: /addchannel @канал")
        return

    channel = parse_channel(context.args[0])
    if not channel:
        await update.message.reply_text("Не понял канал. Пример: /addchannel @mychannel")
        return

    if any(c["username"].lower() == channel["username"].lower() for c in load_channels()):
        await update.message.reply_text(f"{channel['username']} уже в списке условий.")
        return

    # Бот должен быть админом в этом канале
    try:
        me = await context.bot.get_me()
        bot_member = await context.bot.get_chat_member(channel["username"], me.id)
        if bot_member.status != "administrator":
            raise RuntimeError("not admin")
    except Exception:
        await update.message.reply_text(
            f"⚠️ Сначала добавь меня администратором в {channel['username']}, "
            "иначе я не смогу согласовать совместный розыгрыш."
        )
        return

    # Находим админов канала и шлём им запрос на согласие в личку
    try:
        admins = await context.bot.get_chat_administrators(channel["username"])
    except Exception:
        logger.exception("Не удалось получить админов %s", channel["username"])
        await update.message.reply_text(
            f"⚠️ Не получилось получить администраторов {channel['username']}."
        )
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Согласиться", callback_data=f"jga:{channel['username']}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"jgr:{channel['username']}"),
            ]
        ]
    )
    requester = f"@{user.username}" if user.username else user.full_name
    text = (
        "🤝 Запрос на <b>совместный розыгрыш</b>\n\n"
        f"Канал <b>{channel['username']}</b> предлагают добавить в обязательные подписки "
        "для доступа к TRND Redux.\n"
        f"Инициатор: {requester}\n\n"
        "Согласны на совместный розыгрыш?"
    )

    delivered = 0
    for adm in admins:
        u = adm.user
        if u.is_bot:
            continue
        try:
            await context.bot.send_message(
                chat_id=u.id, text=text, parse_mode="HTML", reply_markup=keyboard
            )
            delivered += 1
        except Exception:
            logger.info("Не удалось написать админу %s канала %s", u.id, channel["username"])

    if delivered == 0:
        await update.message.reply_text(
            f"⚠️ Не удалось отправить запрос админам {channel['username']}.\n"
            f"Попроси их зайти в @{(await context.bot.get_me()).username} и нажать /start, "
            "затем повтори /addchannel."
        )
        return

    pending = load_pending()
    pending[channel["username"].lower()] = {
        "requester_id": user.id,
        "requester": requester,
        "url": channel["url"],
        "username": channel["username"],
    }
    save_pending(pending)

    await update.message.reply_text(
        f"📨 Запрос на совместный розыгрыш отправлен админам {channel['username']} "
        f"({delivered}). Канал добавится в условия после их согласия."
    )


async def removechannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    remember_admin(user)
    if not is_admin(user):
        await update.message.reply_text("Эта команда только для администраторов.")
        return
    if not context.args:
        await update.message.reply_text("Укажи канал: /removechannel @канал")
        return
    channel = parse_channel(context.args[0])
    if not channel:
        await update.message.reply_text("Не понял канал. Пример: /removechannel @mychannel")
        return
    if remove_channel(channel["username"]):
        await update.message.reply_text(f"✅ {channel['username']} убран из условий.")
    else:
        await update.message.reply_text(f"{channel['username']} не найден в списке.")


async def on_joint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Владелец второго канала согласился/отклонил совместный розыгрыш."""
    query = update.callback_query
    action, _, channel_username = (query.data or "").partition(":")

    # Подтверждаем, что нажавший — админ этого канала
    try:
        member = await context.bot.get_chat_member(channel_username, query.from_user.id)
        if member.status not in ("administrator", "creator", "owner"):
            await query.answer(
                f"Согласовать может только админ {channel_username}.", show_alert=True
            )
            return
    except Exception:
        await query.answer("Не удалось проверить твои права в канале.", show_alert=True)
        return

    pending = load_pending()
    info = pending.pop(channel_username.lower(), None)
    url = (info or {}).get("url", f"https://t.me/{channel_username.lstrip('@')}")
    who = f"@{query.from_user.username}" if query.from_user.username else query.from_user.full_name

    if action == "jga":
        added = add_channel({"username": channel_username, "url": url})
        status = (
            f"✅ Совместный розыгрыш согласован — {channel_username} добавлен в условия."
            if added
            else f"✅ {channel_username} уже был в условиях."
        )
        # Сообщаем инициатору
        if info:
            try:
                await context.bot.send_message(
                    chat_id=info["requester_id"],
                    text=f"🤝 Админ {channel_username} ({who}) согласился на совместный розыгрыш. "
                    f"Канал добавлен в обязательные подписки.",
                )
            except Exception:
                pass
    else:
        status = f"❌ Совместный розыгрыш с {channel_username} отклонён."
        if info:
            try:
                await context.bot.send_message(
                    chat_id=info["requester_id"],
                    text=f"❌ Админ {channel_username} ({who}) отклонил совместный розыгрыш.",
                )
            except Exception:
                pass

    save_pending(pending)
    try:
        await query.edit_message_text(status)
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await query.answer("Готово.")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Укажи его в файле .env")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CommandHandler("channels", channels_command))
    application.add_handler(CommandHandler("addchannel", addchannel_command))
    application.add_handler(CommandHandler("removechannel", removechannel_command))
    application.add_handler(CallbackQueryHandler(on_check_subs, pattern=r"^check_subs$"))
    application.add_handler(CallbackQueryHandler(on_set_mode, pattern=r"^mode:"))
    application.add_handler(CallbackQueryHandler(on_review, pattern=r"^(approve|deny):"))
    application.add_handler(CallbackQueryHandler(on_joint, pattern=r"^jg[ar]:"))
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_screenshot)
    )

    logger.info("Redux-бот запущен. Ожидаю сообщения...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
