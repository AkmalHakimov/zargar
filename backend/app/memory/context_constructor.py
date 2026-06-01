from app.models import Community, Entity, Episode, Fact


def construct_context(
    facts: list[tuple[Fact, Episode | None, Entity | None, Entity | None]],
    entities: list[Entity],
    communities: list[Community],
) -> str:
    lines = ["<FACTS>"]
    for fact, episode, source, target in facts:
        invalid = fact.invalid_at.isoformat() if fact.invalid_at else "present"
        fact_type = (getattr(fact, "metadata_", None) or {}).get("fact_type", "workflow")
        citation = "unknown source"
        if episode:
            citation = f"{episode.chat_title or episode.chat_id}, {episode.actor_name}, msg {episode.message_id}, {episode.event_time.isoformat()}"
        lines.append(f"- {fact.fact_text} ({source.name if source else '?'} {fact.relation_type} {target.name if target else '?'})")
        lines.append(f"  Type: {fact_type}. Status: {getattr(fact, 'status', 'active')}.")
        lines.append(f"  Valid: {fact.valid_at.isoformat() if fact.valid_at else 'unknown'} to {invalid}.")
        lines.append(f"  Source: {citation}.")
    lines.append("</FACTS>")
    lines.append("")
    lines.append("<ENTITIES>")
    for entity in entities:
        lines.append(f"- {entity.name}: {entity.summary or ''}")
    lines.append("</ENTITIES>")
    lines.append("")
    lines.append("<COMMUNITIES>")
    for community in communities:
        lines.append(f"- {community.name}: {community.summary or ''}")
    lines.append("</COMMUNITIES>")
    return "\n".join(lines)
