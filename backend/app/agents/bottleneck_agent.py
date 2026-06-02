from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.founder_report_agent import EMPTY_PERIOD_MESSAGE, format_source, load_typed_facts, serialize_retrieved

BOTTLENECK_FACT_TYPES = {"bottleneck", "complaint", "payment_issue", "customer_objection", "task"}


class BottleneckAgent:
    async def run(self, db: Session, company_id: UUID, start_date: datetime | None, end_date: datetime | None) -> dict:
        facts = [
            fact
            for fact in load_typed_facts(db, company_id, start_date, end_date, limit=80)
            if fact["fact_type"] in BOTTLENECK_FACT_TYPES or bottleneck_category(fact) != "Other Operational Issue"
        ]
        if not facts:
            return {"bottlenecks": EMPTY_PERIOD_MESSAGE, "retrieved_context": {"facts": [], "sources": []}}
        return {"bottlenecks": format_bottlenecks(facts), "retrieved_context": serialize_retrieved(facts)}


def format_bottlenecks(facts: list[dict]) -> str:
    groups = group_bottleneck_facts(facts)
    lines = []
    for title, grouped_facts in groups.items():
        lines.extend(
            [
                f"Bottleneck title: {title}",
                f"Pattern observed: {pattern_observed(title, grouped_facts)}",
                "Evidence:",
                *format_group_evidence(grouped_facts),
                f"Business impact: {business_impact(title)}",
                f"Suggested fix: {suggested_fix(title)}",
                f"Confidence: {confidence(grouped_facts):.2f}",
                "Related facts:",
                *[f"- {fact['fact_text']}" for fact in grouped_facts[:6]],
                "",
            ]
        )
    return "\n".join(lines).strip()


def group_bottleneck_facts(facts: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for fact in facts:
        category = bottleneck_category(fact)
        groups.setdefault(category, []).append(fact)
    return groups


def bottleneck_category(fact: dict) -> str:
    text = f"{fact.get('fact_text', '')} {fact.get('relation_type', '')} {fact.get('fact_type', '')}".lower()
    if "late reply" in text or "late replies" in text or ("reply" in text and "price" in text):
        return "Late replies after price message"
    if "payment" in text or "confirmation" in text:
        return "Manual payment confirmation delay"
    if "approval" in text or "approve" in text or "manager" in text:
        return "Manager approval bottleneck"
    if "task" in text or "follow" in text or "call lead" in text or "deadline" in text:
        return "Leads not followed up by deadline"
    if "complain" in text or "complaint" in text:
        return "Repeated complaints from customers"
    if "drop" in text or "objection" in text or "expensive" in text:
        return "Leads stall after sales message"
    return "Other Operational Issue"


def pattern_observed(title: str, facts: list[dict]) -> str:
    if len(facts) > 1:
        return f"{len(facts)} related facts point to {title.lower()}."
    return facts[0]["fact_text"]


def format_group_evidence(facts: list[dict]) -> list[str]:
    lines = []
    seen = set()
    for fact in facts:
        source = fact.get("source")
        if not source:
            continue
        key = (source.get("chat_title"), source.get("actor"), source.get("message_id"), source.get("event_time"))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {format_source(source)}")
    return lines or ["- none"]


def business_impact(title: str) -> str:
    if title == "Late replies after price message":
        return "Leads may lose trust or stop responding after receiving pricing."
    if title == "Manual payment confirmation delay":
        return "Revenue confirmation and customer onboarding can slow down."
    if title == "Manager approval bottleneck":
        return "Exceptions wait on manager attention and slow frontline decisions."
    if title == "Leads not followed up by deadline":
        return "Open leads may be lost because follow-up ownership is unclear or late."
    if title == "Repeated complaints from customers":
        return "Repeated complaints can reduce retention and damage service quality."
    if title == "Leads stall after sales message":
        return "The sales process may lose qualified leads at a specific step."
    return "Operational friction may be affecting execution quality."


def suggested_fix(title: str) -> str:
    if title == "Late replies after price message":
        return "Set response-time ownership after price messages and review delayed chats daily."
    if title == "Manual payment confirmation delay":
        return "Assign one owner for payment confirmation and document the confirmation checklist."
    if title == "Manager approval bottleneck":
        return "Define which exceptions need approval and which can be handled by the team."
    if title == "Leads not followed up by deadline":
        return "Turn lead follow-up into an owned task list with deadlines and completion checks."
    if title == "Repeated complaints from customers":
        return "Group complaint causes weekly and assign one corrective action per repeated pattern."
    if title == "Leads stall after sales message":
        return "Review price-message wording and add an objection-handling follow-up step."
    return "Assign an owner, define the next action, and inspect recurrence next week."


def confidence(facts: list[dict]) -> float:
    if not facts:
        return 0.0
    base = sum(float(fact.get("confidence") or 0.0) for fact in facts) / len(facts)
    recurrence_bonus = min(max(len(facts) - 1, 0) * 0.05, 0.15)
    return min(base + recurrence_bonus, 0.99)
