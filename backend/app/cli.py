import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import typer
from sqlalchemy import func, select

from app.agents import BottleneckAgent, FounderReportAgent, MemoryQAAgent
from app.db import Base, SessionLocal, engine
from app.ingestion.telegram_export_parser import load_export, parse_messages
from app.memory.context_retriever import ContextRetriever
from app.memory.episode_service import create_telegram_export_source, import_telegram_messages
from app.models import Company, Entity, Episode, EpisodeFact, Fact, Source
from app.workers.process_backfill import backfill_plan, process_backfill as run_process_backfill

app = typer.Typer(help="Zargar Labs local MVP CLI.")


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


@app.command("create-company")
def create_company(name: str = typer.Option(...), industry: str | None = typer.Option(None)) -> None:
    ensure_schema()
    with SessionLocal() as db:
        company = Company(name=name, industry=industry)
        db.add(company)
        db.commit()
        db.refresh(company)
        typer.echo(f"company_id={company.id}")
        typer.echo(f"name={company.name}")
        typer.echo(f"industry={company.industry or ''}")


@app.command("import-telegram")
def import_telegram(company_id: UUID = typer.Option(...), file: Path = typer.Option(...)) -> None:
    ensure_schema()
    with SessionLocal() as db:
        data = load_export(file)
        messages = parse_messages(data)
        source = create_telegram_export_source(
            db,
            company_id=company_id,
            source_name=data.get("name") or file.name,
            config={"filename": str(file), "message_count": len(messages)},
        )
        imported, skipped = import_telegram_messages(db, company_id, source, messages)
        db.commit()
        typer.echo(f"source_id={source.id}")
        typer.echo(f"imported={imported}")
        typer.echo(f"skipped={skipped}")


@app.command("process-backfill")
def process_backfill(
    company_id: UUID = typer.Option(...),
    limit: int | None = typer.Option(None),
    dry_run: bool = typer.Option(False),
) -> None:
    ensure_schema()
    with SessionLocal() as db:
        plan = backfill_plan(db, company_id, limit=limit)
        typer.echo(f"pending={plan['pending']}")
        typer.echo(f"llm_calls={plan['llm_calls']}")
        typer.echo(f"skipped_noise={plan['skipped_noise']}")
        if dry_run:
            typer.echo("dry_run=true")
            return
        processed, skipped = asyncio.run(run_process_backfill(db, company_id, limit=limit))
        active = db.scalar(select(Fact).where(Fact.company_id == company_id, Fact.status == "active").limit(1))
        typer.echo(f"processed={processed}")
        typer.echo(f"skipped={skipped}")
        typer.echo(f"has_active_facts={bool(active)}")


@app.command("ask")
def ask(company_id: UUID = typer.Option(...), query: str = typer.Option(...)) -> None:
    ensure_schema()
    with SessionLocal() as db:
        result = asyncio.run(MemoryQAAgent().run(db, company_id, query))
        typer.echo(result["answer"])


@app.command("report")
def report(company_id: UUID = typer.Option(...), period: str = typer.Option("week")) -> None:
    ensure_schema()
    start, end = period_window(period)
    with SessionLocal() as db:
        result = asyncio.run(FounderReportAgent().run(db, company_id, start, end))
        typer.echo(result["report"])


@app.command("bottlenecks")
def bottlenecks(company_id: UUID = typer.Option(...), period: str = typer.Option("week")) -> None:
    ensure_schema()
    start, end = period_window(period)
    with SessionLocal() as db:
        result = asyncio.run(BottleneckAgent().run(db, company_id, start, end))
        typer.echo(result["bottlenecks"])


@app.command("context-search")
def context_search(company_id: UUID = typer.Option(...), query: str = typer.Option(...)) -> None:
    ensure_schema()
    with SessionLocal() as db:
        result = ContextRetriever().search(db, company_id, query=query, time_mode="current")
        typer.echo(result["context"])


@app.command("stats")
def stats(company_id: UUID = typer.Option(...)) -> None:
    ensure_schema()
    with SessionLocal() as db:
        episode_count = db.scalar(select(func.count()).select_from(Episode).where(Episode.company_id == company_id)) or 0
        chat_count = db.scalar(select(func.count(func.distinct(Episode.chat_id))).where(Episode.company_id == company_id)) or 0
        date_range = db.execute(
            select(func.min(Episode.event_time), func.max(Episode.event_time)).where(Episode.company_id == company_id)
        ).one()
        entity_count = db.scalar(select(func.count()).select_from(Entity).where(Entity.company_id == company_id)) or 0
        fact_count = db.scalar(select(func.count()).select_from(Fact).where(Fact.company_id == company_id)) or 0
        active_facts = count_facts(db, company_id, "active")
        invalidated_facts = count_facts(db, company_id, "invalidated")
        needs_review = count_facts(db, company_id, "needs_review")
        typer.echo(f"episodes={episode_count}")
        typer.echo(f"chats={chat_count}")
        typer.echo(f"date_range={date_range[0]} to {date_range[1]}")
        typer.echo("top_senders:")
        for sender, count in top_senders(db, company_id):
            typer.echo(f"- {sender or 'unknown'}: {count}")
        typer.echo(f"entities={entity_count}")
        typer.echo(f"facts={fact_count}")
        typer.echo(f"active_facts={active_facts}")
        typer.echo(f"invalidated_facts={invalidated_facts}")
        typer.echo(f"facts_needing_review={needs_review}")


@app.command("facts")
def facts(company_id: UUID = typer.Option(...), status: str = typer.Option("active")) -> None:
    ensure_schema()
    with SessionLocal() as db:
        rows = db.execute(
            select(Fact, Episode)
            .outerjoin(EpisodeFact, EpisodeFact.fact_id == Fact.id)
            .outerjoin(Episode, Episode.id == EpisodeFact.episode_id)
            .where(Fact.company_id == company_id, Fact.status == status)
            .order_by(Fact.valid_at.asc(), Fact.created_at.asc())
        ).all()
        for fact, episode in rows:
            typer.echo(format_fact_line(fact, episode))


@app.command("entities")
def entities(company_id: UUID = typer.Option(...)) -> None:
    ensure_schema()
    with SessionLocal() as db:
        for entity in db.scalars(
            select(Entity).where(Entity.company_id == company_id).order_by(Entity.entity_type, Entity.name)
        ):
            typer.echo(f"{entity.name} [{entity.entity_type}] - {entity.summary or ''}")


@app.command("sources")
def sources(company_id: UUID = typer.Option(...)) -> None:
    ensure_schema()
    with SessionLocal() as db:
        for source in db.scalars(select(Source).where(Source.company_id == company_id).order_by(Source.created_at)):
            episode_count = db.scalar(select(func.count()).select_from(Episode).where(Episode.source_id == source.id)) or 0
            typer.echo(f"{source.id} | {source.source_type} | {source.source_name} | episodes={episode_count}")


@app.command("review")
def review(company_id: UUID = typer.Option(...)) -> None:
    ensure_schema()
    with SessionLocal() as db:
        typer.echo("episodes_needing_review:")
        for episode in db.scalars(
            select(Episode)
            .where(Episode.company_id == company_id, Episode.processed_status.in_(["needs_review", "failed"]))
            .order_by(Episode.event_time)
        ):
            reason = (episode.raw_payload or {}).get("review_reason", "")
            error = (episode.raw_payload or {}).get("review_error", "")
            typer.echo(
                f"- {episode.processed_status} | {episode.chat_title or episode.chat_id}, "
                f"{episode.actor_name}, msg {episode.message_id}, {episode.event_time} | {reason} | {error}"
            )
        typer.echo("facts_needing_review:")
        for fact, episode in db.execute(
            select(Fact, Episode)
            .outerjoin(EpisodeFact, EpisodeFact.fact_id == Fact.id)
            .outerjoin(Episode, Episode.id == EpisodeFact.episode_id)
            .where(Fact.company_id == company_id, Fact.status == "needs_review")
            .order_by(Fact.created_at)
        ):
            typer.echo(format_fact_line(fact, episode))
        typer.echo("low_confidence_facts:")
        for fact, episode in db.execute(
            select(Fact, Episode)
            .outerjoin(EpisodeFact, EpisodeFact.fact_id == Fact.id)
            .outerjoin(Episode, Episode.id == EpisodeFact.episode_id)
            .where(Fact.company_id == company_id, Fact.confidence < 0.6)
            .order_by(Fact.confidence.asc())
        ):
            typer.echo(format_fact_line(fact, episode))


def period_window(period: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(days=7)
    return start, now


def count_facts(db, company_id: UUID, status: str) -> int:
    return db.scalar(select(func.count()).select_from(Fact).where(Fact.company_id == company_id, Fact.status == status)) or 0


def top_senders(db, company_id: UUID) -> list[tuple[str | None, int]]:
    return list(
        db.execute(
            select(Episode.actor_name, func.count())
            .where(Episode.company_id == company_id)
            .group_by(Episode.actor_name)
            .order_by(func.count().desc())
            .limit(10)
        ).all()
    )


def format_fact_line(fact: Fact, episode: Episode | None) -> str:
    source = "source=unknown"
    if episode:
        source = f"source={episode.chat_title or episode.chat_id}, {episode.actor_name}, msg {episode.message_id}, {episode.event_time}"
    return (
        f"{fact.status} | {fact.relation_type} | {fact.fact_text} | "
        f"valid_at={fact.valid_at} | invalid_at={fact.invalid_at} | {source}"
    )


if __name__ == "__main__":
    app()
