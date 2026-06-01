from uuid import UUID

from sqlalchemy.orm import Session

from app.memory.context_retriever import ContextRetriever


class MemoryQAAgent:
    async def run(self, db: Session, company_id: UUID, query: str) -> dict:
        time_mode = "historical" if is_policy_query(query) else "current"
        retrieved = ContextRetriever().search(db, company_id, query=query, time_mode=time_mode, limit=20)
        answer = format_answer(retrieved)
        return {"answer": answer, "retrieved_context": retrieved}


def is_policy_query(query: str) -> bool:
    lowered = query.lower()
    return "policy" in lowered or "policies" in lowered


def format_answer(retrieved: dict) -> str:
    facts = retrieved["facts"]
    active = [fact for fact in facts if fact["status"] == "active"]
    historical = [fact for fact in facts if fact["status"] != "active" or fact.get("invalid_at")]
    lines = ["Answer:"]
    if active:
        for fact in active[:6]:
            lines.append(wrap_fact(fact))
    else:
        lines.append("- No active matching facts were found in the temporal memory.")
    lines.append("")
    lines.append("Current facts:")
    if active:
        for fact in active[:8]:
            lines.extend(format_fact_block(fact))
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Historical/outdated facts:")
    if historical:
        for fact in historical[:8]:
            lines.extend(format_fact_block(fact))
    else:
        lines.append("- none found")
    lines.append("")
    lines.append("Evidence:")
    sources = retrieved["sources"]
    if sources:
        seen = set()
        for source in sources:
            key = (source["chat_title"], source["actor"], source["message_id"], source["event_time"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {source['chat_title']}, {source['actor']}, msg {source['message_id']}, {source['event_time']}")
    else:
        lines.append("- none")
    return "\n".join(lines)


def wrap_fact(fact: dict) -> str:
    return f"- [{fact['fact_type']}] {fact['fact_text']}"


def format_fact_block(fact: dict) -> list[str]:
    invalid = fact["invalid_at"] or "present"
    return [
        f"- [{fact['fact_type']}] {fact['fact_text']}",
        f"  Relation: {fact['relation_type']}. Status: {fact['status']}.",
        f"  Valid: {fact['valid_at'] or 'unknown'} to {invalid}.",
    ]
