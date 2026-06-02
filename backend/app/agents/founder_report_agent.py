from collections import defaultdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import Entity, Episode, EpisodeFact, Fact

EMPTY_PERIOD_MESSAGE = "No business-relevant memory found for this period."

SECTION_TYPES = {
    "Important Decisions": {"decision"},
    "New or Changed Policies": {"policy"},
    "Open Tasks": {"task"},
    "Customer Complaints": {"complaint"},
    "Payment / Operations Issues": {"payment_issue", "workflow", "responsibility"},
    "Bottlenecks": {"bottleneck", "customer_objection"},
}


class FounderReportAgent:
    async def run(self, db: Session, company_id: UUID, start_date: datetime | None, end_date: datetime | None) -> dict:
        facts = load_typed_facts(db, company_id, start_date, end_date, limit=80)
        if not facts:
            return {"report": EMPTY_PERIOD_MESSAGE, "retrieved_context": {"facts": [], "sources": []}}
        return {"report": format_founder_report(facts), "retrieved_context": serialize_retrieved(facts)}


def load_typed_facts(
    db: Session,
    company_id: UUID,
    start_date: datetime | None,
    end_date: datetime | None,
    limit: int = 80,
) -> list[dict]:
    SourceEntity = aliased(Entity)
    TargetEntity = aliased(Entity)
    stmt = (
        select(Fact, Episode, SourceEntity, TargetEntity)
        .join(SourceEntity, Fact.source_entity_id == SourceEntity.id)
        .join(TargetEntity, Fact.target_entity_id == TargetEntity.id)
        .outerjoin(EpisodeFact, EpisodeFact.fact_id == Fact.id)
        .outerjoin(Episode, Episode.id == EpisodeFact.episode_id)
        .where(Fact.company_id == company_id)
        .order_by(Fact.valid_at.desc().nullslast(), Episode.event_time.desc().nullslast(), Fact.created_at.desc())
        .limit(limit)
    )
    if start_date:
        stmt = stmt.where(Fact.valid_at >= start_date)
    if end_date:
        stmt = stmt.where(Fact.valid_at <= end_date)
    rows = db.execute(stmt).all()
    facts = [serialize_typed_fact(fact, episode, source, target) for fact, episode, source, target in rows]
    return dedupe_facts(facts)


def serialize_typed_fact(fact: Fact, episode: Episode | None, source: Entity, target: Entity) -> dict:
    return {
        "id": str(fact.id),
        "source_entity": source.name,
        "target_entity": target.name,
        "relation_type": fact.relation_type,
        "fact_type": (fact.metadata_ or {}).get("fact_type", "workflow"),
        "fact_text": fact.fact_text,
        "valid_at": fact.valid_at.isoformat() if fact.valid_at else None,
        "invalid_at": fact.invalid_at.isoformat() if fact.invalid_at else None,
        "status": fact.status,
        "confidence": fact.confidence,
        "source": serialize_source(episode),
    }


def serialize_source(episode: Episode | None) -> dict | None:
    if not episode:
        return None
    return {
        "episode_id": str(episode.id),
        "chat_title": episode.chat_title,
        "actor": episode.actor_name,
        "message_id": episode.message_id,
        "event_time": episode.event_time.isoformat(),
    }


def dedupe_facts(facts: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for fact in facts:
        key = (fact["id"], (fact.get("source") or {}).get("message_id"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fact)
    return deduped


def serialize_retrieved(facts: list[dict]) -> dict:
    sources = []
    for fact in facts:
        if fact.get("source"):
            sources.append(fact["source"])
    return {
        "facts": [{key: value for key, value in fact.items() if key != "source"} for fact in facts],
        "sources": dedupe_sources(sources),
    }


def format_founder_report(facts: list[dict]) -> str:
    grouped = group_by_type(facts)
    lines = ["Executive Summary"]
    lines.extend(executive_summary_lines(grouped))
    for section, fact_types in SECTION_TYPES.items():
        lines.extend(["", section])
        section_facts = [fact for fact in facts if fact["fact_type"] in fact_types]
        lines.extend(format_fact_items(section_facts))
    lines.extend(["", "Risks"])
    lines.extend(format_risks(facts))
    lines.extend(["", "Suggested Actions"])
    lines.extend(format_suggested_actions(facts))
    lines.extend(["", "Evidence"])
    lines.extend(format_evidence(facts))
    return "\n".join(lines)


def group_by_type(facts: list[dict]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for fact in facts:
        grouped[fact["fact_type"]].append(fact)
    return grouped


def executive_summary_lines(grouped: dict[str, list[dict]]) -> list[str]:
    parts = []
    labels = [
        ("decision", "decisions"),
        ("policy", "policy updates"),
        ("task", "open tasks"),
        ("complaint", "customer complaints"),
        ("payment_issue", "payment issues"),
        ("bottleneck", "bottlenecks"),
        ("customer_objection", "customer objections"),
    ]
    for fact_type, label in labels:
        count = len(grouped.get(fact_type, []))
        if count:
            parts.append(f"{count} {label}")
    if not parts:
        return ["- No high-signal typed business facts were found."]
    return ["- " + ", ".join(parts) + "."]


def format_fact_items(facts: list[dict]) -> list[str]:
    if not facts:
        return ["- none"]
    return [f"- {fact['fact_text']} ({source_label(fact)})" for fact in facts[:8]]


def format_risks(facts: list[dict]) -> list[str]:
    risk_facts = [
        fact
        for fact in facts
        if fact["fact_type"] in {"complaint", "payment_issue", "bottleneck", "customer_objection"}
    ]
    if not risk_facts:
        return ["- none identified from typed facts"]
    return [f"- {risk_sentence(fact)} ({source_label(fact)})" for fact in risk_facts[:6]]


def risk_sentence(fact: dict) -> str:
    if fact["fact_type"] == "complaint":
        return f"Customer experience risk: {fact['fact_text']}"
    if fact["fact_type"] == "payment_issue":
        return f"Operations/payment risk: {fact['fact_text']}"
    if fact["fact_type"] == "customer_objection":
        return f"Sales conversion risk: {fact['fact_text']}"
    return f"Execution bottleneck risk: {fact['fact_text']}"


def format_suggested_actions(facts: list[dict]) -> list[str]:
    actions = []
    if any(fact["fact_type"] == "task" for fact in facts):
        actions.append("- Review open tasks, assign owners, and confirm deadlines.")
    if any(fact["fact_type"] == "complaint" for fact in facts):
        actions.append("- Address repeated customer complaints and track response-time follow-up.")
    if any(fact["fact_type"] == "payment_issue" for fact in facts):
        actions.append("- Reduce manual payment confirmation delay with a clear owner or checklist.")
    if any(fact["fact_type"] in {"bottleneck", "customer_objection"} for fact in facts):
        actions.append("- Inspect the sales step where leads stall and update the operating process.")
    if any(fact["fact_type"] == "policy" for fact in facts):
        actions.append("- Communicate changed policies to the team and confirm old rules are retired.")
    return actions or ["- No action suggested from the available typed facts."]


def format_evidence(facts: list[dict]) -> list[str]:
    sources = dedupe_sources([fact["source"] for fact in facts if fact.get("source")])
    if not sources:
        return ["- none"]
    return [f"- {format_source(source)}" for source in sources[:12]]


def dedupe_sources(sources: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for source in sources:
        key = (source.get("chat_title"), source.get("actor"), source.get("message_id"), source.get("event_time"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped


def source_label(fact: dict) -> str:
    source = fact.get("source")
    return format_source(source) if source else "source unknown"


def format_source(source: dict) -> str:
    return (
        f"{source.get('chat_title') or 'unknown chat'}, {source.get('actor') or 'unknown actor'}, "
        f"msg {source.get('message_id') or 'unknown'}, {source.get('event_time') or 'unknown time'}"
    )
