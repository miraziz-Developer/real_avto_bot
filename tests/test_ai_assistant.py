"""AI maslahatchi: tool-calling aylanishi, lead saqlash va xatolarga chidamlilik (soxta LLM bilan)."""

import json
from typing import Any

from bot.services.ai.assistant import (
    CarSearch,
    ClientContext,
    LeadInput,
    SalesAssistant,
    parse_lead_args,
    parse_search_args,
)
from bot.services.ai.llm import LlmError

CLIENT = ClientContext(platform="telegram", social_id="42", name="Sardor", username="sardor")
MALIBU = {"listing_id": 7, "car": "Chevrolet Malibu 2", "year": 2019, "price_usd": 17500, "url": "https://t.me/x/7"}


class FakeLlm:
    def __init__(self, script: list[dict[str, Any] | Exception]) -> None:
        self.script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, messages, tools):
        self.calls.append(list(messages))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeCatalog:
    def __init__(self) -> None:
        self.searches: list[CarSearch] = []

    async def search(self, params: CarSearch):
        self.searches.append(params)
        return [MALIBU] if "malibu" in (params.query or "").lower() else []

    async def get(self, listing_id: int):
        return MALIBU if listing_id == 7 else None


class FakeLeads:
    def __init__(self) -> None:
        self.saved: list[tuple[ClientContext, LeadInput]] = []

    async def save(self, client, lead):
        self.saved.append((client, lead))
        return 101


def _tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }


def _assistant(llm, catalog=None, leads=None) -> SalesAssistant:
    return SalesAssistant(
        client=llm,
        catalog=catalog or FakeCatalog(),
        leads=leads or FakeLeads(),
        business_info="Menejer: +998901234567",
        fallback_text="FALLBACK",
    )


async def test_search_then_answer():
    llm = FakeLlm([
        _tool_call("search_cars", {"query": "Malibu", "budget_max_usd": "18000"}),
        {"role": "assistant", "content": "Malibu 2, 2019 — $17,500 bor."},
    ])
    catalog = FakeCatalog()
    reply = await _assistant(llm, catalog).reply(client_ctx=CLIENT, history=[], user_text="Malibu bormi?")

    assert reply.text == "Malibu 2, 2019 — $17,500 bor."
    assert reply.used_tools == ["search_cars"]
    assert catalog.searches[0].budget_max_usd == 18000
    tool_msg = llm.calls[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"])["cars"][0]["listing_id"] == 7


async def test_save_lead_normalizes_phone():
    leads = FakeLeads()
    llm = FakeLlm([
        _tool_call("save_lead", {"name": "Sardor", "phone": "90 123 45 67", "listing_id": 7, "payment_type": "naqd"}),
        {"role": "assistant", "content": "Rahmat! Menejer bog'lanadi."},
    ])
    reply = await _assistant(llm, leads=leads).reply(client_ctx=CLIENT, history=[], user_text="+998901234567")

    assert reply.lead_id == 101
    _, lead = leads.saved[0]
    assert lead.phone == "+998901234567"
    assert lead.listing_id == 7 and lead.payment_type == "naqd"


async def test_invalid_phone_is_returned_to_model_not_saved():
    leads = FakeLeads()
    llm = FakeLlm([
        _tool_call("save_lead", {"phone": "123"}),
        {"role": "assistant", "content": "Raqamni to'liq yozing."},
    ])
    reply = await _assistant(llm, leads=leads).reply(client_ctx=CLIENT, history=[], user_text="123")

    assert reply.lead_id is None and not leads.saved
    assert "error" in json.loads(llm.calls[1][-1]["content"])


async def test_llm_failure_returns_fallback():
    reply = await _assistant(FakeLlm([LlmError("down")])).reply(client_ctx=CLIENT, history=[], user_text="salom")
    assert reply.failed and reply.text == "FALLBACK"


async def test_history_is_trimmed_and_system_prompt_first():
    llm = FakeLlm([{"role": "assistant", "content": "ok"}])
    history = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    await _assistant(llm).reply(client_ctx=CLIENT, history=history, user_text="oxirgi")

    sent = llm.calls[0]
    assert sent[0]["role"] == "system" and "Real Avto" in sent[0]["content"]
    assert "Menejer: +998901234567" in sent[0]["content"]
    assert sent[-1] == {"role": "user", "content": "oxirgi"}
    assert len(sent) == 1 + 20 + 1


async def test_endless_tool_loop_is_bounded():
    llm = FakeLlm([_tool_call("get_car", {"listing_id": 7}, f"c{i}") for i in range(5)] + [
        {"role": "assistant", "content": "Yakuniy javob"}
    ])
    reply = await _assistant(llm).reply(client_ctx=CLIENT, history=[], user_text="?")
    assert reply.text == "Yakuniy javob"
    assert len(llm.calls) == 6


def test_parse_args_clamp_and_ignore_garbage():
    s = parse_search_args({"query": "  Cobalt ", "limit": 99, "year_min": "abc"})
    assert s.query == "Cobalt" and s.limit == 8 and s.year_min is None
    lead = parse_lead_args({"phone": "+998 91 000 00 00", "payment_type": "bitcoin"})
    assert isinstance(lead, LeadInput) and lead.payment_type is None
