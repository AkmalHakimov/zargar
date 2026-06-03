import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.developer_agent import DeveloperAgent, DeveloperAgentInput, branch_name
from app.bot.telegram_owner_bot import TelegramOwnerBot
from app.config import Settings
from app.db import Base
from app.models import Company, DeveloperTask
from app.services.github_service import (
    FileChange,
    GitHubSafetyError,
    GitHubService,
    validate_not_protected_branch,
    validate_safe_path,
    validate_zargar_branch,
)


def setup_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    company = Company(name="Demo", industry="software")
    db.add(company)
    db.commit()
    db.refresh(company)
    return db, company


def test_repository_allowlist():
    service = GitHubService(Settings(github_allowed_repos="akmal/zargar"))

    service.verify_repository_allowed("akmal/zargar")
    with pytest.raises(GitHubSafetyError):
        service.verify_repository_allowed("other/repo")


def test_branch_naming_rules():
    generated = branch_name("Add GET /health endpoint and tests", "12345678-aaaa")

    assert generated.startswith("zargar/add-get-health-endpoint-and-tests-dev123456")
    validate_zargar_branch(generated)
    with pytest.raises(GitHubSafetyError):
        validate_zargar_branch("main")
    with pytest.raises(GitHubSafetyError):
        validate_zargar_branch("zargar/nested/branch")


def test_forbidden_file_detection():
    with pytest.raises(GitHubSafetyError):
        validate_safe_path(".env")
    with pytest.raises(GitHubSafetyError):
        validate_safe_path(".github/workflows/deploy.yml")
    with pytest.raises(GitHubSafetyError):
        validate_safe_path("keys/private_key.pem")


def test_direct_push_prevention():
    with pytest.raises(GitHubSafetyError):
        validate_not_protected_branch("main", "main")
    with pytest.raises(GitHubSafetyError):
        validate_not_protected_branch("develop", "main")
    validate_not_protected_branch("zargar/add-health-dev123", "main")


def test_stale_branch_cleanup():
    service = CapturingGitHubService(Settings(github_allowed_repos="akmal/zargar", branch_retention_days=7))
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    deleted = asyncio.run(
        service.cleanup_stale_zargar_branches(
            "akmal/zargar",
            [
                {"name": "zargar/old-dev001", "updated_at": old},
                {"name": "zargar/fresh-dev002", "updated_at": fresh},
                {"name": "feature/not-owned", "updated_at": old},
            ],
        )
    )

    assert deleted == ["zargar/old-dev001"]
    assert service.deleted_refs == ["/repos/akmal/zargar/git/refs/heads/zargar/old-dev001"]


def test_unauthorized_user_rejection():
    db, company = setup_db()
    agent = DeveloperAgent(github=MockGitHub(), allowed_requester_ids={123})

    result = asyncio.run(agent.run(db, request(company.id, requester_id="999")))

    assert result["status"] == "unauthorized"
    assert db.query(DeveloperTask).count() == 0


def test_ambiguity_detection_creates_clarification_task():
    db, company = setup_db()
    agent = DeveloperAgent(github=MockGitHub(), allowed_requester_ids={123})

    result = asyncio.run(agent.run(db, request(company.id, task_text="Improve auth", requester_id="123")))
    task = db.get(DeveloperTask, result["task_id"])

    assert result["status"] == "needs_clarification"
    assert "Task requires clarification." in result["message"]
    assert task.status == "needs_clarification"
    assert task.branch is None


def test_task_creation_and_pr_creation_with_mocked_github_client():
    db, company = setup_db()
    github = MockGitHub()
    agent = DeveloperAgent(github=github, allowed_requester_ids={123})

    result = asyncio.run(agent.run(db, request(company.id, requester_id="123")))
    task = db.get(DeveloperTask, result["task_id"])

    assert result["status"] == "pr_created"
    assert task.status == "pr_created"
    assert task.branch.startswith("zargar/")
    assert task.pr_url == "https://github.example/pull/1"
    assert github.created_branches == [task.branch]
    assert github.created_prs[0]["draft"] is True
    assert github.merges == []
    assert "No merge performed." in result["message"]


def test_branch_reuse_for_same_task():
    db, company = setup_db()
    github = MockGitHub()
    agent = DeveloperAgent(github=github, allowed_requester_ids={123})

    first = asyncio.run(agent.run(db, request(company.id, requester_id="123")))
    second = asyncio.run(agent.run(db, request(company.id, requester_id="123")))

    assert first["task_id"] == second["task_id"]
    assert len(github.created_branches) == 1
    assert "Task already exists" in second["message"]


def test_change_size_limits_are_enforced():
    service = GitHubService(
        Settings(
            github_allowed_repos="akmal/zargar",
            github_max_files_changed=1,
            github_max_new_files=1,
            github_max_lines_changed=3,
        )
    )

    with pytest.raises(GitHubSafetyError):
        asyncio.run(
            service.commit_changes(
                "akmal/zargar",
                "zargar/test-dev001",
                [FileChange("one.txt", "1"), FileChange("two.txt", "2")],
                "test",
            )
        )


def test_telegram_dev_task_command_routing():
    db, company = setup_db()
    agent = FakeDeveloperAgent()
    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123}, developer_agent=agent)

    response = asyncio.run(bot.handle_message(123, '/dev_task akmal/zargar "Add README setup section"', chat_id="10", message_id="20"))

    assert response == ["Draft PR Created"]
    assert agent.requests[0].repo == "akmal/zargar"
    assert agent.requests[0].task_text == "Add README setup section"
    assert agent.requests[0].requester_id == "123"
    assert agent.requests[0].telegram_chat_id == "10"
    assert agent.requests[0].telegram_message_id == "20"


def request(company_id, task_text="Add README setup section", requester_id="123"):
    return DeveloperAgentInput(
        company_id=company_id,
        repo="akmal/zargar",
        task_text=task_text,
        requester_id=requester_id,
        telegram_chat_id="10",
        telegram_message_id="20",
    )


def session_factory(db):
    @contextmanager
    def factory():
        yield db

    return factory


class MockGitHub:
    def __init__(self):
        self.created_branches = []
        self.created_prs = []
        self.merges = []

    def verify_repository_allowed(self, repo):
        if repo != "akmal/zargar":
            raise GitHubSafetyError("Repository is not allowlisted")

    async def get_default_branch(self, repo):
        return "main"

    async def create_branch(self, repo, branch, from_branch=None):
        validate_zargar_branch(branch)
        validate_not_protected_branch(branch, from_branch)
        self.created_branches.append(branch)
        return {"branch": branch}

    async def commit_changes(self, repo, branch, changes, message):
        validate_zargar_branch(branch)
        for change in changes:
            validate_safe_path(change.path)
        return [{"path": change.path} for change in changes]

    async def push_branch(self, repo, branch):
        validate_zargar_branch(branch)
        return {"status": "branch-updated"}

    async def create_draft_pr(self, repo, branch, title, body, base_branch=None):
        validate_zargar_branch(branch)
        self.created_prs.append({"repo": repo, "branch": branch, "title": title, "body": body, "draft": True})
        return {"html_url": "https://github.example/pull/1"}


class CapturingGitHubService(GitHubService):
    def __init__(self, settings):
        super().__init__(settings)
        self.deleted_refs = []

    async def _request(self, method, path, **kwargs):
        if method == "DELETE":
            self.deleted_refs.append(path)
        return {}


class FakeDeveloperAgent:
    def __init__(self):
        self.requests = []

    async def run(self, db, request):
        self.requests.append(request)
        return {"status": "pr_created", "task_id": "task", "message": "Draft PR Created"}
