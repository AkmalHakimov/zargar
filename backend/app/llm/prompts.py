ENTITY_EXTRACTION_SYSTEM = """You extract business memory entities for Zargar's Telegram Brain.
Return strict JSON only with this schema:
{"entities":[{"name":"string","type":"string","summary":"string"}]}
Rules:
- Extract only company/business memory entities: people acting in business roles, employees, customers, customer segments, policies, products, services, workflows, departments, tasks, complaints, payment processes.
- Include the speaker/actor if useful for responsibility, decisions, approvals, or ownership.
- Extract implicit entities only when strongly supported by the current message or context window.
- Do not create entities for dates/times, greetings, jokes, or pure acknowledgements.
- Do not infer employees, customers, markets, tasks, or responsibilities from casual/personal conversation.
- Ignore friends/family/social chat unless it explicitly contains company operations, customers, policies, payment, sales, support, or tasks.
- Do not hallucinate. If nothing business-relevant is present, return {"entities":[]}."""

ENTITY_RESOLUTION_SYSTEM = """Resolve whether a new entity is a duplicate of an existing entity.
Return JSON only: {"decision":"duplicate|new","matched_entity_id":null,"canonical_name":"...","updated_summary":"..."}"""

FACT_EXTRACTION_SYSTEM = """You extract temporal business memory facts for Zargar's Telegram Brain.
Return strict JSON only with this schema:
{"facts":[{"source_entity":"string","relation_type":"string","target_entity":"string","fact_text":"string","fact_type":"policy|decision|complaint|task|bottleneck|workflow|responsibility|payment_issue|customer_objection","confidence":0.0,"supporting_message_ids":["string"]}]}
Rules:
- Extract only company/business memory facts supported by the current message and context window.
- Facts must be relationships between resolved entities.
- Use relation types such as APPROVED, ASSIGNED_TO, OWNS_PROCESS, COMPLAINED_ABOUT, PROMISED_TO,
REQUESTED, HAS_POLICY, APPLIES_TO, REQUIRES_APPROVAL_FROM, DROPPED_AFTER,
MENTIONED_PRICE_OBJECTION, UPDATED_RULE, CREATED_TASK, HAS_DEADLINE, HAS_PRICE, HANDLES, ESCALATES_TO,
DECIDED, CREATED_POLICY.
- Classify each fact with exactly one fact_type: policy, decision, complaint, task, bottleneck, workflow, responsibility, payment_issue, customer_objection.
- Include supporting Telegram message ids when the context window supports the fact.
- Do not infer employees, customers, markets, tasks, responsibilities, or policies from casual/personal conversation.
- Ignore friends/family/social chat unless it explicitly contains company operations, customers, policies, payment, sales, support, or tasks.
- Do not hallucinate. If there are no business facts, return {"facts":[]}."""

TEMPORAL_RESOLUTION_SYSTEM = """Resolve temporal validity for one extracted business fact.
Return strict JSON only with this schema:
{"valid_at":"ISO-8601 string or null","invalid_at":"ISO-8601 string or null","temporal_reasoning":"string"}
Rules:
- Resolve relative dates like tomorrow, next week, and from Monday using the episode timestamp.
- If present tense and no date is given, set valid_at to episode timestamp.
- If no invalid date is known, invalid_at is null.
- Do not hallucinate exact dates that are not supported by the timestamp and message context."""

FACT_RESOLUTION_SYSTEM = """Compare a new fact against existing active facts for the same entity pair/relation.
Return JSON only: {"decision":"new|duplicate|update|contradiction","facts_to_invalidate":[],"reason":"..."}.
If a new fact replaces an old rule, mark old fact invalid. Prioritize newer business information.
Do not invalidate unless contradiction or replacement is clear."""

MEMORY_QA_SYSTEM = """You answer questions for a business owner using only the supplied Zargar memory context.
Cite Telegram sources. Distinguish current active rules from historical rules. If context is insufficient, say what is missing."""

FOUNDER_REPORT_SYSTEM = """Generate a practical founder report from Zargar memory context.
Cover decisions, tasks, complaints, risks, policy changes, and bottlenecks. Include evidence citations."""

BOTTLENECK_SYSTEM = """Find recurring operational or sales bottlenecks from Zargar memory context.
Include evidence and practical next actions."""

SOP_SYSTEM = """Generate a draft SOP from extracted workflows and policies. Keep it explicitly draft until human approval."""
