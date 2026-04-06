from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup

from arbitrage_bot.core.database import AsyncSessionLocal
from arbitrage_bot.tg_bot.preferences import clear_ui_state
from arbitrage_bot.tg_bot.preferences import ensure_telegram_user
from arbitrage_bot.tg_bot.preferences import format_home_text
from arbitrage_bot.tg_bot.preferences import format_preferences_text
from arbitrage_bot.tg_bot.preferences import format_setting_prompt
from arbitrage_bot.tg_bot.preferences import get_ui_state
from arbitrage_bot.tg_bot.preferences import get_user_preferences
from arbitrage_bot.tg_bot.preferences import reset_user_preferences
from arbitrage_bot.tg_bot.preferences import set_user_preference
from arbitrage_bot.tg_bot.preferences import set_ui_state
from arbitrage_bot.tg_bot.preferences import toggle_mute

router = Router()

@router.message(Command("start"))
async def cmd_start(message):
    async with AsyncSessionLocal() as session:
        await ensure_telegram_user(
            session,
            message.chat.id,
            chat_type=getattr(message.chat, "type", "private"),
        )
        preferences = await get_user_preferences(session, message.chat.id)
        await clear_ui_state(session, message.chat.id)

    await message.answer(
        format_home_text(preferences),
        reply_markup=_build_home_keyboard(preferences),
    )



@router.callback_query(F.data.startswith("tg_nav:"))
async def on_nav_callback(callback):
    action = callback.data.split(":", 1)[1]

    async with AsyncSessionLocal() as session:
        if action == "home":
            preferences = await get_user_preferences(session, callback.message.chat.id)
            await clear_ui_state(session, callback.message.chat.id)
            await _safe_edit_text(
                callback,
                format_home_text(preferences),
                reply_markup=_build_home_keyboard(preferences),
            )
        elif action == "settings":
            preferences = await get_user_preferences(session, callback.message.chat.id)
            await clear_ui_state(session, callback.message.chat.id)
            await _safe_edit_text(
                callback,
                format_preferences_text(preferences),
                reply_markup=_build_settings_keyboard(),
            )
        elif action == "toggle_mute":
            preferences = await toggle_mute(session, callback.message.chat.id)
            await clear_ui_state(session, callback.message.chat.id)
            muted = preferences.get("muted", False)
            toast = "⏸ Alerts paused" if muted else "▶️ Alerts resumed"
            await _safe_edit_text(
                callback,
                format_home_text(preferences),
                reply_markup=_build_home_keyboard(preferences),
            )
            try:
                await callback.answer(toast, show_alert=False)
                return
            except TelegramBadRequest:
                pass
        elif action == "reset":
            preferences = await reset_user_preferences(session, callback.message.chat.id)
            await clear_ui_state(session, callback.message.chat.id)
            await _safe_edit_text(
                callback,
                "Your settings were reset.\n\n"
                "All Telegram filters are disabled for your chat, so you will receive every alert that passes system checks.\n\n"
                f"{format_preferences_text(preferences)}",
                reply_markup=_build_settings_keyboard(),
            )

    await _safe_answer_callback(callback)


@router.callback_query(F.data.startswith("tg_edit:"))
async def on_edit_callback(callback):
    field_name = callback.data.split(":", 1)[1]

    async with AsyncSessionLocal() as session:
        preferences = await get_user_preferences(session, callback.message.chat.id)
        await set_ui_state(
            session,
            callback.message.chat.id,
            {
                "mode": "awaiting_value",
                "field_name": field_name,
                "prompt_message_id": callback.message.message_id,
            },
        )

    await _safe_edit_text(
        callback,
        format_setting_prompt(field_name, preferences),
        reply_markup=_build_prompt_keyboard(),
    )
    await _safe_answer_callback(callback)


@router.message()
async def on_plain_text_setting(message):
    text = (message.text or "").strip()
    if not text:
        return

    async with AsyncSessionLocal() as session:
        ui_state = await get_ui_state(session, message.chat.id)

    if ui_state and ui_state.get("mode") == "awaiting_value":
        field_name = ui_state.get("field_name")
        try:
            value = _parse_setting_value(field_name, text)
        except ValueError as exc:
            await message.answer(str(exc))
            return

        await _apply_setting_update(message, field_name, value)
        return


async def _apply_setting_update(message, field_name, value):
    async with AsyncSessionLocal() as session:
        ui_state = await get_ui_state(session, message.chat.id)
        preferences = await set_user_preference(session, message.chat.id, field_name, value)
        await clear_ui_state(session, message.chat.id)

    prompt_message_id = None
    if ui_state and ui_state.get("mode") == "awaiting_value":
        prompt_message_id = ui_state.get("prompt_message_id")

    text = (
        f"{_SETTINGS_SUCCESS_LABELS[field_name]} updated to {_format_success_value(field_name, value)}.\n\n"
        f"{format_preferences_text(preferences)}"
    )

    if prompt_message_id is not None:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_message_id,
                text=text,
                reply_markup=_build_settings_keyboard(),
            )
            await _safe_delete_message(message)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return

    await message.answer(
        text,
        reply_markup=_build_settings_keyboard(),
    )
    await _safe_delete_message(message)


def _build_home_keyboard(preferences=None):
    muted = (preferences or {}).get("muted", False)
    toggle_text = "▶️ Resume" if muted else "⏸ Pause"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data="tg_nav:toggle_mute",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Settings",
                    callback_data="tg_nav:settings",
                ),
            ]
        ]
    )


def _build_settings_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="→ Min ROI",
                    callback_data="tg_edit:min_roi_percent",
                ),
                InlineKeyboardButton(
                    text="→ Min volume",
                    callback_data="tg_edit:min_capital_usd",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="→ Max volume",
                    callback_data="tg_edit:max_capital_usd",
                ),
                InlineKeyboardButton(
                    text="→ Min profit",
                    callback_data="tg_edit:min_profit_usd",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="→ Max market end",
                    callback_data="tg_edit:max_days_to_close",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Reset all",
                    callback_data="tg_nav:reset",
                ),
                InlineKeyboardButton(
                    text="← Back",
                    callback_data="tg_nav:home",
                ),
            ],
        ]
    )


def _build_prompt_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← Back",
                    callback_data="tg_nav:settings",
                ),
            ]
        ]
    )


def _parse_setting_value(field_name, raw_value):
    value = raw_value.strip().lower()
    if value == "off":
        return None

    if field_name == "min_roi_percent":
        try:
            parsed = float(value)
        except (ValueError, TypeError):
            raise ValueError("Enter a number, e.g. 1.5")
        if parsed < 0:
            raise ValueError("ROI must be zero or greater.")
        return parsed

    if field_name in {"min_capital_usd", "max_capital_usd", "min_profit_usd"}:
        try:
            parsed = float(value)
        except (ValueError, TypeError):
            raise ValueError("Enter a number, e.g. 50")
        if parsed <= 0:
            raise ValueError("Value must be greater than zero.")
        return parsed

    if field_name == "max_days_to_close":
        try:
            parsed = int(value)
        except (ValueError, TypeError):
            raise ValueError("Enter a whole number, e.g. 30")
        if parsed <= 0:
            raise ValueError("Max market end must be greater than zero days.")
        return parsed

    raise ValueError("Unsupported setting.")


async def _safe_edit_text(callback, text, reply_markup):
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _safe_delete_message(message):
    try:
        await message.delete()
    except TelegramBadRequest:
        return


async def _safe_answer_callback(callback):
    try:
        await callback.answer()
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "query is too old" not in error_text and "query id is invalid" not in error_text:
            raise


_SETTINGS_SUCCESS_LABELS = {
    "min_roi_percent": "Min ROI",
    "min_capital_usd": "Min volume",
    "max_capital_usd": "Max volume",
    "min_profit_usd": "Min profit",
    "max_days_to_close": "Max market end",
}


def _format_success_value(field_name, value):
    if value is None:
        return "off"

    if field_name == "min_roi_percent":
        return f"{float(value):.2f}%"

    if field_name in {"min_capital_usd", "max_capital_usd", "min_profit_usd"}:
        return f"${float(value):.0f}"

    if field_name == "max_days_to_close":
        return f"{int(value)} days"

    return str(value)