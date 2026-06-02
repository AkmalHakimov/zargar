import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import DeveloperTask
from app.services.github_service import FileChange, GitHubSafetyError, GitHubService

ACTIVE_TASK_STATUSES = {"pending", "planning", "coding", "pr_created", "needs_clarification"}
PROTECTED_BRANCH_NAMES = {"main", "master", "develop", "production"}


@dataclass
class DeveloperAgentInput:
    company_id: UUID
    repo: str
    task_text: str
    requester_id: str
    telegram_chat_id: str | None = None
    telegram_message_id: str | None = None


class DeveloperAgent:
    def __init__(
        self,
        github: GitHubService | None = None,
        settings: Settings | None = None,
        allowed_requester_ids: set[int] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.github = github or GitHubService(self.settings)
        self.allowed_requester_ids = allowed_requester_ids

    async def run(self, db: Session, request: DeveloperAgentInput) -> dict:
        if not self.is_authorized(request.requester_id):
            return {"status": "unauthorized", "message": "You are not authorized to use the Zargar senior developer agent."}

        try:
            self.github.verify_repository_allowed(request.repo)
        except GitHubSafetyError as exc:
            task = self.create_task(db, request, status="failed", error=str(exc))
            db.commit()
            return {"status": "failed", "task_id": str(task.id), "message": str(exc)}

        existing = find_active_task(db, request.company_id, request.repo, request.task_text, request.requester_id)
        if existing and existing.branch:
            append_audit(existing, "branch_reused", {"branch": existing.branch})
            db.commit()
            return format_existing_task_response(existing)

        task = self.create_task(db, request, status="planning")
        db.commit()

        ambiguity = detect_ambiguity(request.task_text)
        if ambiguity:
            task.status = "needs_clarification"
            task.summary = "Task requires clarification."
            task.error = "\n".join(ambiguity)
            append_audit(task, "needs_clarification", {"questions": ambiguity})
            db.commit()
            return format_clarification_response(task, ambiguity)

        plan = implementation_plan(request.task_text)
        branch = branch_name(request.task_text, str(task.id))
        task.branch = branch
        append_audit(task, "plan_created", {"plan": plan})

        try:
            task.status = "coding"
            default_branch = await self.github.get_default_branch(request.repo)
            if default_branch in PROTECTED_BRANCH_NAMES:
                append_audit(task, "default_branch_identified", {"default_branch": default_branch})
            await self.github.create_branch(request.repo, branch, from_branch=default_branch)
            append_audit(task, "branch_created", {"branch": branch, "from": default_branch})
            change = FileChange(path=f".zargar/developer-tasks/{task_public_id(task)}.md", content=task_audit_file(task, request, plan))
            await self.github.commit_changes(request.repo, branch, [change], message=f"Add developer task audit {task_public_id(task)}")
            await self.github.push_branch(request.repo, branch)
            pr = await self.github.create_draft_pr(request.repo, branch, title=pr_title(request.task_text), body=pr_body(task, request, plan), base_branch=default_branch)
            task.pr_url = pr.get("html_url") or pr.get("url")
            task.status = "pr_created"
            task.summary = "Created draft PR with implementation plan, safety notes, and audit trail. Human review required."
            append_audit(task, "draft_pr_created", {"pr_url": task.pr_url})
            db.commit()
            return format_pr_created_response(task, plan)
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            append_audit(task, "failed", {"error": str(exc)})
            db.commit()
            return {"status": "failed", "task_id": str(task.id), "message": str(exc)}

    def create_task(self, db: Session, request: DeveloperAgentInput, status: str, error: str | None = None) -> DeveloperTask:
        task = DeveloperTask(
            company_id=request.company_id,
            repo=request.repo,
            status=status,
            task_text=request.task_text,
            requester_id=str(request.requester_id),
            telegram_chat_id=str(request.telegram_chat_id) if request.telegram_chat_id is not None else None,
            telegram_message_id=str(request.telegram_message_id) if request.telegram_message_id is not None else None,
            error=error,
            audit_log=[],
        )
        append_audit(task, "task_created", {"status": status})
        db.add(task)
        db.flush()
        return task

    def is_authorized(self, requester_id: str) -> bool:
        if self.allowed_requester_ids is None:
            return True
        try:
            return int(requester_id) in self.allowed_requester_ids
        except ValueError:
            return False


def find_active_task(db: Session, company_id: UUID, repo: str, task_text: str, requester_id: str) -> DeveloperTask | None:
    return db.scalar(
        select(DeveloperTask)
        .where(
            DeveloperTask.company_id == company_id,
            DeveloperTask.repo == repo,
            DeveloperTask.task_text == task_text,
            DeveloperTask.requester_id == str(requester_id),
            DeveloperTask.status.in_(ACTIVE_TASK_STATUSES),
        )
        .order_by(DeveloperTask.created_at.desc())
        .limit(1)
    )


def detect_ambiguity(task_text: str) -> list[str]:
    normalized = " ".join(task_text.strip().split())
    lowered = normalized.lower()
    vague = {"improve auth", "fix onboarding", "refactor backend", "make it better", "improve ui", "fix bugs"}
    if lowered in vague:
        return [
            "What exact behavior should change?",
            "Which files, endpoint, or user flow should be affected?",
        ]
    action_words = {"add", "fix", "update", "remove", "create", "implement", "rename", "document"}
    has_action = any(lowered.startswith(word + " ") or f" {word} " in lowered for word in action_words)
    has_target = len(normalized.split()) >= 4
    if not has_action or not has_target:
        return [
            "What concrete code or documentation change should be made?",
            "What acceptance check should pass after the change?",
        ]
    return []


def branch_name(task_text: str, task_id: str) -> str:
    slug = slugify(task_text)[:36].strip("-") or "developer-task"
    return f"zargar/{slug}-dev{task_id.replace('-', '')[:6]}"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug)


def implementation_plan(task_text: str) -> list[str]:
    return [
        f"Clarify scope from task: {task_text}",
        "Inspect repository README, docs, code patterns, and tests.",
        "Apply the smallest safe change on a dedicated zargar/* branch.",
        "Run relevant tests when available or explain why tests were not run.",
        "Open a draft PR for human review. No merge or deployment.",
    ]


def task_public_id(task: DeveloperTask) -> str:
    return f"DEV-{str(task.id).replace('-', '')[:6].upper()}"


def task_audit_file(task: DeveloperTask, request: DeveloperAgentInput, plan: list[str]) -> str:
    plan_lines = "\n".join(f"{index}. {item}" for index, item in enumerate(plan, start=1))
    return (
        f"# Zargar Developer Task {task_public_id(task)}\n\n"
        f"Repository: `{request.repo}`\n\n"
        f"Requester Telegram user id: `{request.requester_id}`\n\n"
        f"Source Telegram chat id: `{request.telegram_chat_id or ''}`\n\n"
        f"Source Telegram message id: `{request.telegram_message_id or ''}`\n\n"
        f"Task:\n\n{request.task_text}\n\n"
        "## Implementation Plan\n\n"
        f"{plan_lines}\n\n"
        "## Safety\n\n"
        "- Draft PR only.\n"
        "- No merge performed.\n"
        "- No production deployment.\n"
        "- No secret, infrastructure, or CI/CD changes.\n"
    )


def pr_title(task_text: str) -> str:
    return f"Zargar developer task: {task_text[:80]}"


def pr_body(task: DeveloperTask, request: DeveloperAgentInput, plan: list[str]) -> str:
    plan_lines = "\n".join(f"{index}. {item}" for index, item in enumerate(plan, start=1))
    return (
        f"Task: {task_public_id(task)}\n\n"
        f"Requested from Telegram user `{request.requester_id}`.\n\n"
        "## Summary\n\n"
        "- Added an auditable developer task file for review.\n"
        "- Preserved human control through a draft PR.\n\n"
        "## Implementation Plan\n\n"
        f"{plan_lines}\n\n"
        "## Safety Evidence\n\n"
        "- Branch uses `zargar/*` namespace.\n"
        "- Draft PR created by default.\n"
        "- No merge, approval, deploy, force push, secrets, or branch protection changes performed.\n\n"
        "## Tests\n\n"
        "- Not run by the MVP developer agent; no repository test command was inferred safely.\n"
    )


def append_audit(task: DeveloperTask, action: str, details: dict) -> None:
    audit = list(task.audit_log or [])
    audit.append({"at": datetime.now(timezone.utc).isoformat(), "action": action, "details": details})
    task.audit_log = audit


def format_clarification_response(task: DeveloperTask, questions: list[str]) -> dict:
    lines = ["Task requires clarification.", "Questions:"]
    lines.extend(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    return {"status": "needs_clarification", "task_id": str(task.id), "message": "\n".join(lines)}


def format_existing_task_response(task: DeveloperTask) -> dict:
    return {
        "status": task.status,
        "task_id": str(task.id),
        "message": (
            f"Task already exists: {task_public_id(task)}\n\n"
            f"Repository:\n{task.repo}\n\n"
            f"Branch:\n{task.branch}\n\n"
            f"Status:\n{task.status}"
        ),
    }


def format_pr_created_response(task: DeveloperTask, plan: list[str]) -> dict:
    plan_lines = "\n".join(f"{index}. {item}" for index, item in enumerate(plan, start=1))
    return {
        "status": "pr_created",
        "task_id": str(task.id),
        "message": (
            f"Draft PR Created\n\n"
            f"Task: {task_public_id(task)}\n\n"
            f"Repository:\n{task.repo}\n\n"
            f"Branch:\n{task.branch}\n\n"
            f"PR URL: {task.pr_url}\n\n"
            "Implementation Plan\n\n"
            f"{plan_lines}\n\n"
            "Summary:\n"
            "- Created a dedicated zargar/* branch.\n"
            "- Created a draft PR for review.\n"
            "- Human review required.\n\n"
            "No merge performed."
        ),
    }
