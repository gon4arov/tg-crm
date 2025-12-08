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


def main() -> None:
    token = _get_token()
    offset = 0

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
                    _call_api(
                        token,
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": f"Дякуємо! Ми отримали ваш номер: {phone}",
                        },
                    )
        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            break
        except urllib.error.URLError as exc:
            print(f"Network error: {exc}. Retrying in 3 seconds...")
            time.sleep(3)
        except Exception as exc:  # pragma: no cover - safety net for unexpected errors
            print(f"Unexpected error: {exc}. Retrying in 3 seconds...")
            time.sleep(3)


if __name__ == "__main__":
    main()
