from arbitrage_bot.core.config import settings


def is_kurilov_chat(chat_id):
    configured_chat_id = str(getattr(settings, "ANDREI_KURILOV_ID", "") or "").strip()
    if not configured_chat_id or chat_id is None:
        return False
    return str(chat_id) == configured_chat_id


def translate(chat_id, en_text, ru_text):
    if is_kurilov_chat(chat_id):
        return ru_text
    return en_text