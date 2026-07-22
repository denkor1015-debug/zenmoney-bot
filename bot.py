"""
Telegram bot for ZenMoney — aiogram 3.x
Handlers: /start, /refresh, /accounts, text messages
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import date
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

from parser import TransactionParser
from zenmoney import ZenMoneyClient, load_state, save_state

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# In-memory pending confirmations: { user_id: [zenmoney_txn_dicts] }
_pending: dict[int, list[dict]] = {}


# ---------------------------------------------------------------------------
# Security guard
# ---------------------------------------------------------------------------

def _is_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CURRENCY_SYMBOLS = {
    4: "₴",    # UAH (ZenMoney internal ID)
    1: "$",    # USD (ZenMoney internal ID)
    2: "€",    # EUR (ZenMoney internal ID)
    980: "₴",  # UAH (ISO)
    978: "€",  # EUR (ISO)
    840: "$",  # USD (ISO)
    643: "₽",  # RUB (ISO)
    826: "£",  # GBP (ISO)
}

TYPE_EMOJI = {
    "expense": "💸",
    "income": "💰",
    "transfer": "🔄",
}


def _currency_symbol(instrument_id: int) -> str:
    return CURRENCY_SYMBOLS.get(instrument_id, "?")


def _find_account(name: str | None, state: dict) -> dict:
    """
    Find account by fuzzy name match.
    Falls back to defaultAccount if name is None or no match found.
    """
    accounts: dict[str, dict] = state.get("accounts", {})
    default_key = state.get("defaultAccount")

    if not accounts:
        return {}

    def _default() -> dict:
        if default_key and default_key in accounts:
            return accounts[default_key]
        # last resort: first account
        return next(iter(accounts.values()))

    if name is None:
        return _default()

    name_lower = name.lower().strip()
    # exact key match
    if name_lower in accounts:
        return accounts[name_lower]
    # substring match
    for key, acc in accounts.items():
        if name_lower in key or key in name_lower:
            return acc
    return _default()


def _build_zenmoney_txn(
    parsed: dict,
    state: dict,
    tag_uuid: str | None,
) -> dict:
    """
    Convert a parsed transaction dict into a fully-formed ZenMoney transaction.
    Per official API docs, all nullable fields must be present.
    """
    now = int(time.time())
    user_id = state.get("userId") or 0

    account = _find_account(parsed.get("account"), state)
    acc_id = account.get("id", "")
    instrument = account.get("instrument", 0)

    amount = float(parsed.get("amount", 0))
    txn_type = parsed.get("type", "expense")
    payee = parsed.get("payee")
    comment = parsed.get("comment", "")
    txn_date = parsed.get("date", date.today().isoformat())

    to_account_title = None

    if txn_type == "income":
        income = amount
        outcome = 0.0
        income_account = acc_id
        outcome_account = acc_id
        income_instrument = instrument
        outcome_instrument = instrument
    elif txn_type == "transfer":
        dst_account = _find_account(parsed.get("to_account"), state)
        dst_acc_id = dst_account.get("id", "")
        dst_instrument = dst_account.get("instrument", 0)
        to_account_title = dst_account.get("title", "")

        income = amount
        outcome = amount
        income_account = dst_acc_id
        outcome_account = acc_id
        income_instrument = dst_instrument
        outcome_instrument = instrument
    else:  # expense
        income = 0.0
        outcome = amount
        income_account = acc_id
        outcome_account = acc_id
        income_instrument = instrument
        outcome_instrument = instrument

    return {
        "id": str(uuid.uuid4()),
        "changed": now,
        "created": now,
        "user": user_id,
        "deleted": False,
        "incomeInstrument": income_instrument,
        "incomeAccount": income_account,
        "income": income,
        "outcomeInstrument": outcome_instrument,
        "outcomeAccount": outcome_account,
        "outcome": outcome,
        "tag": [tag_uuid] if tag_uuid else [],
        "merchant": None,
        "payee": payee,
        "originalPayee": None,
        "comment": comment,
        "date": txn_date,
        "mcc": None,
        "reminderMarker": None,
        "opIncome": None,
        "opIncomeInstrument": None,
        "opOutcome": None,
        "opOutcomeInstrument": None,
        "latitude": None,
        "longitude": None,
        "incomeBankID": None,
        "outcomeBankID": None,
        "qrCode": None,
        # Store extras for confirmation display (stripped before saving)
        "_meta": {
            "type": txn_type,
            "amount": amount,
            "account_title": account.get("title", ""),
            "to_account_title": to_account_title,
            "tag_title": None,  # filled by caller
            "instrument": instrument,
        },
    }


def _format_confirmation(zn_txns: list[dict]) -> str:
    """Build human-readable confirmation message."""
    lines = [f"📋 Розпізнано {len(zn_txns)} транзакц{'ію' if len(zn_txns) == 1 else 'ії' if len(zn_txns) < 5 else 'ій'}:\n"]
    for i, txn in enumerate(zn_txns, 1):
        meta = txn.get("_meta", {})
        txn_type = meta.get("type", "expense")
        amount = meta.get("amount", txn.get("outcome") or txn.get("income", 0))
        account_title = meta.get("account_title", "")
        to_account_title = meta.get("to_account_title")
        tag_title = meta.get("tag_title") or "без категорії"
        symbol = _currency_symbol(meta.get("instrument", 0))
        comment = txn.get("comment", "")
        payee = txn.get("payee", "")
        emoji = TYPE_EMOJI.get(txn_type, "💳")

        label = payee or comment or "транзакція"
        if txn_type == "transfer" and to_account_title:
            acc_display = f"{account_title} ➡️ {to_account_title}"
        else:
            acc_display = account_title

        lines.append(f"{i}. {emoji} {label} — {amount:,.0f} {symbol} ({acc_display})")
        lines.append(f"   📁 {tag_title}\n")

    lines.append("Зберегти всі?")
    return "\n".join(lines)


def _strip_meta(txns: list[dict]) -> list[dict]:
    """Remove _meta keys before sending to ZenMoney."""
    clean = []
    for t in txns:
        c = {k: v for k, v in t.items() if k != "_meta"}
        clean.append(c)
    return clean


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return

    state = load_state()
    if not state.get("accounts"):
        await message.answer("⏳ Підключаюсь до ZenMoney, отримую дані…")
        try:
            client = ZenMoneyClient()
            state = await client.fetch_initial_data()
        except Exception as exc:
            logger.error("fetch_initial_data failed: %s", exc)
            await message.answer(f"❌ Помилка підключення до ZenMoney: {exc}\n\nПеревір ZENMONEY_TOKEN у .env")
            return

    acc_count = len(state.get("accounts", {}))
    tag_count = len(state.get("tags", {}))
    default = state.get("defaultAccount", "—")
    await message.answer(
        f"✅ Підключено до ZenMoney!\n\n"
        f"📊 Знайдено: {acc_count} рахунків, {tag_count} категорій\n"
        f"💳 Рахунок за замовчуванням: <b>{default}</b>\n\n"
        "Надсилай транзакції у будь-якому форматі:\n"
        "<i>кава 85, аптека 120 монобанк, АТБ 340 готівка</i>",
        parse_mode="HTML",
    )


@dp.message(Command("refresh"))
async def cmd_refresh(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return

    await message.answer("⏳ Оновлюю дані з ZenMoney…")
    try:
        client = ZenMoneyClient()
        state = await client.fetch_initial_data()
    except Exception as exc:
        logger.error("refresh failed: %s", exc)
        await message.answer(f"❌ Помилка ZenMoney API. {exc}\n\nСпробуй пізніше або перевір токен.")
        return

    acc_count = len(state.get("accounts", {}))
    tag_count = len(state.get("tags", {}))
    await message.answer(
        f"🔄 Дані оновлено.\n"
        f"Знайдено <b>{acc_count}</b> рахунків, <b>{tag_count}</b> категорій.",
        parse_mode="HTML",
    )


@dp.message(Command("accounts"))
async def cmd_accounts(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return

    await message.answer("⏳ Отримую баланси…")
    try:
        client = ZenMoneyClient()
        accounts = await client.get_balances()
    except Exception as exc:
        logger.error("get_balances failed: %s", exc)
        await message.answer("❌ Помилка ZenMoney API. Спробуй /refresh")
        return

    if not accounts:
        await message.answer("Рахунків не знайдено. Спробуй /refresh")
        return

    lines = ["💳 <b>Рахунки:</b>\n"]
    for acc in accounts:
        balance = acc.get("balance", 0)
        symbol = _currency_symbol(acc.get("instrument", 0))
        sign = "+" if balance >= 0 else ""
        lines.append(f"• {acc['title']}: <b>{sign}{balance:,.2f} {symbol}</b>")

    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_transaction(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return

    state = load_state()
    if not state.get("accounts"):
        await message.answer("⚠️ Спочатку виконай /start щоб підключитись до ZenMoney.")
        return

    # 1. Parse with Claude
    await message.answer("🤔 Розпізнаю транзакцію…")
    try:
        parser = TransactionParser()
        account_names = list(state.get("accounts", {}).keys())

        # Build hierarchical category list for Claude: "Продукты > АТБ"
        tags = state.get("tags", {})
        category_names = []
        for name, info in tags.items():
            parent_title = info.get("parent_title") if isinstance(info, dict) else None
            if parent_title:
                category_names.append(f"{parent_title} > {name}")
            else:
                category_names.append(name)

        parsed_list = await parser.parse(message.text, account_names, category_names)
    except ValueError as exc:
        logger.warning("Parsing failed: %s", exc)
        await message.answer(
            "❌ Не вдалось розпізнати. Спробуй інший формат.\n"
            "Приклад: <i>кава 85, продукти АТБ 340 готівка</i>",
            parse_mode="HTML",
        )
        return
    except Exception as exc:
        logger.error("Unexpected parsing error: %s", exc)
        await message.answer("❌ Помилка AI сервісу. Спробуй пізніше.")
        return

    # 2. For each parsed txn: match category, build ZenMoney txn
    client = ZenMoneyClient()
    zn_txns: list[dict] = []

    for parsed in parsed_list:
        payee = parsed.get("payee")
        category = parsed.get("category")
        tag_uuid: str | None = None
        tag_title: str | None = None

        tags = state.get("tags", {})

        # Helper to extract tag id from tags dict (supports both old and new format)
        def _tag_id(info):
            return info["id"] if isinstance(info, dict) else info

        def _tag_parent(info):
            return info.get("parent_title") if isinstance(info, dict) else None

        # 2a. Match category from Claude to local tags
        if category:
            cat_lower = category.lower().strip()

            # Handle "parent > child" format from Claude
            if " > " in cat_lower:
                parts = cat_lower.split(" > ", 1)
                child = parts[-1].strip()
                if child in tags:
                    tag_uuid = _tag_id(tags[child])
                    parent_t = _tag_parent(tags[child])
                    tag_title = f"{parent_t.capitalize()} > {child.capitalize()}" if parent_t else child.capitalize()

            # Exact match
            if not tag_uuid and cat_lower in tags:
                tag_uuid = _tag_id(tags[cat_lower])
                parent_t = _tag_parent(tags[cat_lower])
                tag_title = f"{parent_t.capitalize()} > {cat_lower.capitalize()}" if parent_t else cat_lower.capitalize()

            # Substring match
            if not tag_uuid:
                for t_name, t_info in tags.items():
                    if cat_lower in t_name or t_name in cat_lower:
                        tag_uuid = _tag_id(t_info)
                        parent_t = _tag_parent(t_info)
                        tag_title = f"{parent_t.capitalize()} > {t_name.capitalize()}" if parent_t else t_name.capitalize()
                        break

        # 2b. Fallback: try suggest API
        if not tag_uuid and payee:
            try:
                suggest_resp = await client.suggest(payee)
                tags_suggested = suggest_resp.get("tag", [])
                if tags_suggested:
                    first = tags_suggested[0]
                    if isinstance(first, dict):
                        tag_uuid = first.get("id")
                        tag_title = first.get("title")
                    else:
                        tag_uuid = str(first)
                        for title, t_info in tags.items():
                            if _tag_id(t_info) == tag_uuid:
                                tag_title = title.capitalize()
                                break
            except Exception as exc:
                logger.warning("suggest() error: %s", exc)

        # 2c. Fallback: fuzzy match payee to local tags
        if not tag_uuid and payee:
            payee_lower = payee.lower()
            for t_title, t_info in tags.items():
                if t_title in payee_lower or payee_lower in t_title:
                    tag_uuid = _tag_id(t_info)
                    parent_t = _tag_parent(t_info)
                    tag_title = f"{parent_t.capitalize()} > {t_title.capitalize()}" if parent_t else t_title.capitalize()
                    break

        txn = _build_zenmoney_txn(parsed, state, tag_uuid)
        txn["_meta"]["tag_title"] = tag_title
        zn_txns.append(txn)

    # 3. Show confirmation
    _pending[message.from_user.id] = zn_txns

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Так, зберегти", callback_data="confirm_save"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data="confirm_cancel"),
        ]
    ])

    await message.answer(
        _format_confirmation(zn_txns),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "confirm_save")
async def callback_confirm_save(callback: CallbackQuery) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer()
        return

    zn_txns = _pending.pop(callback.from_user.id, None)
    if not zn_txns:
        await callback.message.edit_text("⚠️ Сесія підтвердження вже закінчилась. Надішли повідомлення знову.")
        await callback.answer()
        return

    clean_txns = _strip_meta(zn_txns)
    try:
        client = ZenMoneyClient()
        await client.add_transactions(clean_txns)
    except Exception as exc:
        logger.error("add_transactions failed: %s", exc)
        await callback.message.edit_text(
            f"❌ Помилка ZenMoney API при збереженні.\n{exc}\n\nСпробуй /refresh"
        )
        await callback.answer()
        return

    count = len(clean_txns)
    await callback.message.edit_text(
        f"✅ Збережено {count} транзакц{'ію' if count == 1 else 'ії' if count < 5 else 'ій'} в ZenMoney! 🎉"
    )
    await callback.answer("Збережено!")


@dp.callback_query(F.data == "confirm_cancel")
async def callback_confirm_cancel(callback: CallbackQuery) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer()
        return

    _pending.pop(callback.from_user.id, None)
    await callback.message.edit_text("❌ Скасовано. Транзакції не збережено.")
    await callback.answer("Скасовано")


# ---------------------------------------------------------------------------
# Entry point & Render health check
# ---------------------------------------------------------------------------

async def start_health_server() -> None:
    """Start a dummy HTTP server on PORT for Render health check."""
    from aiohttp import web
    port = int(os.getenv("PORT", "10000"))

    async def health_handler(request):
        return web.Response(text="ZenMoney Bot is healthy 🤖")

    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/healthz", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Started health check server on port %d", port)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not ALLOWED_USER_ID:
        raise RuntimeError("TELEGRAM_USER_ID is not set in .env")

    # Start dummy HTTP health check server for Render Web Service
    try:
        await start_health_server()
    except Exception as exc:
        logger.warning("Could not start health server: %s", exc)

    logger.info("Starting ZenMoney bot (allowed user_id=%d)", ALLOWED_USER_ID)
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

