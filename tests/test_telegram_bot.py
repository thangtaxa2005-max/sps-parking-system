import asyncio
import sqlite3

from backend import api_core
from backend.cache import CacheState
from backend.db import initialize_schema
from backend.telegram_bot import TelegramBot


class FakeSession:
    def __init__(self):
        self.messages = []


def _bot():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    unit_id = connection.execute("INSERT INTO units (unit_code) VALUES ('BOT-UNIT')").lastrowid
    cache = CacheState(connection)
    user_id = api_core.create_resident_user(
        connection, cache, unit_id=int(unit_id), username="bot-resident", password="secret1"
    )
    code = api_core.create_telegram_link_code(connection, user_id)
    api_core.link_telegram_chat(connection, code, "chat-1")
    return TelegramBot("test-token", connection, cache), connection


async def _send(session, chat_id, text):
    session.messages.append((str(chat_id), text))


def test_bot_register_and_vehicles(monkeypatch):
    bot, connection = _bot()
    session = FakeSession()
    monkeypatch.setattr(bot, "send", _send)
    asyncio.run(bot.handle(session, {"message": {"chat": {"id": "chat-1"}, "text": "/register 29A12345 car month"}}))
    request = connection.execute("SELECT plate, vehicle_type, unit_id FROM registration_requests").fetchone()
    assert request["plate"] == "29A12345"
    assert request["vehicle_type"] == "car"
    assert session.messages[-1][0] == "chat-1"


def test_bot_rejects_unlinked_command(monkeypatch):
    bot, _ = _bot()
    session = FakeSession()
    monkeypatch.setattr(bot, "send", _send)
    asyncio.run(bot.handle(session, {"message": {"chat": {"id": "unknown"}, "text": "/vehicles"}}))
    assert "chưa liên kết" in session.messages[-1][1]
