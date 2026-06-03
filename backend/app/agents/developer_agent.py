import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import DeveloperTask
from app.services.github_service import FileChange, GitHubSafetyError, GitHubService
from app.services.coding_executor import CodingExecutor, CodingExecutorError
from app.services.local_workspace import ChangedFile, LocalWorkspaceService
from app.services.developer_validation import DeveloperValidationResult, validate_developer_diff, validation_markdown
from app.services.test_runner import TestRunner, TestRunSummary

ACTIVE_TASK_STATUSES = {"pending", "planning", "coding", "tests_running", "tests_failed", "pr_created", "needs_clarification"}
PROTECTED_BRANCH_NAMES = {"main", "master", "develop", "production"}


@dataclass
class DeveloperAgentInput:
    company_id: UUID
    repo: str
    task_text: str
    requester_id: str
    telegram_chat_id: str | None = None
    telegram_message_id: str | None = None
    execute_local: bool = False
    engine: str | None = None


class DeveloperAgent:
    def __init__(
        self,
        github: GitHubService | None = None,
        settings: Settings | None = None,
        allowed_requester_ids: set[int] | None = None,
        workspace: LocalWorkspaceService | None = None,
        coding_executor: CodingExecutor | None = None,
        test_runner: TestRunner | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.github = github or GitHubService(self.settings)
        self.allowed_requester_ids = allowed_requester_ids
        self.workspace = workspace or LocalWorkspaceService(self.settings)
        self.coding_executor = coding_executor or CodingExecutor(self.settings)
        self.test_runner = test_runner or TestRunner()

    async def run(self, db: Session, request: DeveloperAgentInput) -> dict:
        if not self.is_authorized(request.requester_id):
            return {"status": "unauthorized", "message": "You are not authorized to use the Zargar senior developer agent."}

        try:
            self.github.verify_repository_allowed(request.repo)
        except GitHubSafetyError as exc:
            task = self.create_task(db, request, status="failed", error=str(exc))
            db.commit()
            return {"status": "failed", "task_id": str(task.id), "message": str(exc)}

        dangerous_intent = detect_dangerous_developer_intent(request.task_text)
        if dangerous_intent:
            task = self.create_task(db, request, status="failed", error=dangerous_intent)
            append_audit(task, "blocked_dangerous_intent", {"reason": dangerous_intent})
            db.commit()
            return {"status": "failed", "task_id": str(task.id), "message": dangerous_intent}

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
        if should_execute_local(self.settings, request):
            return await self.run_local_workspace(db, task, request, plan)

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

    async def run_local_workspace(self, db: Session, task: DeveloperTask, request: DeveloperAgentInput, plan: list[str]) -> dict:
        original_engine = self.settings.developer_coding_engine
        if request.engine:
            self.settings.developer_coding_engine = request.engine
        try:
            task.status = "coding"
            default_branch = await self.github.get_default_branch(request.repo)
            workspace_path = self.workspace.clone_repo(request.repo, str(task.id))
            append_audit(task, "workspace_created", {"path": str(workspace_path)})
            self.workspace.checkout_default_branch(workspace_path, default_branch)
            self.workspace.create_branch(workspace_path, task.branch, default_branch)
            repo_summary = self.workspace.build_repo_summary(workspace_path, default_branch, task.branch)
            append_audit(task, "repo_context_built", {"project_type": repo_summary.project_type, "files": repo_summary.tree[:40]})
            try:
                coding_result = self.coding_executor.execute(request.task_text, repo_summary, company_context="")
            except CodingExecutorError as exc:
                append_audit(task, "coding_failed", {"error": str(exc), "debug": exc.debug_log})
                raise
            append_audit(
                task,
                "coding_completed",
                {"changed_files": coding_result.changed_files, "summary": coding_result.summary, "debug": coding_result.debug_log},
            )
            changed = self.workspace.enforce_diff_safety(workspace_path, default_branch)
            if not changed:
                raise RuntimeError("Coding executor produced no repository changes.")
            git_debug = collect_git_debug(self.workspace, workspace_path)
            append_audit(task, "diff_inspected", git_debug)
            try:
                validation_result = validate_developer_diff(request.task_text, changed, self.settings, diff_text=git_debug["diff"])
            except Exception as exc:
                append_audit(task, "validation_failed", {"reason": str(exc), **git_debug})
                raise
            append_audit(task, "validation_passed", {"metrics": validation_result.__dict__})
            task.status = "tests_running"
            test_result = self.test_runner.run(workspace_path)
            append_audit(task, "tests_completed", {"summary": test_result.summary_text(), "passed": test_result.passed})
            commit_message = f"Implement {task_public_id(task)}"
            self.workspace.commit_all(workspace_path, commit_message)
            self.workspace.push_branch(workspace_path, task.branch)
            pr = await self.github.create_draft_pr(
                request.repo,
                task.branch,
                title=pr_title(request.task_text),
                body=local_pr_body(task, request, coding_result.summary, changed, test_result, validation_result),
                base_branch=default_branch,
            )
            task.pr_url = pr.get("html_url") or pr.get("url")
            task.status = "tests_failed" if test_result.ran and not test_result.passed else "pr_created"
            task.summary = local_summary(coding_result.summary, changed, test_result, validation_result)
            append_audit(task, "draft_pr_created", {"pr_url": task.pr_url, "status": task.status})
            db.commit()
            return format_local_pr_response(task, changed, test_result, validation_result)
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            append_audit(task, "failed", {"error": str(exc)})
            db.commit()
            return {"status": "failed", "task_id": str(task.id), "message": str(exc)}
        finally:
            self.settings.developer_coding_engine = original_engine

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
    action_words = {"add", "fix", "update", "remove", "create", "implement", "rename", "document", "build", "complete", "rebuild", "redesign"}
    has_action = any(lowered.startswith(word + " ") or f" {word} " in lowered for word in action_words)
    has_target = len(normalized.split()) >= 4
    if not has_action or not has_target:
        return [
            "What concrete code or documentation change should be made?",
            "What acceptance check should pass after the change?",
        ]
    return []


def detect_dangerous_developer_intent(task_text: str) -> str | None:
    lowered = " ".join(task_text.lower().split())
    dangerous_phrases = {
        "merge to main": "Direct merge to main is forbidden.",
        "merge into main": "Direct merge to main is forbidden.",
        "merge the pr": "Merging pull requests is forbidden.",
        "auto merge": "Auto-merge is forbidden.",
        "deploy production": "Production deployment is forbidden.",
        "deploy to production": "Production deployment is forbidden.",
        "production deploy": "Production deployment is forbidden.",
        "release to production": "Production deployment is forbidden.",
        "publish package": "Package publishing/release changes are forbidden unless explicitly reviewed outside the MVP.",
        "npm publish": "Package publishing/release changes are forbidden unless explicitly reviewed outside the MVP.",
    }
    for phrase, reason in dangerous_phrases.items():
        if phrase in lowered:
            return reason
    return None


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


def should_execute_local(settings: Settings, request: DeveloperAgentInput) -> bool:
    return request.execute_local or settings.developer_agent_mode == "local_workspace"


def collect_git_debug(workspace: LocalWorkspaceService, workspace_path) -> dict:
    return {
        "workspace_path": str(workspace_path),
        "git_status": workspace.git_status(workspace_path),
        "git_diff_stat": workspace.diff_stat(workspace_path),
        "git_diff_numstat": workspace.diff_numstat(workspace_path),
        "diff": workspace.diff_text(workspace_path),
    }


def validate_meaningful_code_changes(task_text: str, changed_files: list[ChangedFile]) -> None:
    validate_developer_diff(task_text, changed_files)


def is_readme_path(path: str) -> bool:
    return path.lower().rsplit("/", 1)[-1] in {"readme.md", "readme"}


def is_documentation_only_path(path: str) -> bool:
    lower = path.lower()
    return is_readme_path(lower) or lower.startswith(".zargar/") or lower.startswith("docs/")


def is_frontend_or_app_task(lower_task_text: str) -> bool:
    terms = {
        "ui",
        "frontend",
        "front-end",
        "react",
        "vite",
        "next",
        "homepage",
        "home page",
        "navigation",
        "blog",
        "layout",
        "profile section",
        "article",
        "css",
        "scss",
        "app.jsx",
        "index.scss",
    }
    return any(term in lower_task_text for term in terms)


def frontend_required_prefix(lower_task_text: str) -> str | None:
    if "blog-front/" in lower_task_text or "blog-front/src/" in lower_task_text:
        return "blog-front/src/"
    return None


def is_app_source_path(path: str) -> bool:
    lower = path.lower()
    if lower.endswith((".jsx", ".tsx", ".js", ".ts", ".css", ".scss", ".html")):
        source_prefixes = ("src/", "app/", "pages/", "components/", "public/", "blog-front/src/")
        return lower.startswith(source_prefixes) or lower == "index.html"
    return False


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


def local_pr_body(
    task: DeveloperTask,
    request: DeveloperAgentInput,
    summary: str,
    changed_files,
    test_result: TestRunSummary,
    validation_result: DeveloperValidationResult,
) -> str:
    files = "\n".join(f"- {item.path} (+{item.added}/-{item.deleted})" for item in changed_files)
    source_files = "\n".join(f"- {path}" for path in validation_result.source_files) or "- None"
    return (
        f"Task: {task_public_id(task)}\n\n"
        f"Requested from Telegram user `{request.requester_id}`.\n\n"
        "## Summary\n\n"
        f"- {summary}\n\n"
        "## Changed Files\n\n"
        f"{files}\n\n"
        "## Changed Source Files\n\n"
        f"{source_files}\n\n"
        "## Source Depth Validation\n\n"
        f"{validation_markdown(validation_result)}\n\n"
        "## Tests / Build\n\n"
        f"{test_result.summary_text()}\n\n"
        "## Known Limitations / Human Review\n\n"
        "- Draft PR requires human review before merge.\n"
        "- Verify product fit, visual quality, and any environment-specific build behavior manually.\n\n"
        "## Safety Evidence\n\n"
        "- Branch uses `zargar/*` namespace.\n"
        "- Draft PR created by default.\n"
        "- Diff inspected before commit and push.\n"
        "- No merge, approval, deploy, force push, secrets, or branch protection changes performed.\n"
    )


def local_summary(summary: str, changed_files, test_result: TestRunSummary, validation_result: DeveloperValidationResult) -> str:
    return (
        f"{summary}\n"
        f"Changed files: {', '.join(item.path for item in changed_files)}\n"
        f"Validation: {validation_result.validation_summary}\n"
        f"Tests/build: {test_result.summary_text()}"
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


def format_local_pr_response(task: DeveloperTask, changed_files, test_result: TestRunSummary, validation_result: DeveloperValidationResult | None = None) -> dict:
    files = "\n".join(f"- {item.path}" for item in changed_files)
    validation = validation_result.validation_summary if validation_result else "Validation passed."
    return {
        "status": task.status,
        "task_id": str(task.id),
        "message": (
            f"Draft PR Created\n\n"
            f"Task: {task_public_id(task)}\n\n"
            f"Repository:\n{task.repo}\n\n"
            f"Branch:\n{task.branch}\n\n"
            f"Changed files:\n{files}\n\n"
            f"Validation:\n{validation}\n\n"
            f"Tests/build:\n{test_result.summary_text()}\n\n"
            f"PR URL: {task.pr_url}\n\n"
            "Human review required.\n\n"
            "No merge or deploy performed."
        ),
    }
