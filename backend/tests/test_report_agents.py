import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone

from app.agents.bottleneck_agent import BottleneckAgent
from app.agents.founder_report_agent import EMPTY_PERIOD_MESSAGE, FounderReportAgent
from app.bot.telegram_owner_bot import TelegramOwnerBot
from app.models import Episode
from app.workers.process_backfill import process_backfill
from tests.test_vertical_slice import build_demo_memory


DEMO_START = datetime(2026, 5, 20, tzinfo=timezone.utc)
DEMO_END = datetime(2026, 5, 30, tzinfo=timezone.utc)


def test_weekly_report_includes_decisions_tasks_complaints_bottlenecks():
    db, company, _, _ = build_demo_memory()
    asyncio.run(process_backfill(db, company.id))

    result = asyncio.run(FounderReportAgent().run(db, company.id, DEMO_START, DEMO_END))
    report = result["report"]

    assert "Executive Summary" in report
    assert "Important Decisions" in report
    assert "Founder decided to update" in report
    assert "Open Tasks" in report
    assert "Open task: call leads" in report
    assert "Customer Complaints" in report
    assert "Customers complained about late replies" in report
    assert "Payment / Operations Issues" in report
    assert "Payment confirmations are delayed" in report
    assert "Bottlenecks" in report
    assert "Several leads dropped" in report
    assert "Suggested Actions" in report


def test_report_excludes_skipped_personal_messages():
    db, company, _, _ = build_demo_memory()
    personal = make_personal_episode(db, company.id)
    db.add(personal)
    db.commit()

    asyncio.run(process_backfill(db, company.id))

    result = asyncio.run(FounderReportAgent().run(db, company.id, DEMO_START, DEMO_END))
    report = result["report"]

    assert personal.processed_status == "skipped_personal"
    assert "dinner this weekend" not in report
    assert "friends" not in report.lower()


def test_report_includes_evidence():
    db, company, _, _ = build_demo_memory()
    asyncio.run(process_backfill(db, company.id))

    result = asyncio.run(FounderReportAgent().run(db, company.id, DEMO_START, DEMO_END))

    assert "Evidence" in result["report"]
    assert "Ziyo Education Managers, Founder, msg 3" in result["report"]


def test_bottleneck_agent_groups_repeated_issues():
    db, company, _, _ = build_demo_memory()
    asyncio.run(process_backfill(db, company.id))

    result = asyncio.run(BottleneckAgent().run(db, company.id, DEMO_START, DEMO_END))
    output = result["bottlenecks"]

    assert "Bottleneck title:" in output
    assert "Late replies after price message" in output
    assert "Pattern observed:" in output
    assert "Evidence:" in output
    assert "Business impact:" in output
    assert "Suggested fix:" in output
    assert "Confidence:" in output
    assert "Related facts:" in output


def test_no_memory_period_returns_clean_empty_state():
    db, company, _, _ = build_demo_memory()
    asyncio.run(process_backfill(db, company.id))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, tzinfo=timezone.utc)

    report = asyncio.run(FounderReportAgent().run(db, company.id, start, end))
    bottlenecks = asyncio.run(BottleneckAgent().run(db, company.id, start, end))

    assert report["report"] == EMPTY_PERIOD_MESSAGE
    assert bottlenecks["bottlenecks"] == EMPTY_PERIOD_MESSAGE


def test_telegram_bot_report_commands_call_improved_agents():
    db, company, _, _ = build_demo_memory()
    report_agent = FakeReportAgent()
    bottleneck_agent = FakeBottleneckAgent()
    bot = TelegramOwnerBot(
        company.id,
        session_factory(db),
        allowed_user_ids={123},
        report_agent=report_agent,
        bottleneck_agent=bottleneck_agent,
    )

    week = asyncio.run(bot.handle_message(123, "/report_week"))
    today = asyncio.run(bot.handle_message(123, "/report_today"))
    bottlenecks = asyncio.run(bot.handle_message(123, "/bottlenecks_week"))

    assert week == ["Executive Summary\n- weekly report"]
    assert today == ["Executive Summary\n- weekly report"]
    assert bottlenecks == ["Bottleneck title: test"]
    assert len(report_agent.calls) == 2
    assert len(bottleneck_agent.calls) == 1


def make_personal_episode(db, company_id):
    source = db.query(Episode).filter_by(company_id=company_id).first().source_id
    return Episode(
        company_id=company_id,
        source_id=source,
        source_type="telegram_export",
        chat_id="ziyo-demo-group",
        chat_title="Ziyo Education Managers",
        message_id="personal-1",
        actor_name="Founder",
        event_time=datetime(2026, 5, 24, 9, 0, tzinfo=timezone.utc),
        content_type="text",
        content="Friends are coming for dinner this weekend.",
        raw_payload={},
        processed_status="pending",
    )


def session_factory(db):
    @contextmanager
    def factory():
        yield db

    return factory


class FakeReportAgent:
    def __init__(self):
        self.calls = []

    async def run(self, db, company_id, start_date, end_date):
        self.calls.append((company_id, start_date, end_date))
        return {"report": "Executive Summary\n- weekly report", "retrieved_context": {"facts": [], "sources": []}}


class FakeBottleneckAgent:
    def __init__(self):
        self.calls = []

    async def run(self, db, company_id, start_date, end_date):
        self.calls.append((company_id, start_date, end_date))
        return {"bottlenecks": "Bottleneck title: test", "retrieved_context": {"facts": [], "sources": []}}
