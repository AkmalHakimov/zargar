import json
import re
from datetime import datetime, timedelta
from json import JSONDecodeError

import httpx

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.prompts import MEMORY_QA_SYSTEM
from app.llm.validation import LLMValidationError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, settings: Settings):
        self.settings = settings

    async def json_completion(self, system: str, user: str) -> dict:
        text = await self.text_completion(system, user)
        try:
            return parse_json_object(text)
        except JSONDecodeError:
            repair = await self.text_completion(
                "Repair malformed JSON. Return JSON only, with no markdown.",
                f"Original task system prompt:\n{system}\n\nMalformed response:\n{text}",
            )
            try:
                return parse_json_object(repair)
            except JSONDecodeError as exc:
                raise LLMValidationError(f"Invalid JSON after repair: {exc}") from exc

    async def text_completion(self, system: str, user: str) -> str:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for openai-compatible provider")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{openai_base_url(self.settings).rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                json={
                    "model": openai_model(self.settings),
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]


class MockLLMProvider(LLMProvider):
    async def json_completion(self, system: str, user: str) -> dict:
        current = current_message(user)
        lowered_current = current.lower()
        lowered_all = user.lower()
        if '"entities"' in system:
            actor = "Speaker"
            for line in user.splitlines():
                if line.startswith("Actor:"):
                    actor = line.split(":", 1)[1].strip() or actor
            entities = [{"name": actor, "type": "person", "summary": f"Telegram participant {actor}."}]
            if "discount" in lowered_current:
                entities.append({"name": "Discount Policy", "type": "policy", "summary": "Rules about discounts."})
            if "refund" in lowered_current:
                entities.append({"name": "Refund Policy", "type": "policy", "summary": "Rules about refunds and refund approvals."})
                entities.append({"name": "Refund Requests", "type": "workflow", "summary": "Customer refund request handling."})
            if "returning student" in lowered_current:
                entities.append({"name": "Returning Students", "type": "customer_segment", "summary": "Students who have previously bought or enrolled."})
            if "payment" in lowered_current:
                entities.append({"name": "Payment Process", "type": "workflow", "summary": "Payment follow-up and confirmation process."})
                entities.append({"name": "Payment Follow-up", "type": "task", "summary": "Follow-up work for payment confirmation."})
            if "complain" in lowered_current or "late reply" in lowered_current:
                entities.append({"name": "Customers", "type": "customer_segment", "summary": "Customers discussed in Telegram operations."})
                entities.append({"name": "Late Reply", "type": "complaint", "summary": "Complaint about slow responses."})
                entities.append({"name": "Customer Complaints", "type": "complaint", "summary": "Customer complaints and service issues."})
            if "task" in lowered_current or "deadline" in lowered_current or "call" in lowered_current or "assigned" in lowered_current or "responsible" in lowered_current:
                entities.append({"name": "Open Tasks", "type": "task", "summary": "Open operational tasks from Telegram."})
            if "payment" in lowered_current or "follow-up" in lowered_current:
                entities.append({"name": "Payment Follow-up", "type": "task", "summary": "Follow-up work for payment confirmation."})
            if "price" in lowered_current or "drop" in lowered_current or "lead" in lowered_current:
                entities.append({"name": "Sales Leads", "type": "customer_segment", "summary": "Prospective customers in the sales process."})
                entities.append({"name": "Price Message", "type": "sales_step", "summary": "The sales step where price is sent to leads."})
            if "price objection" in lowered_current or "too expensive" in lowered_current or "expensive" in lowered_current:
                entities.append({"name": "Price Objection", "type": "sales_objection", "summary": "Lead or customer objection about price."})
            for name in extract_person_names(current):
                if name != actor:
                    entities.append({"name": name, "type": "person", "summary": f"Telegram participant or business actor {name}."})
            return {"entities": entities}
        if '"decision"' in system and "matched_entity_id" in system:
            return {"decision": "new", "matched_entity_id": None, "canonical_name": "auto", "updated_summary": "auto"}
        if '"facts"' in system:
            facts = []
            if "10%" in lowered_current and "discount" in lowered_current:
                facts.append({
                    "source_entity": "Discount Policy",
                    "relation_type": "HAS_POLICY",
                    "target_entity": "Returning Students",
                    "fact_text": "Returning students get 10% discount.",
                    "fact_type": "policy",
                    "confidence": 0.72,
                })
            if "15%" in lowered_current and "discount" in lowered_current:
                facts.append({
                    "source_entity": "Discount Policy",
                    "relation_type": "HAS_POLICY",
                    "target_entity": "Returning Students",
                    "fact_text": "Returning students now get 15% discount from Monday.",
                    "fact_type": "policy",
                    "confidence": 0.92,
                })
                facts.append({
                    "source_entity": "Founder",
                    "relation_type": "DECIDED",
                    "target_entity": "Discount Policy",
                    "fact_text": "Founder decided to update the returning student discount to 15% from Monday.",
                    "fact_type": "decision",
                    "confidence": 0.88,
                })
            if "refund" in lowered_current and ("policy" in lowered_current or "rule" in lowered_current):
                facts.append({
                    "source_entity": "Refund Policy",
                    "relation_type": "HAS_POLICY",
                    "target_entity": "Refund Requests",
                    "fact_text": sentence_fact(current, "Refund requests require manager approval."),
                    "fact_type": "policy",
                    "confidence": 0.78,
                })
            if "payment" in lowered_current:
                facts.append({
                    "source_entity": "Payment Process",
                    "relation_type": "HANDLES",
                    "target_entity": "Payment Follow-up",
                    "fact_text": "Payment confirmations are delayed because they are checked manually.",
                    "fact_type": "payment_issue",
                    "confidence": 0.68,
                })
            owner_fact = extract_owner_fact(current)
            if owner_fact:
                facts.append(owner_fact)
            if "complain" in lowered_current or "late reply" in lowered_current:
                facts.append({
                    "source_entity": "Customers",
                    "relation_type": "COMPLAINED_ABOUT",
                    "target_entity": "Late Reply",
                    "fact_text": "Customers complained about late replies after the price message.",
                    "fact_type": "complaint",
                    "confidence": 0.7,
                })
            if "task" in lowered_current or "call" in lowered_current:
                facts.append({
                    "source_entity": "Open Tasks",
                    "relation_type": "CREATED_TASK",
                    "target_entity": "Sales Leads",
                    "fact_text": "Open task: call leads who did not respond after the price message.",
                    "fact_type": "task",
                    "confidence": 0.8,
                })
            if "drop" in lowered_current:
                facts.append({
                    "source_entity": "Sales Leads",
                    "relation_type": "DROPPED_AFTER",
                    "target_entity": "Price Message",
                    "fact_text": "Several leads dropped after receiving the price message.",
                    "fact_type": "bottleneck",
                    "confidence": 0.86,
                })
            if "price objection" in lowered_current or "too expensive" in lowered_current or "expensive" in lowered_current:
                facts.append({
                    "source_entity": "Sales Leads",
                    "relation_type": "MENTIONED_PRICE_OBJECTION",
                    "target_entity": "Price Objection",
                    "fact_text": "Sales leads mentioned price objections.",
                    "fact_type": "customer_objection",
                    "confidence": 0.82,
                })
            return {"facts": facts}
        if '"valid_at"' in system:
            ts = None
            for line in user.splitlines():
                if line.startswith("Episode timestamp:"):
                    ts = line.split(":", 1)[1].strip()
            valid_at = resolve_mock_valid_at(ts, user)
            return {"valid_at": valid_at, "invalid_at": None, "temporal_reasoning": "Resolved deterministically in mock mode."}
        if "facts_to_invalidate" in system:
            return {"decision": "new", "facts_to_invalidate": [], "reason": "No clear duplicate or contradiction."}
        return {}

    async def text_completion(self, system: str, user: str) -> str:
        if system == MEMORY_QA_SYSTEM and "current discount policy" in user.lower():
            fact, valid, source = first_context_fact(user, "discount")
            if fact:
                return (
                    f"Current active discount policy: {fact}\n"
                    f"Valid from: {valid or 'unknown'}\n"
                    f"Source: {source or 'unknown Telegram source'}\n"
                    "Older conflicting discount policies are treated as outdated because current retrieval only uses active, non-invalidated facts."
                )
        return format_mock_memory_answer(user)


def build_llm_provider(settings: Settings) -> LLMProvider:
    if not settings.use_mock_llm:
        return OpenAICompatibleProvider(settings)
    return MockLLMProvider()


def openai_base_url(settings: Settings) -> str:
    return settings.openai_compatible_base_url or settings.openai_base_url


def openai_model(settings: Settings) -> str:
    return settings.openai_chat_model or settings.openai_model


def parse_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    return json.loads(stripped)


def current_message(user: str) -> str:
    marker = "Current message:"
    if marker not in user:
        return user
    return user.split(marker, 1)[1].split("Resolved entities:", 1)[0].strip()


def resolve_mock_valid_at(timestamp: str | None, user: str) -> str | None:
    if not timestamp:
        return None
    fact_text = ""
    for line in user.splitlines():
        if line.startswith("Fact text:"):
            fact_text = line.split(":", 1)[1].strip().lower()
            break
    if "from monday" not in fact_text:
        return timestamp
    base = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    days_until_monday = (7 - base.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    monday = base + timedelta(days=days_until_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def first_context_fact(user: str, keyword: str) -> tuple[str | None, str | None, str | None]:
    lines = user.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("- ") and keyword in line.lower():
            fact = re.sub(r"^\- ", "", line).strip()
            valid = None
            source = None
            for detail in lines[index + 1 : index + 4]:
                if detail.strip().startswith("Valid:"):
                    valid = detail.split(":", 1)[1].strip()
                if detail.strip().startswith("Source:"):
                    source = detail.split(":", 1)[1].strip().rstrip(".")
            return fact, valid, source
    return None, None, None


def extract_person_names(text: str) -> list[str]:
    common = {
        "Update",
        "Open",
        "Pattern",
        "Returning",
        "Two",
        "Customers",
        "Sales",
        "Payment",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }
    names = []
    for match in re.findall(r"\b[A-Z][a-z]{2,}\b", text):
        if match not in common and match not in names:
            names.append(match)
    return names[:4]


def extract_owner_fact(text: str) -> dict | None:
    lower = text.lower()
    match = re.search(r"\b([A-Z][a-z]{2,})\b.*\b(owns|owner|responsible for|assigned to|handles)\b", text)
    if not match and "assigned to" in lower:
        match = re.search(r"assigned to\s+\b([A-Z][a-z]{2,})\b", text)
    if not match:
        return None
    owner = match.group(1)
    target = "Payment Follow-up" if "payment" in lower else "Open Tasks"
    return {
        "source_entity": owner,
        "relation_type": "OWNS_PROCESS",
        "target_entity": target,
        "fact_text": f"{owner} owns {target.lower()}.",
        "fact_type": "responsibility",
        "confidence": 0.84,
    }


def sentence_fact(text: str, fallback: str) -> str:
    stripped = " ".join(text.strip().split())
    return stripped.rstrip(".") + "." if stripped else fallback


def format_mock_memory_answer(user: str) -> str:
    fact, valid, source = first_context_fact(user, "")
    if not fact:
        return (
            "Direct answer: I do not have enough extracted temporal facts to answer from the memory layer.\n"
            "Status: insufficient context\n"
            "Valid: unknown\n"
            "Source evidence: none\n"
            "Older conflicting facts: unknown"
        )
    facts = collect_context_facts(user)
    lines = ["Direct answer:"]
    for item in facts[:6]:
        lines.append(f"- {item['fact']}")
    lines.extend(
        [
            "",
            "Status: current active facts unless explicitly marked otherwise.",
            "Validity:",
        ]
    )
    for item in facts[:6]:
        lines.append(f"- {item['valid'] or 'unknown'}")
    lines.append("Source evidence:")
    for item in facts[:6]:
        lines.append(f"- {item['source'] or 'unknown Telegram source'}")
    lines.append("Older conflicting facts: current retrieval excludes invalidated facts; check `zargar facts --status invalidated` for history.")
    return "\n".join(lines)


def collect_context_facts(user: str) -> list[dict[str, str | None]]:
    lines = user.splitlines()
    facts = []
    for index, line in enumerate(lines):
        if not line.startswith("- "):
            continue
        fact = re.sub(r"^\- ", "", line).strip()
        valid = None
        source = None
        for detail in lines[index + 1 : index + 4]:
            if detail.strip().startswith("Valid:"):
                valid = detail.split(":", 1)[1].strip()
            if detail.strip().startswith("Source:"):
                source = detail.split(":", 1)[1].strip().rstrip(".")
        if valid or source:
            facts.append({"fact": fact, "valid": valid, "source": source})
    return facts
