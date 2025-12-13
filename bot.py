#!/usr/bin/env python3
"""
Minimal Telegram bot that replies to /start with a welcome message in Ukrainian.
Uses only the Python standard library, reads TELEGRAM_BOT_TOKEN from .env or environment,
and long polling to avoid external dependencies.
"""

import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


WELCOME_TEXT = "Надішліть номер телефона чи емейл для ідентифікації клієнта."

KEYCRM_API_URL = "https://openapi.keycrm.app/v1/buyer"


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("crm_bot")

# Таймауты на сетевые запросы (секунды), можно переопределить через переменные окружения.
TELEGRAM_TIMEOUT = float(os.environ.get("TELEGRAM_TIMEOUT_SECONDS", "8"))
KEYCRM_TIMEOUT = float(os.environ.get("KEYCRM_TIMEOUT_SECONDS", "8"))


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


def _allowed_chat_ids() -> set[int]:
    raw = os.environ.get("ALLOWED_CHAT_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


def _get_keycrm_token() -> str | None:
    # Токен KeyCRM (KEYCRM_TOKEN) потрібен, щоб перевірити, чи є номер у CRM.
    return os.environ.get("KEYCRM_TOKEN")


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _call_api(token: str, method: str, params: dict | None = None) -> dict:
    logger.info("Telegram call: %s", method)
    data = None
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(_api_url(token, method), data=data, method="POST")
    started_at = time.perf_counter()
    try:
        with urllib.request.urlopen(
            request, timeout=TELEGRAM_TIMEOUT, context=_ssl_context()
        ) as response:
            parsed = json.load(response)
            duration = time.perf_counter() - started_at
            logger.info("Telegram response: ok=%s in %.3fs", parsed.get("ok"), duration)
            return parsed
    except Exception as exc:
        duration = time.perf_counter() - started_at
        logger.exception("Telegram request failed for %s in %.3fs: %s", method, duration, exc)
        raise


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
    }
    _call_api(token, "sendMessage", payload)


def clear_webhook(token: str) -> None:
    """Remove Telegram webhook to avoid 409 conflicts when using getUpdates."""
    try:
        _call_api(token, "deleteWebhook", {"drop_pending_updates": True})
    except Exception as exc:  # pragma: no cover - best-effort cleanup
        print(f"Unable to delete webhook automatically: {exc}")


def _fetch_keycrm(filter_field: str, value: str) -> dict | None:
    keycrm_token = _get_keycrm_token()
    if not keycrm_token:
        return None

    params = urllib.parse.urlencode(
        {
            "limit": 15,
            "page": 1,
            "include": "manager,company,shipping,custom_fields",
            f"filter[{filter_field}]": value,
        }
    )
    url = f"{KEYCRM_API_URL}?{params}"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Content-type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", f"Bearer {keycrm_token}")

    logger.info("KeyCRM request: filter[%s]=%s", filter_field, value)
    started_at = time.perf_counter()

    try:
        with urllib.request.urlopen(request, timeout=KEYCRM_TIMEOUT, context=_ssl_context()) as resp:
            parsed = json.load(resp)
            duration = time.perf_counter() - started_at
            logger.info(
                "KeyCRM response: total=%s count=%s in %.3fs",
                parsed.get("total"),
                len(parsed.get("data") or []),
                duration,
            )
            return parsed
    except Exception as exc:  # pragma: no cover - CRM checks are best-effort
        duration = time.perf_counter() - started_at
        logger.warning("CRM lookup failed for value=%s in %.3fs: %s", value, duration, exc)
        return None


def _lookup_buyers(filter_field: str, value: str) -> tuple[list[dict], int]:
    result = _fetch_keycrm(filter_field, value)
    if result is None:
        return [], 0
    buyers = result.get("data") or []
    total = result.get("total", 0)
    return buyers, total


def _format_crm_message(buyers: list[dict], total: int) -> str:
    if not buyers:
        if _get_keycrm_token():
            return "Номер не знайдено в системі."
        return "Перевірка номера в CRM недоступна зараз."

    def _join(values: list[str] | None) -> str:
        return ", ".join([v for v in values or [] if v])

    def _format_shipping(shipping: list[dict] | None) -> list[str]:
        lines: list[str] = []
        for item in (shipping or [])[:3]:
            parts = [
                item.get("address"),
                item.get("additional_address"),
                item.get("city"),
                item.get("region"),
                item.get("zip_code"),
                item.get("country"),
            ]
            packed = ", ".join([p for p in parts if p])
            if packed:
                lines.append(f"Адреса: {packed}")
        return lines

    def _format_custom_fields(custom_fields: list[dict] | None) -> list[str]:
        lines: list[str] = []
        for cf in custom_fields or []:
            uuid = cf.get("uuid") or "поле"
            value = cf.get("value")
            if value:
                if isinstance(value, list):
                    value = ", ".join(map(str, value))
                lines.append(f"{uuid}: {value}")
        return lines[:5]

    matches = f"{total} збіг" if total == 1 else f"{total} збігів"
    lines = [f"Знайдено {matches}:"]

    for idx, buyer in enumerate(buyers[:5], start=1):  # обмежуємо перші 5
        name = buyer.get("full_name") or buyer.get("name") or "Без імені"
        manager = buyer.get("manager") or {}
        manager_name = (
            manager.get("name") or manager.get("full_name") or "не призначений"
        )

        block: list[str] = [
            f"{idx}. Клієнт: {name}",
            f"   Менеджер: {manager_name}",
        ]

        emails = _join(buyer.get("email"))
        if emails:
            block.append(f"   E-mail: {emails}")

        phones = _join(buyer.get("phone"))
        if phones:
            block.append(f"   Телефон(и): {phones}")

        birthday = buyer.get("birthday")
        if birthday:
            block.append(f"   Дата народження: {birthday}")

        company = buyer.get("company") or {}
        company_name = company.get("name")
        if company_name:
            block.append(f"   Компанія: {company_name}")

        for addr in _format_shipping(buyer.get("shipping")):
            block.append(f"   {addr}")

        cf_lines = _format_custom_fields(buyer.get("custom_fields"))
        if cf_lines:
            block.append("   Кастомні поля:")
            block.extend([f"   - {line}" for line in cf_lines])

        lines.extend(block)

    if total > len(buyers):
        lines.append("Показані перші записи.")

    return "\n".join(lines)


def _update_buyer_note_for_first(buyers: list[dict], message: str) -> None:
    keycrm_token = _get_keycrm_token()
    if not keycrm_token or not buyers:
        return

    buyer = buyers[0]
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


def _normalize_phone(raw: str) -> str | None:
    """Return phone in format +380XXXXXXXXX or None if cannot normalize."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("380"):
        return f"+{digits}"
    if len(digits) == 10 and digits.startswith("0"):
        return f"+380{digits[1:]}"
    if len(digits) == 9:  # e.g., 991234567
        return f"+380{digits}"
    if len(digits) == 11 and digits.startswith("80"):
        return f"+3{digits}"
    return None


def _normalize_email(raw: str) -> str | None:
    candidate = (raw or "").strip()
    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", candidate):
        return candidate.lower()
    return None


def _lookup_phone_with_fallbacks(phone: str) -> tuple[list[dict], int]:
    """Try to find buyers by phone with and without leading plus."""
    variants: list[str] = []
    if phone:
        variants.append(phone)
        plain = phone.lstrip("+")
        if plain != phone:
            variants.append(plain)
        if not phone.startswith("+") and phone.startswith("380"):
            variants.append(f"+{phone}")

    for variant in variants:
        buyers, total = _lookup_buyers("buyer_phone", variant)
        if total:
            return buyers, total
    return [], 0


def main() -> None:
    token = _get_token()
    offset = 0
    allowed_ids = _allowed_chat_ids()

    # Якщо бот був підключений як вебхук у CRM, видаляємо його, щоб уникнути 409 Conflict.
    clear_webhook(token)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    while True:
        try:
            updates = get_updates(token, offset)
            for update in updates:
                offset = update.get("update_id", offset) + 1
                message = update.get("message") or {}
                text = message.get("text") or ""
                chat_id = message.get("chat", {}).get("id")

                logger.info("Update chat_id=%s text=%s", chat_id, text)

                if allowed_ids and chat_id and chat_id not in allowed_ids:
                    _call_api(
                        token,
                        "sendMessage",
                        {"chat_id": chat_id, "text": "Доступ обмежено для цього бота."},
                    )
                    continue

                if text.startswith("/start") and chat_id:
                    send_welcome(token, chat_id)
                elif chat_id and text:
                    normalized = _normalize_phone(text)
                    if normalized:
                        buyers, total = _lookup_phone_with_fallbacks(normalized)
                        response_text = (
                            f"Дякуємо! Ми отримали ваш номер: {normalized}.\n"
                            f"{_format_crm_message(buyers, total)}"
                        )
                        _call_api(token, "sendMessage", {"chat_id": chat_id, "text": response_text})
                        if buyers:
                            _update_buyer_note_for_first(buyers, response_text)
                        continue

                    email = _normalize_email(text)
                    if email:
                        buyers, total = _lookup_buyers("buyer_email", email)
                        response_text = (
                            f"Дякуємо! Ми отримали ваш e-mail: {email}.\n"
                            f"{_format_crm_message(buyers, total)}"
                        )
                        _call_api(token, "sendMessage", {"chat_id": chat_id, "text": response_text})
                        if buyers:
                            _update_buyer_note_for_first(buyers, response_text)
                        continue

                    else:
                        _call_api(
                            token,
                            "sendMessage",
                            {
                                "chat_id": chat_id,
                                "text": "Будь ласка, введіть номер у форматі +380XXXXXXXXX, e-mail або поділіться контактом кнопкою.",
                            },
                        )
        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            break
        except urllib.error.URLError as exc:
            logger.warning("Network error: %s. Retrying in 3 seconds...", exc)
            time.sleep(3)
        except urllib.error.HTTPError as exc:
            logger.warning("HTTP error: %s. Retrying in 3 seconds...", exc)
            time.sleep(3)
        except Exception as exc:  # pragma: no cover - safety net for unexpected errors
            logger.exception("Unexpected error: %s. Retrying in 3 seconds...", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
