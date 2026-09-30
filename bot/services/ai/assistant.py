"""AI savdo maslahatchisi: bazadan mashina qidiradi, savollarga javob beradi, issiq mijozni saralaydi.

Bu modul Telegram/DB ga bog‘liq emas — katalog va lead saqlash `CarCatalog` / `LeadSink`
orqali beriladi (testlarda soxta obyektlar bilan tekshiriladi).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from bot.services.ai.llm import ChatClient, LlmError
from bot.services.ai.prompts import build_system_prompt
from bot.utils.phone import normalize_uz_phone

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
MAX_HISTORY_MESSAGES = 20
MAX_SEARCH_RESULTS = 8
PAYMENT_TYPES = ("naqd", "kredit", "barter", "noma'lum")


@dataclass(frozen=True)
class CarSearch:
    query: str | None = None
    year_min: int | None = None
    year_max: int | None = None
    budget_min_usd: int | None = None
    budget_max_usd: int | None = None
    max_mileage_km: int | None = None
    limit: int = 5


@dataclass(frozen=True)
class LeadInput:
    name: str | None
    phone: str
    listing_id: int | None
    car_text: str | None
    payment_type: str | None
    budget_usd: int | None
    note: str | None


@dataclass(frozen=True)
class ClientContext:
    platform: str  # telegram | instagram
    social_id: str
    name: str | None = None
    username: str | None = None


class CarCatalog(Protocol):
    async def search(self, params: CarSearch) -> list[dict[str, Any]]: ...

    async def get(self, listing_id: int) -> dict[str, Any] | None: ...


class LeadSink(Protocol):
    async def save(self, client: ClientContext, lead: LeadInput) -> int:
        """Lead ID qaytaradi (menejerlarga xabar ham shu yerda yuboriladi)."""


@dataclass
class AssistantReply:
    text: str
    lead_id: int | None = None
    used_tools: list[str] = field(default_factory=list)
    failed: bool = False


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_cars",
            "description": (
                "Real Avto bazasidagi sotuvdagi mashinalarni qidirish. Mijoz qaysi mashina, qancha byudjet, "
                "qaysi yil kerakligini aytganda chaqiring. Faqat shu natijalardagi mashinalar haqida gapiring."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Marka va/yoki model, lotin harflarida: 'Cobalt', 'Chevrolet Malibu', 'Nexia 3'.",
                    },
                    "year_min": {"type": "integer"},
                    "year_max": {"type": "integer"},
                    "budget_min_usd": {"type": "integer", "description": "Minimal narx, USD"},
                    "budget_max_usd": {"type": "integer", "description": "Maksimal narx, USD"},
                    "max_mileage_km": {"type": "integer"},
                    "limit": {"type": "integer", "description": "1..8, standart 5"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_car",
            "description": "Bitta e'lon (mashina) haqida to'liq ma'lumot, ID bo'yicha.",
            "parameters": {
                "type": "object",
                "properties": {"listing_id": {"type": "integer"}},
                "required": ["listing_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_lead",
            "description": (
                "Mijoz mashinani ko'rishga kelmoqchi / sotib olishga tayyor bo'lsa va telefon raqamini bergan "
                "bo'lsa — menejerga issiq mijoz sifatida yuborish. Raqamsiz chaqirmang."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Mijoz ismi (aytgan bo'lsa)"},
                    "phone": {"type": "string", "description": "Telefon raqam, masalan +998901234567"},
                    "listing_id": {"type": "integer", "description": "Qiziqqan e'lon ID (bo'lsa)"},
                    "car_text": {"type": "string", "description": "Qiziqqan mashina qisqacha: 'Malibu 2, 2019'"},
                    "payment_type": {"type": "string", "enum": list(PAYMENT_TYPES)},
                    "budget_usd": {"type": "integer"},
                    "note": {"type": "string", "description": "Menejer uchun qisqa izoh: qachon keladi, nima so'radi"},
                },
                "required": ["phone"],
            },
        },
    },
]


def _opt_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _opt_str(v: Any, max_len: int) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s[:max_len] if s else None


def parse_search_args(args: dict[str, Any]) -> CarSearch:
    limit = _opt_int(args.get("limit")) or 5
    return CarSearch(
        query=_opt_str(args.get("query"), 80),
        year_min=_opt_int(args.get("year_min")),
        year_max=_opt_int(args.get("year_max")),
        budget_min_usd=_opt_int(args.get("budget_min_usd")),
        budget_max_usd=_opt_int(args.get("budget_max_usd")),
        max_mileage_km=_opt_int(args.get("max_mileage_km")),
        limit=max(1, min(MAX_SEARCH_RESULTS, limit)),
    )


def parse_lead_args(args: dict[str, Any]) -> LeadInput | str:
    """LeadInput yoki model uchun xato matni."""
    phone = normalize_uz_phone(str(args.get("phone") or ""))
    if phone is None:
        return "Telefon raqam noto'g'ri. Mijozdan +998XXXXXXXXX formatida raqamni qayta so'rang."
    payment = _opt_str(args.get("payment_type"), 20)
    if payment not in PAYMENT_TYPES:
        payment = None
    return LeadInput(
        name=_opt_str(args.get("name"), 200),
        phone=phone,
        listing_id=_opt_int(args.get("listing_id")),
        car_text=_opt_str(args.get("car_text"), 255),
        payment_type=payment,
        budget_usd=_opt_int(args.get("budget_usd")),
        note=_opt_str(args.get("note"), 1000),
    )


class SalesAssistant:
    def __init__(
        self,
        *,
        client: ChatClient,
        catalog: CarCatalog,
        leads: LeadSink,
        business_info: str,
        fallback_text: str,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._leads = leads
        self._business_info = business_info
        self._fallback_text = fallback_text

    async def reply(
        self,
        *,
        client_ctx: ClientContext,
        history: list[dict[str, str]],
        user_text: str,
    ) -> AssistantReply:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(self._business_info, client_ctx)},
            *history[-MAX_HISTORY_MESSAGES:],
            {"role": "user", "content": user_text},
        ]
        result = AssistantReply(text="")

        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                msg = await self._client.complete(messages, TOOLS)
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    result.text = (msg.get("content") or "").strip() or self._fallback_text
                    return result

                messages.append(
                    {"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls}
                )
                for call in tool_calls:
                    fn = call.get("function") or {}
                    name = str(fn.get("name") or "")
                    result.used_tools.append(name)
                    output = await self._run_tool(name, fn.get("arguments"), client_ctx, result)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "content": json.dumps(output, ensure_ascii=False, default=str),
                        }
                    )
            # Juda ko‘p tool aylanishi — oxirgi marta toolsiz javob so‘raymiz.
            msg = await self._client.complete(messages, [])
            result.text = (msg.get("content") or "").strip() or self._fallback_text
            return result
        except LlmError:
            result.text = self._fallback_text
            result.failed = True
            return result

    async def _run_tool(
        self,
        name: str,
        raw_args: Any,
        client_ctx: ClientContext,
        result: AssistantReply,
    ) -> Any:
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            return {"error": "arguments JSON noto'g'ri"}

        try:
            if name == "search_cars":
                cars = await self._catalog.search(parse_search_args(args))
                return {"count": len(cars), "cars": cars}
            if name == "get_car":
                listing_id = _opt_int(args.get("listing_id"))
                car = await self._catalog.get(listing_id) if listing_id else None
                return car or {"error": "Bunday e'lon topilmadi yoki sotilgan"}
            if name == "save_lead":
                lead = parse_lead_args(args)
                if isinstance(lead, str):
                    return {"error": lead}
                result.lead_id = await self._leads.save(client_ctx, lead)
                return {"ok": True, "lead_id": result.lead_id}
        except Exception:
            logger.exception("AI tool %s xatosi", name)
            return {"error": "Ichki xato, mijozga menejer raqamini bering"}
        return {"error": f"Noma'lum funksiya: {name}"}
