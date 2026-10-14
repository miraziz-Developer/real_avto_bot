"""Kanal postidagi har bir video alohida tinglanadi: xatoda qayta urinish va adminga «nechtasi tinglandi»."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from aiogram.types import Chat, Message, VideoNote

from bot.ai.errors import AIError
from bot.handlers import channel_watch as watch


def _vn(mid: int) -> Message:
    return Message(
        message_id=mid,
        date=datetime.now(UTC),
        chat=Chat(id=-100777, type="channel"),
        video_note=VideoNote(file_id=f"v{mid}", file_unique_id=f"u{mid}", length=240, duration=30),
    )


class FakeBot:
    async def get_file(self, file_id):
        class F:
            file_path = file_id

        return F()

    async def download_file(self, path):
        return io.BytesIO(path.encode())


class FlakyAI:
    enabled = True

    def __init__(self):
        self.calls: list[str] = []

    async def transcribe(self, data, *, filename, mime_type):
        name = data.decode()
        self.calls.append(name)
        if name == "v1" and self.calls.count("v1") == 1:
            raise AIError("vaqtinchalik xato")  # birinchi urinish — xato, ikkinchisi o'tadi
        return {"v1": "Damas 2022, 76 ming yurgan", "v2": "", "v3": "metan bor"}[name]


async def test_every_video_is_listened_with_retry_and_summary(monkeypatch):
    ai = FlakyAI()
    monkeypatch.setattr(watch, "get_ai", lambda: ai)
    monkeypatch.setattr(watch.asyncio, "sleep", _no_sleep)
    watch._transcript_cache.clear()
    msgs = [_vn(1), _vn(2), _vn(3)]
    heard = await watch._transcribe_media(FakeBot(), msgs)
    assert heard == ["Damas 2022, 76 ming yurgan", "metan bor"]
    assert ai.calls.count("v1") == 2  # qayta urinildi
    summary = watch._listen_summary(msgs)
    assert "3 ta videodan 2 tasi tinglandi" in summary and "2-video: nutq yo'q" in summary
    html_ = watch._heard_html(heard)
    assert "1)" in html_ and "2)" in html_ and "metan bor" in html_  # birinchisi bilan cheklanmaydi


async def _no_sleep(_s):
    return None
