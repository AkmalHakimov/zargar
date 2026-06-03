import os
from urllib.parse import quote
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.services.github_service import GitHubSafetyError, validate_not_protected_branch, validate_safe_path, validate_zargar_branch

WORKSPACE_ROOT = Path("/tmp/zargar-workspaces")
KEY_FILE_CANDIDATES = [
    "README.md",
    "README",
    "package.json",
    "pyproject.toml",
    "index.html",
    "src/App.jsx",
    "src/App.tsx",
    "src/main.jsx",
    "src/main.tsx",
    "app/page.tsx",
    "app/page.jsx",
    "pages/index.tsx",
    "pages/index.jsx",
]
KEY_DIRS = ["src", "app", "pages", "components", "public"]
SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".sass", ".html", ".vue", ".svelte", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".sql"}


@dataclass
class RepoSummary:
    path: Path
    project_type: str
    key_files: dict[str, str]
    tree: list[str]
    default_branch: str
    branch: str


@dataclass
class ChangedFile:
    path: str
    status: str
    added: int = 0
    deleted: int = 0


class LocalWorkspaceService:
    def __init__(self, settings: Settings, root: Path = WORKSPACE_ROOT):
        self.settings = settings
        self.root = root

    def create_workspace(self, task_id: str) -> Path:
        workspace = self.root / task_id
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def clone_repo(self, repo: str, task_id: str) -> Path:
        if not self.settings.github_token:
            raise RuntimeError("GITHUB_TOKEN is required to clone repositories.")
        workspace = self.create_workspace(task_id)
        url = github_clone_url(repo, self.settings.github_token)
        clean_url = f"https://github.com/{repo}.git"
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        self._git(["clone", url, str(workspace)], cwd=None, env=env)
        self._git(["remote", "set-url", "origin", clean_url], cwd=workspace)
        return workspace

    def checkout_default_branch(self, workspace: Path, default_branch: str) -> None:
        self._git(["checkout", default_branch], cwd=workspace)

    def create_branch(self, workspace: Path, branch: str, default_branch: str) -> None:
        validate_zargar_branch(branch)
        validate_not_protected_branch(branch, default_branch)
        self._git(["checkout", "-B", branch], cwd=workspace)

    def build_repo_summary(self, workspace: Path, default_branch: str, branch: str) -> RepoSummary:
        return RepoSummary(
            path=workspace,
            project_type=detect_project_type(workspace),
            key_files=read_key_files(workspace),
            tree=inspect_tree(workspace),
            default_branch=default_branch,
            branch=branch,
        )

    def current_branch(self, workspace: Path) -> str:
        return self._git(["branch", "--show-current"], cwd=workspace).strip()

    def changed_files(self, workspace: Path) -> list[ChangedFile]:
        files: dict[str, ChangedFile] = {}
        status_output = self._git(["status", "--porcelain"], cwd=workspace)
        for line in status_output.splitlines():
            if not line:
                continue
            status = line[:2].strip() or "M"
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            full_path = workspace / path
            if status == "??" and full_path.exists() and full_path.is_dir():
                for child in sorted(full_path.rglob("*")):
                    if child.is_file() and ".git" not in child.parts:
                        rel = child.relative_to(workspace).as_posix()
                        files[rel] = ChangedFile(path=rel, status=status)
                continue
            files[path] = ChangedFile(path=path, status=status)
        numstat = self._git(["diff", "--numstat"], cwd=workspace)
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, deleted, path = parts
            item = files.setdefault(path, ChangedFile(path=path, status="M"))
            item.added += int(added) if added.isdigit() else 0
            item.deleted += int(deleted) if deleted.isdigit() else 0
        for item in files.values():
            full_path = workspace / item.path
            if item.status == "??" and full_path.exists() and full_path.is_file():
                item.added = len(full_path.read_text(encoding="utf-8", errors="ignore").splitlines())
        return list(files.values())

    def git_status(self, workspace: Path) -> str:
        return self._git(["status", "--porcelain"], cwd=workspace)

    def diff_stat(self, workspace: Path) -> str:
        return self._git(["diff", "--stat"], cwd=workspace)

    def diff_numstat(self, workspace: Path) -> str:
        return self._git(["diff", "--numstat"], cwd=workspace)

    def diff_text(self, workspace: Path) -> str:
        return self._git(["diff", "--", "."], cwd=workspace)

    def enforce_diff_safety(self, workspace: Path, default_branch: str) -> list[ChangedFile]:
        branch = self.current_branch(workspace)
        validate_zargar_branch(branch)
        validate_not_protected_branch(branch, default_branch)
        changed = self.changed_files(workspace)
        forbidden = []
        for item in changed:
            try:
                validate_safe_path(item.path)
            except GitHubSafetyError as exc:
                forbidden.append(f"{item.path}: {exc}")
        if forbidden:
            raise GitHubSafetyError(diff_budget_error("Forbidden paths touched.", changed, self.settings, forbidden))
        if not self.settings.allow_complex_repo_tasks:
            raise GitHubSafetyError(diff_budget_error("Complex repository tasks are disabled.", changed, self.settings, []))
        if len(changed) > self.settings.max_changed_files_for_mvp:
            raise GitHubSafetyError(
                diff_budget_error(
                    f"Changed file count exceeds MAX_CHANGED_FILES_FOR_MVP={self.settings.max_changed_files_for_mvp}.",
                    changed,
                    self.settings,
                    [],
                )
            )
        source_lines = changed_source_lines(changed)
        if source_lines > self.settings.max_changed_source_lines_for_mvp:
            raise GitHubSafetyError(
                diff_budget_error(
                    f"Changed source lines exceed MAX_CHANGED_SOURCE_LINES_FOR_MVP={self.settings.max_changed_source_lines_for_mvp}.",
                    changed,
                    self.settings,
                    [],
                )
            )
        directories = touched_top_level_directories(changed)
        if len(directories) > self.settings.max_allowed_directories_for_mvp:
            raise GitHubSafetyError(
                diff_budget_error(
                    f"Touched directory count exceeds MAX_ALLOWED_DIRECTORIES_FOR_MVP={self.settings.max_allowed_directories_for_mvp}.",
                    changed,
                    self.settings,
                    [],
                )
            )
        return changed

    def commit_all(self, workspace: Path, message: str) -> None:
        self._git(["add", "-A"], cwd=workspace)
        self._git(["commit", "-m", message], cwd=workspace)

    def push_branch(self, workspace: Path, branch: str) -> None:
        validate_zargar_branch(branch)
        self._git(["push", "-u", "origin", branch], cwd=workspace)

    def _git(self, args: list[str], cwd: Path | None, env: dict | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(redact_token((result.stderr or result.stdout or "git command failed").strip(), self.settings.github_token))
        return result.stdout


def inspect_tree(workspace: Path, limit: int = 120) -> list[str]:
    paths = []
    for path in sorted(workspace.rglob("*")):
        if path.is_dir() or ".git" in path.parts:
            continue
        rel = path.relative_to(workspace).as_posix()
        paths.append(rel)
        if len(paths) >= limit:
            break
    return paths


def detect_project_type(workspace: Path) -> str:
    package_json = workspace / "package.json"
    if package_json.exists():
        text = package_json.read_text(encoding="utf-8", errors="ignore").lower()
        if "next" in text:
            return "next"
        if "vite" in text:
            return "vite"
        if "react" in text:
            return "react"
        return "node"
    if (workspace / "index.html").exists():
        return "static_html"
    if (workspace / "pyproject.toml").exists():
        return "python"
    return "unknown"


def read_key_files(workspace: Path, max_chars: int = 4000) -> dict[str, str]:
    files = {}
    for rel in KEY_FILE_CANDIDATES:
        path = workspace / rel
        if path.exists() and path.is_file():
            files[rel] = path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    for dirname in KEY_DIRS:
        directory = workspace / dirname
        if not directory.exists() or not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*"))[:20]:
            if path.is_file() and path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".md"}:
                rel = path.relative_to(workspace).as_posix()
                files.setdefault(rel, path.read_text(encoding="utf-8", errors="ignore")[:max_chars])
    return files


def github_clone_url(repo: str, token: str) -> str:
    return f"https://x-access-token:{quote(token, safe='')}@github.com/{repo}.git"


def redact_token(text: str, token: str | None) -> str:
    if not token:
        return text
    return text.replace(token, "[REDACTED]")


def changed_source_lines(changed: list[ChangedFile]) -> int:
    return sum(item.added + item.deleted for item in changed if is_source_budget_path(item.path))


def touched_top_level_directories(changed: list[ChangedFile]) -> list[str]:
    directories = []
    for item in changed:
        parts = item.path.split("/")
        directory = parts[0] if len(parts) > 1 else "."
        if directory not in directories:
            directories.append(directory)
    return directories


def diff_budget_error(reason: str, changed: list[ChangedFile], settings: Settings, forbidden: list[str]) -> str:
    source_lines = changed_source_lines(changed)
    directories = touched_top_level_directories(changed)
    forbidden_text = ", ".join(forbidden) if forbidden else "None"
    return (
        f"{reason} PR blocked before commit/push.\n"
        f"Changed file count: {len(changed)} / {settings.max_changed_files_for_mvp}\n"
        f"Changed source line count: {source_lines} / {settings.max_changed_source_lines_for_mvp}\n"
        f"Top-level directories touched: {', '.join(directories) if directories else 'None'} "
        f"({len(directories)} / {settings.max_allowed_directories_for_mvp})\n"
        f"Forbidden files: {forbidden_text}\n"
        "Draft PR created: no"
    )


def is_source_budget_path(path: str) -> bool:
    lower = path.lower()
    filename = lower.rsplit("/", 1)[-1]
    if lower.startswith((".zargar/", "docs/")) or filename in {"readme", "readme.md"}:
        return False
    if "test" in lower.split("/") or "tests" in lower.split("/"):
        return False
    if "." not in filename:
        return False
    return "." + filename.rsplit(".", 1)[-1] in SOURCE_EXTENSIONS
