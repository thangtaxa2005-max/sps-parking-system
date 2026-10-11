"""Minimal Telegram bot for resident self-service.

Run with ``SPS_TELEGRAM_BOT_TOKEN`` set. The bot uses api_core for all
database and permission decisions; it never accepts commands from unlinked
chats.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

from backend import api_core
from backend.cache import CacheState
from backend.config import DB_PATH
from backend.db import connect_database, initialize_schema

logger = logging.getLogger(__name__)


def format_vehicle_list(rows: list[Any]) -> str:
    """Show each vehicle once while retaining all its RFID credentials."""
    if not rows:
        return "Căn hộ chưa có xe."
    vehicles: dict[int, list[Any]] = {}
    for row in rows:
        vehicles.setdefault(int(row["id"]), []).append(row)
    lines: list[str] = []
    for credentials in vehicles.values():
        vehicle = credentials[0]
        location = "trong bãi" if vehicle["in_lot"] else "ngoài bãi"
        lines.append(f"{vehicle['plate']} — {location}")
        for credential in credentials:
            if credential["credential_id"] is not None:
                lines.append(f"  • {credential['value']}: {credential['status']}")
        if all(item["credential_id"] is None for item in credentials):
            lines.append("  • Chưa cấp thẻ")
    return "\n".join(lines)


def format_event_history(rows: list[Any]) -> str:
    """Render recent gate events with human-readable local timestamps."""
    if not rows:
        return "Chưa có lịch sử."
    lines = []
    for row in rows:
        raw_timestamp = str(row["ts"])
        try:
            timestamp = datetime.fromisoformat(raw_timestamp).strftime("%d/%m/%Y %H:%M:%S")
        except ValueError:
            timestamp = raw_timestamp
        gate = "Vào" if row["gate"] == "in" else "Ra"
        result = "cho phép" if row["result"] == "allow" else "từ chối"
        lines.append(f"{timestamp} — {row['plate']} — {gate}: {result} ({row['reason_code']})")
    return "\n".join(lines)


class TelegramBot:
    """Long-polling Telegram client with resident-only commands."""

    def __init__(self, token: str, connection: Any, cache: CacheState) -> None:
        self.token = token
        self.connection = connection
        self.cache = cache
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    async def call(self, session: aiohttp.ClientSession, method: str, **payload: Any) -> Any:
        async with session.post(f"{self.base_url}/{method}", json=payload, timeout=35) as response:
            response.raise_for_status()
            body = await response.json()
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "Telegram API error"))
        return body.get("result")

    async def send(self, session: aiohttp.ClientSession, chat_id: str, text: str) -> None:
        await self.call(session, "sendMessage", chat_id=chat_id, text=text)

    async def send_notification(
        self, session: aiohttp.ClientSession, chat_id: str, text: str, image_path: str | None
    ) -> None:
        """Send notification text and attach a local image when it is available."""
        if image_path:
            project_root = Path(__file__).resolve().parent.parent
            target = (project_root / image_path).resolve()
            try:
                target.relative_to(project_root / "data" / "images")
            except ValueError:
                target = None
            if target is not None and target.is_file():
                form = aiohttp.FormData()
                form.add_field("chat_id", chat_id)
                form.add_field("caption", text)
                with target.open("rb") as image:
                    form.add_field("photo", image, filename=target.name, content_type="image/jpeg")
                    await self.call_form(session, "sendPhoto", form)
                return
        await self.send(session, chat_id, text)

    async def call_form(self, session: aiohttp.ClientSession, method: str, form: aiohttp.FormData) -> Any:
        """Call a Telegram method with multipart form data."""
        async with session.post(f"{self.base_url}/{method}", data=form, timeout=35) as response:
            response.raise_for_status()
            body = await response.json()
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "Telegram API error"))
        return body.get("result")

    def reply_text(self, chat_id: str, text: str) -> str:
        user = api_core.get_user_by_telegram_chat(self.connection, chat_id)
        if user is None:
            return "Tài khoản chưa liên kết. Gửi /link MÃ_LIÊN_KẾT để bắt đầu."
        return text

    async def handle(self, session: aiohttp.ClientSession, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = str(message.get("text", "")).strip()
        if not chat_id or not text:
            return
        parts = text.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        try:
            if command == "/start":
                await self.send(session, chat_id, "SPS. Gửi /link MÃ để liên kết, sau đó dùng /vehicles, /lock UID, /unlock UID hoặc /history.")
            elif command == "/link":
                if not argument:
                    await self.send(session, chat_id, "Cú pháp: /link MÃ_LIÊN_KẾT")
                else:
                    api_core.link_telegram_chat(self.connection, argument, chat_id)
                    await self.send(session, chat_id, "Đã liên kết Telegram thành công.")
            elif command == "/vehicles":
                user = api_core.get_user_by_telegram_chat(self.connection, chat_id)
                if user is None:
                    await self.send(session, chat_id, self.reply_text(chat_id, "")); return
                rows = api_core.list_user_vehicles(self.connection, int(user["id"]))
                await self.send(session, chat_id, format_vehicle_list(rows))
            elif command == "/register":
                user = api_core.get_user_by_telegram_chat(self.connection, chat_id)
                if user is None:
                    await self.send(session, chat_id, self.reply_text(chat_id, "")); return
                fields = argument.split()
                if len(fields) != 3 or fields[1] not in ("car", "moto") or fields[2] not in ("month", "year"):
                    await self.send(session, chat_id, "Cú pháp: /register BIỂN_SỐ car|moto month|year"); return
                request_id = api_core.create_registration_request(
                    self.connection, self.cache, unit_id=int(user["unit_id"]),
                    plate=fields[0], vehicle_type=fields[1], plan_type=fields[2],
                )
                await self.send(session, chat_id, f"Đã gửi yêu cầu đăng ký số {request_id}, chờ quản lý duyệt.")
            elif command in ("/lock", "/unlock"):
                user = api_core.get_user_by_telegram_chat(self.connection, chat_id)
                if user is None:
                    await self.send(session, chat_id, self.reply_text(chat_id, "")); return
                if not argument:
                    await self.send(session, chat_id, f"Cú pháp: {command} UID"); return
                rows = api_core.list_user_vehicles(self.connection, int(user["id"]))
                allowed = {str(row["value"]) for row in rows if row["value"]}
                if argument not in allowed:
                    await self.send(session, chat_id, "Thẻ không thuộc căn hộ của bạn."); return
                if command == "/lock":
                    api_core.lock_credential(self.connection, self.cache, argument)
                else:
                    api_core.unlock_credential(self.connection, self.cache, argument)
                await self.send(session, chat_id, "Đã cập nhật trạng thái thẻ.")
            elif command == "/history":
                user = api_core.get_user_by_telegram_chat(self.connection, chat_id)
                if user is None:
                    await self.send(session, chat_id, self.reply_text(chat_id, "")); return
                rows = api_core.list_user_events(self.connection, int(user["id"]), limit=10)
                await self.send(session, chat_id, format_event_history(rows))
        except ValueError as error:
            await self.send(session, chat_id, str(error))
        except Exception:
            logger.exception("Telegram update failed")
            await self.send(session, chat_id, "Có lỗi tạm thời, vui lòng thử lại.")

    async def run(self) -> None:
        """Poll updates until cancelled."""
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            notification_task = asyncio.create_task(self._notification_loop(session))
            try:
                while True:
                    try:
                        updates = await self.call(session, "getUpdates", offset=self.offset, timeout=30)
                    except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
                        logger.exception("Telegram polling failed; retrying")
                        await asyncio.sleep(5)
                        continue
                    for update in updates or []:
                        self.offset = int(update["update_id"]) + 1
                        await self.handle(session, update)
            finally:
                notification_task.cancel()
                await asyncio.gather(notification_task, return_exceptions=True)

    async def _notification_loop(self, session: aiohttp.ClientSession) -> None:
        """Deliver queued notifications independently of incoming messages."""
        while True:
            await self.deliver_notifications(session)
            await asyncio.sleep(5)

    async def deliver_notifications(self, session: aiohttp.ClientSession) -> None:
        """Deliver queued alert/info notifications and mark successful sends."""
        rows = api_core.list_pending_notifications(self.connection)
        for row in rows:
            try:
                await self.send_notification(
                    session, str(row["telegram_chat_id"]), str(row["content"]), row["image_path"]
                )
                api_core.mark_notification_sent(self.connection, int(row["id"]))
            except Exception:
                logger.exception("Failed to deliver notification id=%s", row["id"])


async def main() -> None:
    token = os.environ.get("SPS_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("SPS_TELEGRAM_BOT_TOKEN is required")
    connection = connect_database(DB_PATH)
    initialize_schema(connection)
    cache = CacheState(connection)
    try:
        await TelegramBot(token, connection, cache).run()
    finally:
        connection.close()


if __name__ == "__main__":
    asyncio.run(main())
