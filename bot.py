#!/usr/bin/env python3
"""
Minimal Telegram bot that replies to /start with a welcome message in Ukrainian.
Uses only the Python standard library, reads TELEGRAM_BOT_TOKEN from .env or environment,
and long polling to avoid external dependencies.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


WELCOME_TEXT = (
    "Вітаємо!" 
    "Для Вашої ідентифікації натиснить кнопку 'Поділитися телефоном'.\n"
    "Менеджер відповість Вам у найближчий час. \n"
    "Звертаємо увагу, що ми працюємо у будні дні з 09 по 18. " 
)

REQUEST_CONTACT_BUTTON = {
    "keyboard": [[{"text": "Поділитися телефоном", "request_contact": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}

KEYCRM_API_URL = "https://openapi.keycrm.app/v1/buyer"


def load_dotenv(path: str = ".env") -> None:
    """Very small .env loader; supports KEY=VALUE, ignores comments/blank lines."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key and key not in os.environ:
                os.environ[key] = value


def _get_token() -> str:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Please set the TELEGRAM_BOT_TOKEN environment variable.")
    return token


def _get_keycrm_token() -> str | None:
    # Токен KeyCRM (KEYCRM_TOKEN) потрібен, щоб перевірити, чи є номер у CRM.
    return os.environ.get("KEYCRM_TOKEN")


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _call_api(token: str, method: str, params: dict | None = None) -> dict:
    data = None
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(_api_url(token, method), data=data, method="POST")
    with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
        return json.load(response)


def _ssl_context() -> ssl.SSLContext:
    ca_bundle = os.environ.get("TELEGRAM_CA_BUNDLE")
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)

    if os.environ.get("TELEGRAM_SKIP_TLS_VERIFY") == "1":
        # В корпоративных сетях с перехватом TLS сертификат может быть самоподписанным.
        # Отключение проверки уменьшает безопасность, используйте только если доверяете сети.
        return ssl._create_unverified_context()

    return ssl.create_default_context()


def get_updates(token: str, offset: int) -> list[dict]:
    payload = {"offset": offset, "timeout": 25}
    response = _call_api(token, "getUpdates", payload)
    return response.get("result", [])


def send_message(token: str, chat_id: int, text: str) -> None:
    _call_api(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_welcome(token: str, chat_id: int) -> None:
    payload = {
        "chat_id": chat_id,
        "text": WELCOME_TEXT,
        "reply_markup": json.dumps(REQUEST_CONTACT_BUTTON),
    }
    _call_api(token, "sendMessage", payload)


def clear_webhook(token: str) -> None:
    """Remove Telegram webhook to avoid 409 conflicts when using getUpdates."""
    try:
        _call_api(token, "deleteWebhook", {"drop_pending_updates": True})
    except Exception as exc:  # pragma: no cover - best-effort cleanup
        print(f"Unable to delete webhook automatically: {exc}")


def _fetch_keycrm(phone: str) -> dict | None:
    keycrm_token = _get_keycrm_token()
    if not keycrm_token:
        return None

    params = urllib.parse.urlencode(
        {"limit": 15, "page": 1, "filter[buyer_phone]": phone}
    )
    url = f"{KEYCRM_API_URL}?{params}"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Content-type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", f"Bearer {keycrm_token}")

    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as resp:
            return json.load(resp)
    except Exception as exc:  # pragma: no cover - CRM checks are best-effort
        print(f"CRM lookup failed for {phone}: {exc}")
        return None


def _lookup_buyer_by_phone(phone: str) -> tuple[dict | None, int]:
    result = _fetch_keycrm(phone)
    if result is None:
        return None, 0
    data = result.get("data") or []
    total = result.get("total", 0)
    buyer = data[0] if data else None
    return buyer, total


def _format_crm_message(buyer: dict | None, total: int) -> str:
    if buyer is None:
        if _get_keycrm_token():
            return "Цей номер у CRM не знайдено."
        return "Перевірка номера в CRM недоступна зараз."

    name = buyer.get("full_name") or buyer.get("name") or "Без імені"
    suffix = f" Всього збігів: {total}." if total else ""
    return f"Номер вже є в CRM як: {name}.{suffix}"


def _update_buyer_note(buyer: dict, message: str) -> None:
    keycrm_token = _get_keycrm_token()
    if not keycrm_token:
        return

    buyer_id = buyer.get("id")
    if not buyer_id:
        return

    full_name = buyer.get("full_name") or buyer.get("name") or "Без імені"
    existing_note = buyer.get("note") or ""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    new_note = (existing_note + f"\n[{timestamp}] Bot: {message}").strip()

    payload = json.dumps({"full_name": full_name, "note": new_note}).encode("utf-8")
    url = f"{KEYCRM_API_URL}/{buyer_id}"
    request = urllib.request.Request(url, data=payload, method="PUT")
    request.add_header("Content-type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", f"Bearer {keycrm_token}")

    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as resp:
            json.load(resp)
    except Exception as exc:  # pragma: no cover - логування у CRM не критичне
        print(f"Не вдалося оновити примітку в CRM для buyer_id={buyer_id}: {exc}")


def main() -> None:
    token = _get_token()
    offset = 0

    # Якщо бот був підключений як вебхук у CRM, видаляємо його, щоб уникнути 409 Conflict.
    clear_webhook(token)

    print("Bot is running. Press Ctrl+C to stop.")

    while True:
        try:
            updates = get_updates(token, offset)
            for update in updates:
                offset = update.get("update_id", offset) + 1
                message = update.get("message") or {}
                text = message.get("text") or ""
                chat_id = message.get("chat", {}).get("id")

                if text.startswith("/start") and chat_id:
                    send_welcome(token, chat_id)
                elif message.get("contact") and chat_id:
                    contact = message["contact"]
                    phone = contact.get("phone_number", "невідомий номер")
                    buyer, total = _lookup_buyer_by_phone(phone)
                    response_text = (
                        f"Дякуємо! Ми отримали ваш номер: {phone}.\n"
                        f"{_format_crm_message(buyer, total)}"
                    )
                    _call_api(token, "sendMessage", {"chat_id": chat_id, "text": response_text})
                    if buyer:
                        _update_buyer_note(buyer, response_text)
        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            break
        except urllib.error.URLError as exc:
            print(f"Network error: {exc}. Retrying in 3 seconds...")
            time.sleep(3)
        except urllib.error.HTTPError as exc:
            print(f"HTTP error: {exc}. Retrying in 3 seconds...")
            time.sleep(3)
        except Exception as exc:  # pragma: no cover - safety net for unexpected errors
            print(f"Unexpected error: {exc}. Retrying in 3 seconds...")
            time.sleep(3)


if __name__ == "__main__":
    main()
