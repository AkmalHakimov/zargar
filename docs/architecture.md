# Zargar Labs Architecture

## Product Boundary

Zargar Labs is an agent context layer for Telegram-native businesses. It is not a simple RAG chatbot over chat history. Agents query a compact temporal business memory made of episodes, entities, facts, validity windows, and lightweight communities.

## Pipeline

```text
Telegram Export / Telegram Bot
-> Episode Store
-> Entity Extractor
-> Entity Resolver
-> Fact Extractor
-> Temporal Resolver
-> Fact Deduplicator / Invalidator
-> Business Memory Graph
-> Hybrid Search
-> Context Constructor
-> Agent Runtime
-> Telegram owner bot / API
```

## Memory Layers

### Episodes

Episodes are immutable source memory. Each Telegram message stores source, chat, actor, timestamp, content, raw payload, and processing status. Facts and entities must retain citations back to episodes.

### Entities

Entities are business objects extracted from episodes: people, employees, customers, customer segments, policies, products, workflows, departments, tasks, complaints, and processes.

### Facts

Facts are directed relationships between entities. They carry relation type, natural-language text, source/target entities, confidence, metadata, embeddings, and temporal validity.

### Temporal Validity

Facts use four time fields:

- `valid_at`: when the fact became true in the business world.
- `invalid_at`: when the fact stopped being true in the business world.
- `created_at`: when Zargar created the fact record.
- `expired_at`: when Zargar invalidated or expired it.

When a newer fact clearly replaces an older active rule, the older fact is marked `invalidated` and `invalid_at` is set to the new fact's `valid_at`.

### Communities

MVP communities are rule-based business areas such as Sales Process, Payment Process, Customer Complaints, Discount Policies, Support Workflow, Tasks, and SOPs. They are lightweight grouping aids for retrieval and reporting.

## Retrieval

The MVP retrieval path is hybrid:

1. Keyword matching over fact text and entity names/summaries.
2. Vector similarity via provider-agnostic embeddings and pgvector.
3. Recency and active-status bonuses.
4. Graph-neighbor expansion from matched entities to connected facts.

The context constructor returns compact blocks with facts, entity summaries, communities, and source citations. Agents never read raw chat history directly.

## Provider Interfaces

LLM and embeddings are abstracted behind small interfaces. The default local configuration uses deterministic mock providers. Production can use an OpenAI-compatible chat and embeddings client without changing pipeline code.

## Engineering Constraints

- LLMs never generate SQL.
- Historical imports are processed chronologically by `event_time`.
- Raw Telegram messages are preserved.
- Small/noisy messages can be skipped by the importance filter.
- Source traceability is mandatory from facts to episodes.
- The owner Telegram bot is read/report-only in v1 and does not auto-reply to customers.

