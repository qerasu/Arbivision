from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from arbitrage_bot.models.orm import SettingsRecord
from arbitrage_bot.models.orm import Subscription
from arbitrage_bot.models.orm import TelegramChat
from arbitrage_bot.models.orm import User
from arbitrage_bot.models.orm import UserPreference
from arbitrage_bot.tg_bot.localization import translate

GLOBAL_SETTINGS_KEY = "tg_alert_prefs:global"
UI_STATE_KEY_PREFIX = "tg_ui_state:"
DEFAULT_PREFERENCES = {
    "min_roi_percent": 5,
    "min_capital_usd": 10,
    "max_capital_usd": 150,
    "min_profit_usd": None,
    "max_days_to_close": 5,
}
FIELD_LABELS = {
    "min_roi_percent": "Min ROI",
    "min_capital_usd": "Min volume",
    "max_capital_usd": "Max volume",
    "min_profit_usd": "Min profit",
    "max_days_to_close": "Max market end",
}
DATETIME_FIELDS = (
    "endDate",
    "end_date",
    "endTime",
    "end_time",
    "closeDate",
    "close_date",
    "closeTime",
    "close_time",
    "closedTime",
    "closed_time",
    "expiration",
    "expirationTime",
    "expiration_time",
    "expiresAt",
    "expires_at",
    "resolveDate",
    "resolve_date",
    "resolutionDate",
    "resolution_date",
)


def default_preferences():
    return dict(DEFAULT_PREFERENCES)


def _make_default_preference(user_id):
    return UserPreference(
        user_id=user_id,
        min_roi_percent=DEFAULT_PREFERENCES["min_roi_percent"],
        min_capital_usd=DEFAULT_PREFERENCES["min_capital_usd"],
        max_capital_usd=DEFAULT_PREFERENCES["max_capital_usd"],
        min_profit_usd=DEFAULT_PREFERENCES["min_profit_usd"],
        max_days_to_close=DEFAULT_PREFERENCES["max_days_to_close"],
        muted=False,
    )


def _serialize_user_preferences(preferences):
    if preferences is None:
        return default_preferences()

    return {
        "min_roi_percent": preferences.min_roi_percent,
        "min_capital_usd": preferences.min_capital_usd,
        "max_capital_usd": preferences.max_capital_usd,
        "min_profit_usd": preferences.min_profit_usd,
        "max_days_to_close": preferences.max_days_to_close,
        "muted": preferences.muted,
    }


async def get_global_preferences(db_session):
    stmt = select(SettingsRecord).where(SettingsRecord.key == GLOBAL_SETTINGS_KEY)
    result = await db_session.execute(stmt)
    setting = result.scalars().first()
    if not setting or not isinstance(setting.value_json, dict):
        return default_preferences()
    preferences = default_preferences()
    preferences.update(setting.value_json)
    return preferences


async def ensure_telegram_user(db_session, chat_id, chat_type="private"):
    chat_id_value = str(chat_id)
    for attempt in range(2):
        stmt = select(TelegramChat).where(TelegramChat.chat_id == chat_id_value)
        result = await db_session.execute(stmt)
        telegram_chat = result.scalars().first()

        should_commit = False

        if telegram_chat is None:
            user = User()
            db_session.add(user)
            await db_session.flush()

            telegram_chat = TelegramChat(
                user_id=user.id,
                chat_id=chat_id_value,
                chat_type=chat_type,
                is_primary=True,
                is_verified=True,
            )
            db_session.add(telegram_chat)
            db_session.add(
                _make_default_preference(user_id=user.id)
            )
            db_session.add(
                Subscription(
                    user_id=user.id,
                    channel="telegram",
                    destination=chat_id_value,
                    status="active",
                )
            )
            should_commit = True
        else:
            pref_stmt = select(UserPreference).where(UserPreference.user_id == telegram_chat.user_id)
            pref_result = await db_session.execute(pref_stmt)
            preferences = pref_result.scalars().first()
            if preferences is None:
                db_session.add(
                    _make_default_preference(user_id=telegram_chat.user_id)
                )
                should_commit = True

            subscription_stmt = select(Subscription).where(
                Subscription.channel == "telegram",
                Subscription.destination == chat_id_value,
            )
            subscription_result = await db_session.execute(subscription_stmt)
            subscription = subscription_result.scalars().first()
            if subscription is None:
                db_session.add(
                    Subscription(
                        user_id=telegram_chat.user_id,
                        channel="telegram",
                        destination=chat_id_value,
                        status="active",
                    )
                )
                should_commit = True
            else:
                if subscription.user_id != telegram_chat.user_id:
                    subscription.user_id = telegram_chat.user_id
                    should_commit = True
                if subscription.status != "active":
                    subscription.status = "active"
                    should_commit = True

        if not should_commit:
            return telegram_chat

        try:
            await db_session.commit()
            return telegram_chat
        except IntegrityError:
            await db_session.rollback()
            if attempt == 1:
                raise

    return telegram_chat


async def get_user_preferences(db_session, chat_id, chat_type="private"):
    telegram_chat = await ensure_telegram_user(db_session, chat_id, chat_type=chat_type)
    stmt = select(UserPreference).where(UserPreference.user_id == telegram_chat.user_id)
    result = await db_session.execute(stmt)
    preferences = result.scalars().first()

    if preferences is None:
        preferences = _make_default_preference(user_id=telegram_chat.user_id)
        db_session.add(preferences)
        await db_session.commit()

    return _serialize_user_preferences(preferences)


async def set_user_preference(db_session, chat_id, field_name, field_value):
    telegram_chat = await ensure_telegram_user(db_session, chat_id)
    stmt = select(UserPreference).where(UserPreference.user_id == telegram_chat.user_id)
    result = await db_session.execute(stmt)
    preferences = result.scalars().first()

    if preferences is None:
        preferences = _make_default_preference(user_id=telegram_chat.user_id)
        db_session.add(preferences)

    setattr(preferences, field_name, field_value)
    preferences.updated_at = datetime.now(timezone.utc)
    await db_session.commit()
    return _serialize_user_preferences(preferences)


async def reset_user_preferences(db_session, chat_id):
    telegram_chat = await ensure_telegram_user(db_session, chat_id)
    stmt = select(UserPreference).where(UserPreference.user_id == telegram_chat.user_id)
    result = await db_session.execute(stmt)
    preferences = result.scalars().first()

    if preferences is None:
        preferences = UserPreference(
            user_id=telegram_chat.user_id,
            muted=False,
        )
        db_session.add(preferences)

    preferences.min_roi_percent = DEFAULT_PREFERENCES["min_roi_percent"]
    preferences.min_capital_usd = DEFAULT_PREFERENCES["min_capital_usd"]
    preferences.max_capital_usd = DEFAULT_PREFERENCES["max_capital_usd"]
    preferences.min_profit_usd = DEFAULT_PREFERENCES["min_profit_usd"]
    preferences.max_days_to_close = DEFAULT_PREFERENCES["max_days_to_close"]
    preferences.updated_at = datetime.now(timezone.utc)
    await db_session.commit()
    return _serialize_user_preferences(preferences)


async def get_telegram_alert_targets(db_session):
    stmt = (
        select(Subscription, UserPreference, User)
        .join(User, Subscription.user_id == User.id)
        .outerjoin(UserPreference, UserPreference.user_id == User.id)
        .where(
            Subscription.channel == "telegram",
            Subscription.status == "active",
            User.status == "active",
        )
    )
    result = await db_session.execute(stmt)
    rows = result.all()
    targets = []

    for subscription, preferences, user in rows:
        pref_values = default_preferences()
        pref_values.update(_serialize_user_preferences(preferences))
        targets.append(
            {
                "user_id": user.id,
                "subscription_id": subscription.id,
                "telegram_chat_id": subscription.destination,
                "preferences": pref_values,
            }
        )

    return targets


async def get_ui_state(db_session, chat_id):
    stmt = select(SettingsRecord).where(SettingsRecord.key == _ui_state_key(chat_id))
    result = await db_session.execute(stmt)
    setting = result.scalars().first()
    if not setting or not isinstance(setting.value_json, dict):
        return None
    return setting.value_json


async def set_global_preference(db_session, field_name, field_value):
    preferences = await get_global_preferences(db_session)
    preferences[field_name] = field_value
    return await _save_global_preferences(db_session, preferences)


async def reset_global_preferences(db_session):
    preferences = default_preferences()
    return await _save_global_preferences(db_session, preferences)


async def set_ui_state(db_session, chat_id, state):
    key = _ui_state_key(chat_id)
    stmt = select(SettingsRecord).where(SettingsRecord.key == key)
    result = await db_session.execute(stmt)
    setting = result.scalars().first()

    if setting is None:
        setting = SettingsRecord(
            key=key,
            value_json=state,
        )
        db_session.add(setting)
    else:
        setting.value_json = state
        setting.updated_at = datetime.now(timezone.utc)

    await db_session.commit()
    return state


async def clear_ui_state(db_session, chat_id):
    return await set_ui_state(db_session, chat_id, {})


async def _save_global_preferences(db_session, preferences):
    stmt = select(SettingsRecord).where(SettingsRecord.key == GLOBAL_SETTINGS_KEY)
    result = await db_session.execute(stmt)
    setting = result.scalars().first()
    if setting is None:
        setting = SettingsRecord(
            key=GLOBAL_SETTINGS_KEY,
            value_json=preferences,
        )
        db_session.add(setting)
    else:
        setting.value_json = preferences
        setting.updated_at = datetime.now(timezone.utc)

    await db_session.commit()
    return preferences


def format_preferences_text(preferences, chat_id=None):
    min_roi_str = _format_roi_value(preferences, chat_id=chat_id)
    min_capital = preferences.get("min_capital_usd")
    min_capital_str = translate(chat_id, "off", "выкл") if min_capital is None else _format_money(min_capital, fallback='')
    max_capital = preferences.get("max_capital_usd")
    max_capital_str = translate(chat_id, "off", "выкл") if max_capital is None else _format_money(max_capital, fallback='')
    min_profit = preferences.get("min_profit_usd")
    min_profit_str = translate(chat_id, "off", "выкл") if min_profit is None else _format_money(min_profit, fallback='')
    max_days = _format_days(preferences.get("max_days_to_close"), chat_id=chat_id)
    return (
        f"{translate(chat_id, '⚙️ Your alert settings', '⚙️ Ваши настройки алертов')}\n\n"
        f"📈 {translate(chat_id, 'Min ROI', 'Мин. ROI')}\n"
        f"{translate(chat_id, 'Current', 'Сейчас')}: {min_roi_str}\n\n"
        f"📦 {translate(chat_id, 'Min volume', 'Мин. объём')}\n"
        f"{translate(chat_id, 'Current', 'Сейчас')}: {min_capital_str}\n\n"
        f"💵 {translate(chat_id, 'Volume', 'Макс. объём')}\n"
        f"{translate(chat_id, 'Current', 'Сейчас')}: {max_capital_str}\n\n"
        f"💰 {translate(chat_id, 'Min profit', 'Мин. прибыль')}\n"
        f"{translate(chat_id, 'Current', 'Сейчас')}: {min_profit_str}\n\n"
        f"⏳ {translate(chat_id, 'Max market end', 'Макс. срок рынка')}\n"
        f"{translate(chat_id, 'Current', 'Сейчас')}: {max_days}"
    )


def format_home_text(preferences, chat_id=None):
    min_capital = preferences.get("min_capital_usd")
    min_capital_str = translate(chat_id, "off", "выкл") if min_capital is None else _format_money(min_capital, fallback='')
    max_capital = preferences.get("max_capital_usd")
    max_capital_str = translate(chat_id, "off", "выкл") if max_capital is None else _format_money(max_capital, fallback='')
    min_profit = preferences.get("min_profit_usd")
    min_profit_str = translate(chat_id, "off", "выкл") if min_profit is None else _format_money(min_profit, fallback='')
    muted = preferences.get("muted", False)
    status_icon = "🔴" if muted else "🟢"
    status_label = translate(chat_id, "Paused", "На паузе") if muted else translate(chat_id, "Active", "Активен")
    return (
        f"{translate(chat_id, '🔎 Arbitrage Scanner', '🔎 Сканер арбитража')}\n\n"
        f"{translate(chat_id, 'Monitors Polymarket and Predict.Fun for spread inefficiencies.', 'Следит за Polymarket и Predict.Fun и ищет неэффективности спреда.')}\n\n"
        f"{status_icon} {translate(chat_id, 'Status', 'Статус')}: {status_label}\n"
        f"{translate(chat_id, 'Filters are applied to your personal alert stream.', 'Фильтры применяются только к вашему потоку алертов.')}\n\n"
        f"{translate(chat_id, 'Your filters', 'Ваши фильтры')}:\n"
        f"• 📈 {translate(chat_id, 'Min ROI', 'Мин. ROI')}: {_format_roi_value(preferences, chat_id=chat_id)}\n"
        f"• 📦 {translate(chat_id, 'Min volume', 'Мин. объём')}: {min_capital_str}\n"
        f"• 💵 {translate(chat_id, 'Max volume', 'Макс. объём')}: {max_capital_str}\n"
        f"• 💰 {translate(chat_id, 'Min profit', 'Мин. прибыль')}: {min_profit_str}\n"
        f"• ⏳ {translate(chat_id, 'Max market end', 'Макс. срок рынка')}: {_format_days(preferences.get('max_days_to_close'), chat_id=chat_id)}"
    )


def format_status_text(preferences, chat_id=None):
    muted = preferences.get("muted", False)
    status_icon = "🔴" if muted else "🟢"
    status_label = translate(chat_id, "Paused", "На паузе") if muted else translate(chat_id, "Active", "Активен")
    alerts_line = (
        translate(chat_id, "📭 Telegram alerts are paused.", "📭 Telegram-алерты поставлены на паузу.")
        if muted
        else translate(chat_id, "📬 Telegram alerts are enabled.", "📬 Telegram-алерты включены.")
    )
    return (
        f"{translate(chat_id, '📡 Arbitrage Scanner', '📡 Сканер арбитража')}\n\n"
        f"{translate(chat_id, 'Current bot status.', 'Текущий статус бота.')}\n\n"
        f"{status_icon} {translate(chat_id, 'Status', 'Статус')}: {status_label}\n"
        f"{translate(chat_id, '🔄 Monitoring is running in the background.', '🔄 Мониторинг работает в фоне.')}\n"
        f"{alerts_line}"
    )


def format_setting_prompt(field_name, preferences, chat_id=None):
    label = _field_label(field_name, chat_id=chat_id)
    current_value = _format_field_value(field_name, preferences, chat_id=chat_id)
    description = {
        "min_roi_percent": translate(chat_id, "Enter the minimum ROI percentage required to receive a signal.", "Введите минимальный ROI в процентах для получения сигнала."),
        "min_capital_usd": translate(chat_id, "Enter the minimum volume in USD required for an alert.", "Введите минимальный объём в USD для получения алерта."),
        "max_capital_usd": translate(chat_id, "Enter the maximum volume in USD allowed for an alert.", "Введите максимальный объём в USD для получения алерта."),
        "min_profit_usd": translate(chat_id, "Enter the minimum profit in USD required for an alert.", "Введите минимальную прибыль в USD для получения алерта."),
        "max_days_to_close": translate(chat_id, "Enter the maximum number of days until market expiry.", "Введите максимальное количество дней до окончания рынка."),
    }[field_name]

    return (
        f"{translate(chat_id, '⚙️ Arbitrage Scanner', '⚙️ Сканер арбитража')}\n\n"
        f"✏️ {translate(chat_id, 'Change', 'Изменить')}: {label}\n"
        f"→ {translate(chat_id, 'current value', 'текущее значение')}: {current_value}\n\n"
        f"{description}\n\n"
        f"{translate(chat_id, 'Send `off` to disable this filter.', 'Отправьте `выкл`, чтобы отключить этот фильтр.')}"
    )


def filter_reason_for_preferences(opportunity, market_a, market_b, preferences, now=None, skip_max_capital=False):
    min_roi = effective_min_roi(preferences)
    if min_roi is not None and opportunity.net_roi * 100 < float(min_roi):
        return "min_roi"

    min_capital = preferences.get("min_capital_usd")
    if min_capital is not None and opportunity.capital_required < float(min_capital):
        return "min_capital"

    max_capital = preferences.get("max_capital_usd")
    if not skip_max_capital and max_capital is not None and opportunity.capital_required > float(max_capital):
        return "max_capital"

    min_profit = preferences.get("min_profit_usd")
    if min_profit is not None and opportunity.net_profit < float(min_profit):
        return "min_profit"

    max_days = preferences.get("max_days_to_close")
    if max_days is None:
        return None

    close_at = extract_pair_close_datetime(market_a, market_b)
    if close_at is None:
        # unknown expiry — filter when max_days is active
        return "max_days_to_close"

    reference_now = now or datetime.now(timezone.utc)
    if close_at.tzinfo is None:
        close_at = close_at.replace(tzinfo=timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)

    delta_days = (close_at - reference_now).total_seconds() / 86400
    if delta_days > float(max_days):
        return "max_days_to_close"

    return None


def effective_min_roi(preferences):
    min_roi = preferences.get("min_roi_percent")
    if min_roi is None:
        return None
    return float(min_roi)


def _format_field_value(field_name, preferences, chat_id=None):
    if field_name == "min_roi_percent":
        return _format_roi_value(preferences, chat_id=chat_id)
    if field_name in {"min_capital_usd", "max_capital_usd", "min_profit_usd"}:
        val = preferences.get(field_name)
        return translate(chat_id, "off", "выкл") if val is None else _format_money(val, fallback='')
    return _format_days(preferences.get(field_name), chat_id=chat_id)


def extract_pair_close_datetime(market_a, market_b):
    datetimes = []
    for market in (market_a, market_b):
        raw_payload = getattr(market, "raw_payload_json", None) or {}
        parsed = _extract_market_close_datetime(raw_payload)
        if parsed is not None:
            datetimes.append(parsed)
    if not datetimes:
        return None
    return max(datetimes)


def _extract_market_close_datetime(raw_payload):
    for field_name in DATETIME_FIELDS:
        parsed = _parse_datetime_value(raw_payload.get(field_name))
        if parsed is not None:
            return parsed
    return None


def _parse_datetime_value(value):
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_datetime_value(int(text))

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_money(value, fallback):
    if value is None:
        return fallback
    rounded = round(float(value), 2)
    if rounded.is_integer():
        return f"${int(rounded)}"
    return f"${rounded:.2f}"


def _format_percent(value, fallback):
    if value is None:
        return fallback
    return f"{float(value):.2f}%"


def _format_days(value, chat_id=None):
    if value is None:
        return translate(chat_id, "off", "выкл")
    return translate(chat_id, f"{int(value)} days", f"{int(value)} дн.")


def _format_roi_value(preferences, chat_id=None):
    min_roi = effective_min_roi(preferences)
    if min_roi is None:
        return translate(chat_id, "off", "выкл")
    return f"{float(min_roi):.2f}%"


def _field_label(field_name, chat_id=None):
    ru_labels = {
        "min_roi_percent": "Мин. ROI",
        "min_capital_usd": "Мин. объём",
        "max_capital_usd": "Макс. объём",
        "min_profit_usd": "Мин. прибыль",
        "max_days_to_close": "Макс. срок рынка",
    }
    return translate(chat_id, FIELD_LABELS[field_name], ru_labels[field_name])


def _ui_state_key(chat_id):
    return f"{UI_STATE_KEY_PREFIX}{chat_id}"


async def toggle_mute(db_session, chat_id):
    telegram_chat = await ensure_telegram_user(db_session, chat_id)
    stmt = select(UserPreference).where(UserPreference.user_id == telegram_chat.user_id)
    result = await db_session.execute(stmt)
    preferences = result.scalars().first()

    if preferences is None:
        preferences = _make_default_preference(user_id=telegram_chat.user_id)
        preferences.muted = True
        db_session.add(preferences)
    else:
        preferences.muted = not preferences.muted
        preferences.updated_at = datetime.now(timezone.utc)

    await db_session.commit()
    return _serialize_user_preferences(preferences)