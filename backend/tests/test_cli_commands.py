import os
import subprocess
import sys
from pathlib import Path


def run_cli(db_path: Path, *args: str) -> str:
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}", "USE_MOCK_LLM": "true"}
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def create_demo(db_path: Path) -> str:
    output = run_cli(db_path, "create-company", "--name", "Demo Education Center", "--industry", "education")
    return output.split("company_id=", 1)[1].splitlines()[0]


def import_and_process(db_path: Path, company_id: str) -> None:
    run_cli(db_path, "import-telegram", "--company-id", company_id, "--file", "seed/demo_telegram_export.json")
    run_cli(db_path, "process-backfill", "--company-id", company_id)


def test_stats_and_facts_commands(tmp_path):
    db_path = tmp_path / "zargar.db"
    company_id = create_demo(db_path)
    import_and_process(db_path, company_id)

    stats = run_cli(db_path, "stats", "--company-id", company_id)
    facts = run_cli(db_path, "facts", "--company-id", company_id, "--status", "active")

    assert "episodes=6" in stats
    assert "chats=1" in stats
    assert "active_facts=" in stats
    assert "invalidated_facts=1" in stats
    assert "Returning students now get 15% discount" in facts
    assert "source=Ziyo Education Managers, Founder, msg 3" in facts


def test_memory_qa_source_evidence_format(tmp_path):
    db_path = tmp_path / "zargar.db"
    company_id = create_demo(db_path)
    import_and_process(db_path, company_id)

    answer = run_cli(
        db_path,
        "ask",
        "--company-id",
        company_id,
        "--query",
        "What is our current discount policy?",
    )

    assert "Answer:" in answer
    assert "Current facts:" in answer
    assert "Historical/outdated facts:" in answer
    assert "Evidence:" in answer
    assert "15% discount" in answer
    assert "10% discount" in answer
    assert "Ziyo Education Managers, Founder, msg 3" in answer


def test_process_backfill_dry_run_and_limit(tmp_path):
    db_path = tmp_path / "zargar.db"
    company_id = create_demo(db_path)
    run_cli(db_path, "import-telegram", "--company-id", company_id, "--file", "seed/demo_telegram_export.json")

    dry_run = run_cli(db_path, "process-backfill", "--company-id", company_id, "--limit", "2", "--dry-run")

    assert "pending=2" in dry_run
    assert "llm_calls=2" in dry_run
    assert "dry_run=true" in dry_run
    stats = run_cli(db_path, "stats", "--company-id", company_id)
    assert "facts=0" in stats
