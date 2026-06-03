import base64
import fnmatch
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.config import Settings

PROTECTED_BRANCHES = {"main", "master", "develop", "production"}
FORBIDDEN_PATH_PATTERNS = [
    ".env",
    ".env.*",
    ".envrc",
    "secrets.*",
    "credentials*",
    "*.key",
    "*private_key*",
    "*secret_key*",
    "*.pem",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_ed25519",
    "*/secrets/*",
    "*/.aws/*",
    "*/.ssh/*",
]
FORBIDDEN_DIR_PREFIXES = [".github/workflows/", "terraform/", "infra/", "deployment/"]


class GitHubSafetyError(ValueError):
    pass


@dataclass
class FileChange:
    path: str
    content: str
    operation: str = "upsert"


class GitHubService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token = settings.github_token
        self.allowed_repos = parse_allowed_repos(settings.github_allowed_repos)
        self.base_url = "https://api.github.com"

    def verify_repository_allowed(self, repo: str) -> None:
        validate_repo_name(repo)
        if repo not in self.allowed_repos:
            raise GitHubSafetyError(f"Repository is not allowlisted: {repo}")

    async def get_repository_metadata(self, repo: str) -> dict:
        self.verify_repository_allowed(repo)
        return await self._request("GET", f"/repos/{repo}")

    async def get_default_branch(self, repo: str) -> str:
        metadata = await self.get_repository_metadata(repo)
        return metadata.get("default_branch") or "main"

    async def create_branch(self, repo: str, branch: str, from_branch: str | None = None) -> dict:
        self.verify_repository_allowed(repo)
        validate_zargar_branch(branch)
        default_branch = from_branch or await self.get_default_branch(repo)
        validate_not_protected_branch(branch, default_branch)
        ref = await self._request("GET", f"/repos/{repo}/git/ref/heads/{default_branch}")
        sha = ref["object"]["sha"]
        return await self._request("POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    async def read_file(self, repo: str, path: str, branch: str | None = None) -> dict:
        self.verify_repository_allowed(repo)
        validate_safe_path(path)
        params = {"ref": branch} if branch else None
        return await self._request("GET", f"/repos/{repo}/contents/{path}", params=params)

    async def create_file(self, repo: str, branch: str, path: str, content: str, message: str) -> dict:
        self.verify_repository_allowed(repo)
        validate_zargar_branch(branch)
        validate_safe_path(path)
        body = {"branch": branch, "message": message, "content": encode_content(content)}
        return await self._request("PUT", f"/repos/{repo}/contents/{path}", json=body)

    async def update_file(self, repo: str, branch: str, path: str, content: str, sha: str, message: str) -> dict:
        self.verify_repository_allowed(repo)
        validate_zargar_branch(branch)
        validate_safe_path(path)
        body = {"branch": branch, "message": message, "content": encode_content(content), "sha": sha}
        return await self._request("PUT", f"/repos/{repo}/contents/{path}", json=body)

    async def commit_changes(self, repo: str, branch: str, changes: list[FileChange], message: str) -> list[dict]:
        self.verify_repository_allowed(repo)
        validate_zargar_branch(branch)
        validate_change_limits(
            changes,
            max_files=self.settings.github_max_files_changed,
            max_lines=self.settings.github_max_lines_changed,
            max_new_files=self.settings.github_max_new_files,
        )
        results = []
        for change in changes:
            validate_safe_path(change.path)
            if change.operation != "upsert":
                raise GitHubSafetyError("Only upsert file changes are supported in the MVP.")
            results.append(await self.create_file(repo, branch, change.path, change.content, message))
        return results

    async def push_branch(self, repo: str, branch: str) -> dict:
        self.verify_repository_allowed(repo)
        validate_zargar_branch(branch)
        return {"status": "branch-updated", "repo": repo, "branch": branch}

    async def create_draft_pr(self, repo: str, branch: str, title: str, body: str, base_branch: str | None = None) -> dict:
        self.verify_repository_allowed(repo)
        if not self.settings.github_draft_prs_only:
            raise GitHubSafetyError("Only draft PRs are supported by the developer agent MVP.")
        validate_zargar_branch(branch)
        base = base_branch or await self.get_default_branch(repo)
        validate_not_protected_branch(branch, base)
        payload = {"title": title, "head": branch, "base": base, "body": body, "draft": True}
        return await self._request("POST", f"/repos/{repo}/pulls", json=payload)

    async def update_pr(self, repo: str, pr_number: int, body: str) -> dict:
        self.verify_repository_allowed(repo)
        return await self._request("PATCH", f"/repos/{repo}/pulls/{pr_number}", json={"body": body})

    async def comment_on_pr(self, repo: str, pr_number: int, comment: str) -> dict:
        self.verify_repository_allowed(repo)
        return await self._request("POST", f"/repos/{repo}/issues/{pr_number}/comments", json={"body": comment})

    async def read_pr_status(self, repo: str, pr_number: int) -> dict:
        self.verify_repository_allowed(repo)
        return await self._request("GET", f"/repos/{repo}/pulls/{pr_number}")

    async def cleanup_stale_zargar_branches(self, repo: str, branches: list[dict], now: datetime | None = None) -> list[str]:
        self.verify_repository_allowed(repo)
        now = now or datetime.now(timezone.utc)
        deleted = []
        for branch in branches:
            name = branch.get("name", "")
            if not name.startswith("zargar/"):
                continue
            updated_at = parse_datetime(branch.get("updated_at"))
            if not updated_at or now - updated_at < timedelta(days=self.settings.branch_retention_days):
                continue
            await self._request("DELETE", f"/repos/{repo}/git/refs/heads/{name}")
            deleted.append(name)
        return deleted

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN is required for GitHub operations.")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            response.raise_for_status()
            if response.content:
                return response.json()
            return {}


def parse_allowed_repos(raw: str | None) -> set[str]:
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def validate_repo_name(repo: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise GitHubSafetyError(f"Invalid repository name: {repo}")


def validate_zargar_branch(branch: str) -> None:
    if not branch.startswith("zargar/"):
        raise GitHubSafetyError("Developer agent branches must use the zargar/* namespace.")
    if branch.count("/") != 1:
        raise GitHubSafetyError("Nested developer branches are not allowed.")
    if branch.split("/", 1)[1] in PROTECTED_BRANCHES:
        raise GitHubSafetyError("Protected branch names are not allowed.")


def validate_not_protected_branch(branch: str, default_branch: str | None = None) -> None:
    protected = set(PROTECTED_BRANCHES)
    if default_branch:
        protected.add(default_branch)
    if branch in protected or branch.replace("refs/heads/", "") in protected:
        raise GitHubSafetyError("Direct pushes to protected/default branches are forbidden.")


def validate_safe_path(path: str, ci_cd_allowed: bool = False) -> None:
    normalized = path.strip().lstrip("/")
    if ".." in normalized.split("/"):
        raise GitHubSafetyError(f"Unsafe path is forbidden: {path}")
    lower = normalized.lower()
    for pattern in FORBIDDEN_PATH_PATTERNS:
        if fnmatch.fnmatch(lower, pattern.lower()) or fnmatch.fnmatch(lower.split("/")[-1], pattern.lower()):
            raise GitHubSafetyError(f"Forbidden file path: {path}")
    if not ci_cd_allowed:
        for prefix in FORBIDDEN_DIR_PREFIXES:
            if lower.startswith(prefix):
                raise GitHubSafetyError(f"Forbidden directory path: {path}")


def validate_change_limits(changes: list[FileChange], max_files: int, max_lines: int, max_new_files: int) -> None:
    if len(changes) > max_files:
        raise GitHubSafetyError(f"Changed file count exceeds limit: {len(changes)} / {max_files}. Draft PR blocked.")
    new_files = sum(1 for change in changes if change.operation == "upsert")
    if new_files > max_new_files:
        raise GitHubSafetyError(f"New file count exceeds limit: {new_files} / {max_new_files}. Draft PR blocked.")
    lines = sum(len(change.content.splitlines()) for change in changes)
    if lines > max_lines:
        raise GitHubSafetyError(f"Changed line count exceeds limit: {lines} / {max_lines}. Draft PR blocked.")


def encode_content(content: str) -> str:
    return base64.b64encode(content.encode("utf-8")).decode("ascii")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
