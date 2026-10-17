"""Gemini mijozi, provayder tanlovi va AI xarajat chegarasi (tarmoqsiz — HTTP so'rovlar soxtalashtiriladi)."""

from __future__ import annotations

import base64

import pytest

from bot.ai import resolve_provider
from bot.ai.budget import AIBudget, Prices
from bot.ai.errors import AIBudgetExceeded, AIError
from bot.ai.gemini import INLINE_MAX_BYTES, GeminiClient, guess_mime, parse_media_answer


# --- javobni o'qish ---------------------------------------------------------------------------
def test_parse_media_answer_speech_and_visual():
    out = parse_media_answer("NUTQ: Kobalt 2020 yil, yurgani 98 ming\nKO'RINISH: oq Chevrolet Cobalt, spidometrda 98 000 km")
    assert out == "Kobalt 2020 yil, yurgani 98 ming\n[Videoda ko'rinadi]: oq Chevrolet Cobalt, spidometrda 98 000 km"


def test_parse_media_answer_dashes_mean_nothing():
    assert parse_media_answer("NUTQ: -\nKO'RINISH: -") == ""
    assert parse_media_answer("NUTQ: narxi qancha?\nKO‘RINISH: -") == "narxi qancha?"
    assert parse_media_answer("NUTQ: -\nKORINISH: qizil Spark") == "[Videoda ko'rinadi]: qizil Spark"


def test_parse_media_answer_free_text_fallback():
    assert parse_media_answer("Gentra 2019 sotiladi") == "Gentra 2019 sotiladi"


def test_guess_mime():
    assert guess_mime("audio.ogg") == "audio/ogg"
    assert guess_mime("video.mp4") == "video/mp4"
    assert guess_mime("x.bin", "video/quicktime") == "video/quicktime"


# --- provayder tanlovi ------------------------------------------------------------------------
def test_resolve_provider():
    assert resolve_provider("auto", gemini_key="g", groq_key="q") == "gemini"
    assert resolve_provider("auto", gemini_key="", groq_key="q") == "groq"
    assert resolve_provider("groq", gemini_key="g", groq_key="q") == "groq"
    assert resolve_provider("GEMINI", gemini_key="", groq_key="q") == "gemini"
    assert resolve_provider("nimadir", gemini_key="", groq_key="") == "groq"


# --- budget -----------------------------------------------------------------------------------
def test_prices_cost_splits_audio():
    p = Prices(input_per_m=0.25, output_per_m=1.5, audio_per_m=0.5)
    # 1000 kirish (shundan 400 audio) + 200 chiqish
    assert p.cost(input_tokens=1000, output_tokens=200, audio_tokens=400) == pytest.approx(
        (600 * 0.25 + 400 * 0.5 + 200 * 1.5) / 1_000_000
    )


async def test_budget_blocks_after_daily_cap_and_alerts_once():
    alerts: list[str] = []

    async def alert(text: str) -> None:
        alerts.append(text)

    b = AIBudget(daily_usd=0.01, user_daily_limit=0)
    b.set_alert(alert)
    await b.ensure_available()
    await b.add_cost(0.006)
    await b.ensure_available()
    await b.add_cost(0.006)
    with pytest.raises(AIBudgetExceeded):
        await b.ensure_available()
    await b.add_cost(0.001)
    assert len(alerts) == 1 and "AI_DAILY_BUDGET_USD" in alerts[0]
    assert isinstance(AIBudgetExceeded("x"), AIError)  # chaqiruvchilar AIError ni ushlaydi


async def test_budget_zero_means_unlimited():
    b = AIBudget(daily_usd=0, user_daily_limit=0)
    await b.add_cost(100)
    await b.ensure_available()
    assert all([await b.allow_user("u") for _ in range(100)])


async def test_budget_user_limit_per_key():
    b = AIBudget(daily_usd=0, user_daily_limit=3)
    assert [await b.allow_user("lead:1") for _ in range(4)] == [True, True, True, False]
    assert await b.allow_user("lead:2") is True


# --- Gemini HTTP so'rovlari ------------------------------------------------------------------
class _Recorder:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    async def __call__(self, method, url, *, headers=None, **kwargs):
        self.calls.append((method, url, kwargs.get("json") or {}))
        return self.responses.pop(0)


def _client(rec: _Recorder, budget: AIBudget | None = None, **kw) -> GeminiClient:
    c = GeminiClient("test-key", model="gemini-3.1-flash-lite", budget=budget, **kw)
    c._request = rec  # type: ignore[method-assign]
    return c


async def test_analyze_video_inline_sees_and_hears_and_charges():
    rec = _Recorder(
        [
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "o'ylayapman...", "thought": True},
                                {"text": "NUTQ: Nexia 3 2018, narxi 8500\nKO'RINISH: kumushrang Nexia 3"},
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 5000,
                    "candidatesTokenCount": 40,
                    "thoughtsTokenCount": 60,
                    "promptTokensDetails": [
                        {"modality": "VIDEO", "tokenCount": 3500},
                        {"modality": "AUDIO", "tokenCount": 1400},
                        {"modality": "TEXT", "tokenCount": 100},
                    ],
                },
            }
        ]
    )
    budget = AIBudget(daily_usd=1.0, user_daily_limit=0)
    c = _client(rec, budget)
    out = await c.transcribe(b"\x00video-bytes", filename="video.mp4")
    assert out == "Nexia 3 2018, narxi 8500\n[Videoda ko'rinadi]: kumushrang Nexia 3"

    method, url, body = rec.calls[0]
    assert method == "POST" and url.endswith("/models/gemini-3.1-flash-lite:generateContent")
    media = body["contents"][0]["parts"][0]["inline_data"]
    assert media["mime_type"] == "video/mp4"
    assert base64.b64decode(media["data"]) == b"\x00video-bytes"
    cfg = body["generationConfig"]
    assert cfg["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert cfg["thinkingConfig"] == {"thinkingLevel": "low"}
    expected = (3600 * 0.25 + 1400 * 0.50 + 100 * 1.50) / 1_000_000
    assert await budget.spent_today() == pytest.approx(expected)


async def test_analyze_voice_has_no_media_resolution():
    rec = _Recorder([{"candidates": [{"content": {"parts": [{"text": "NUTQ: assalomu alaykum\nKO'RINISH: -"}]}}]}])
    c = _client(rec)
    assert await c.transcribe(b"ogg", filename="audio.ogg") == "assalomu alaykum"
    cfg = rec.calls[0][2]["generationConfig"]
    assert "mediaResolution" not in cfg
    assert rec.calls[0][2]["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "audio/ogg"


async def test_large_video_goes_through_files_api(monkeypatch):
    rec = _Recorder([{"candidates": [{"content": {"parts": [{"text": "NUTQ: -\nKO'RINISH: oq Malibu"}]}}]}])
    c = _client(rec)
    uploaded: list[int] = []
    deleted: list[str] = []

    async def fake_upload(data, mime):
        uploaded.append(len(data))
        return "https://files/abc", "files/abc"

    async def fake_delete(name):
        deleted.append(name)

    monkeypatch.setattr(c, "_upload_file", fake_upload)
    monkeypatch.setattr(c, "_delete_file", fake_delete)
    out = await c.transcribe(b"x" * (INLINE_MAX_BYTES + 1), filename="video.mp4")
    assert out == "[Videoda ko'rinadi]: oq Malibu"
    assert uploaded == [INLINE_MAX_BYTES + 1] and deleted == ["files/abc"]
    part = rec.calls[0][2]["contents"][0]["parts"][0]
    assert part == {"file_data": {"mime_type": "video/mp4", "file_uri": "https://files/abc"}}


async def test_blocked_or_empty_answer_returns_empty_string():
    rec = _Recorder([{"promptFeedback": {"blockReason": "SAFETY"}}])
    assert await _client(rec).transcribe(b"ogg", filename="audio.ogg") == ""


async def test_chat_uses_openai_endpoint_with_tools_and_reasoning():
    tool_calls = [{"id": "c1", "type": "function", "function": {"name": "search_cars", "arguments": "{}"}}]
    rec = _Recorder(
        [{"choices": [{"message": {"content": None, "tool_calls": tool_calls}}], "usage": {"prompt_tokens": 900, "completion_tokens": 30}}]
    )
    budget = AIBudget(daily_usd=1.0, user_daily_limit=0)
    c = _client(rec, budget, agent_model="gemini-agent")
    resp = await c.chat([{"role": "user", "content": "Cobalt bormi?"}], tools=[{"type": "function"}], max_tokens=700)
    assert resp == {"content": None, "tool_calls": tool_calls}
    _, url, body = rec.calls[0]
    assert url.endswith("/openai/chat/completions")
    assert body["model"] == "gemini-agent" and body["reasoning_effort"] == "low"
    assert body["tool_choice"] == "auto" and body["max_tokens"] > 700
    assert await budget.spent_today() == pytest.approx((900 * 0.25 + 30 * 1.5) / 1_000_000)


async def test_chat_json_strips_code_fence():
    rec = _Recorder([{"choices": [{"message": {"content": '```json\n{"brand": "Chevrolet"}\n```'}}]}])
    assert await _client(rec).chat_json("sys", "user") == {"brand": "Chevrolet"}
    assert rec.calls[0][2]["response_format"] == {"type": "json_object"}


async def test_no_request_when_budget_exhausted():
    rec = _Recorder([])
    budget = AIBudget(daily_usd=0.001, user_daily_limit=0)
    await budget.add_cost(0.002)
    c = _client(rec, budget)
    with pytest.raises(AIBudgetExceeded):
        await c.transcribe(b"ogg", filename="audio.ogg")
    with pytest.raises(AIBudgetExceeded):
        await c.chat([{"role": "user", "content": "x"}])
    assert rec.calls == []


async def test_disabled_without_key():
    c = GeminiClient("", model="m")
    assert c.enabled is False
    with pytest.raises(AIError):
        await c._request("POST", "https://example.invalid")


def test_parse_media_answer_tolerates_markdown_bold():
    assert parse_media_answer("**NUTQ:** Damas 2015\n**KO‘RINISH:** -") == "Damas 2015"


async def test_auth_header_per_endpoint(monkeypatch):
    seen: list[tuple[str, dict]] = []

    class _Resp:
        status = 200

        async def text(self):
            return "{}"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    class _Session:
        closed = False

        def request(self, method, url, headers=None, **kw):
            seen.append((url, headers))
            return _Resp()

    c = GeminiClient("KEY", model="m")
    monkeypatch.setattr(c, "_http", lambda: _Session())
    await c._request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
    await c._request("POST", "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent")
    assert seen[0][1] == {"Authorization": "Bearer KEY"}
    assert seen[1][1] == {"x-goog-api-key": "KEY"}
