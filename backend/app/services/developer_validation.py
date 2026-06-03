import re
from dataclasses import dataclass

from app.config import Settings
from app.services.github_service import GitHubSafetyError
from app.services.local_workspace import ChangedFile

COMPLEX_MIN_SOURCE_FILES = 2
COMPLEX_MIN_SOURCE_LINES = 20
FRONTEND_COMPLEX_MIN_SOURCE_FILES = 2
MULTIPAGE_MIN_RELEVANT_SOURCE_FILES = 2
COMPLEX_SINGLE_FILE_MIN_SOURCE_LINES = 80

DOCUMENTATION_TASK = "documentation_task"
TINY_TEST_TASK = "tiny_test_task"
SIMPLE_CODE_TASK = "simple_code_task"
COMPLEX_IMPLEMENTATION_TASK = "complex_implementation_task"

SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".sass",
    ".html",
    ".vue",
    ".svelte",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".sql",
}
MARKDOWN_EXTENSIONS = {".md", ".mdx", ".rst", ".txt"}
METADATA_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "requirements.txt",
}
FRONTEND_ROOTS = ("blog-front/src/", "src/", "app/", "pages/", "components/", "frontend/", "public/")


@dataclass(frozen=True)
class DeveloperValidationResult:
    task_kind: str
    changed_files: int
    source_files: list[str]
    source_file_count: int
    source_changed_lines: int
    frontend_source_files: list[str]
    frontend_source_file_count: int
    documentation_files: list[str]
    validation_summary: str


def classify_developer_task(task_text: str) -> str:
    lower = normalize(task_text)
    if contains_any(
        lower,
        {
            "add this line",
            "add heading",
            "test passed",
            "small test",
            "smoke test",
            "tiny test",
        },
    ):
        return TINY_TEST_TASK
    if contains_any(
        lower,
        {
            "readme",
            "docs",
            "documentation",
            "write explanation",
            "update guide",
            "document ",
            "comments",
            "report",
        },
    ) and not contains_any(lower, complex_signals()):
        return DOCUMENTATION_TASK
    if contains_any(lower, complex_signals()):
        return COMPLEX_IMPLEMENTATION_TASK
    return SIMPLE_CODE_TASK


def validate_developer_diff(
    task_text: str,
    changed_files: list[ChangedFile],
    settings: Settings | None = None,
    diff_text: str = "",
) -> DeveloperValidationResult:
    settings = settings or Settings()
    result = build_validation_result(task_text, changed_files, diff_text)
    if result.changed_files == 0:
        raise GitHubSafetyError("Coding executor produced no repository changes.")

    if result.task_kind != DOCUMENTATION_TASK and not result.source_files:
        raise GitHubSafetyError("Implementation task produced no source-code changes.")

    if result.task_kind != DOCUMENTATION_TASK and all(is_non_source_artifact(item.path) for item in changed_files):
        raise GitHubSafetyError("Implementation task produced only README, docs, markdown, metadata, or .zargar changes.")

    if result.task_kind == DOCUMENTATION_TASK:
        return result

    if result.source_files and diff_is_comment_or_placeholder_only(diff_text):
        raise GitHubSafetyError("Source changes appear to be comment-only or placeholder-only.")

    if result.task_kind == TINY_TEST_TASK:
        return result

    if result.task_kind == SIMPLE_CODE_TASK:
        if result.source_changed_lines < 1:
            raise GitHubSafetyError("Simple code task did not change source lines.")
        return result

    validate_complex_diff(task_text, result, settings)
    return result


def build_validation_result(task_text: str, changed_files: list[ChangedFile], diff_text: str = "") -> DeveloperValidationResult:
    task_kind = classify_developer_task(task_text)
    source_files = [item.path for item in changed_files if is_source_file(item.path) and not is_test_or_audit_path(item.path)]
    frontend_source_files = [path for path in source_files if is_frontend_source_path(path)]
    docs = [item.path for item in changed_files if is_documentation_path(item.path)]
    source_changed_lines = sum(item.added + item.deleted for item in changed_files if item.path in source_files)
    return DeveloperValidationResult(
        task_kind=task_kind,
        changed_files=len(changed_files),
        source_files=source_files,
        source_file_count=len(source_files),
        source_changed_lines=source_changed_lines,
        frontend_source_files=frontend_source_files,
        frontend_source_file_count=len(frontend_source_files),
        documentation_files=docs,
        validation_summary=(
            f"{task_kind}: {len(source_files)} source file(s), "
            f"{source_changed_lines} source line(s), {len(frontend_source_files)} frontend source file(s)."
        ),
    )


def validate_complex_diff(task_text: str, result: DeveloperValidationResult, settings: Settings) -> None:
    lower = normalize(task_text)
    min_source_files = getattr(settings, "developer_complex_min_source_files", COMPLEX_MIN_SOURCE_FILES)
    min_source_lines = getattr(settings, "developer_complex_min_source_lines", COMPLEX_MIN_SOURCE_LINES)
    frontend_min_source_files = getattr(settings, "developer_frontend_complex_min_source_files", FRONTEND_COMPLEX_MIN_SOURCE_FILES)
    multipage_min_files = getattr(settings, "developer_multipage_min_relevant_source_files", MULTIPAGE_MIN_RELEVANT_SOURCE_FILES)

    if result.source_changed_lines < min_source_lines:
        raise GitHubSafetyError(
            f"Complex implementation is too shallow: {result.source_changed_lines} source line(s) changed; minimum is {min_source_lines}."
        )

    if is_frontend_task(lower):
        required_prefix = required_frontend_prefix(lower)
        if required_prefix and not any(path.startswith(required_prefix) for path in result.frontend_source_files):
            raise GitHubSafetyError(f"Frontend task requires at least one changed file under {required_prefix}.")
        if not result.frontend_source_files:
            raise GitHubSafetyError("Frontend task requires at least one changed file under a frontend source root.")
        if result.frontend_source_file_count < frontend_min_source_files:
            raise GitHubSafetyError(
                f"Complex frontend task is too shallow: {result.frontend_source_file_count} frontend source file(s) changed; "
                f"minimum is {frontend_min_source_files}."
            )

    if is_multipage_or_multisection_task(lower) and result.source_file_count < multipage_min_files:
        raise GitHubSafetyError(
            f"Multi-page or multi-section task requires at least {multipage_min_files} relevant source files."
        )

    if result.source_file_count < min_source_files:
        if result.source_file_count == 1 and result.source_changed_lines >= COMPLEX_SINGLE_FILE_MIN_SOURCE_LINES and not is_frontend_task(lower):
            return
        raise GitHubSafetyError(
            f"Complex implementation is too shallow: {result.source_file_count} source file(s) changed; minimum is {min_source_files}."
        )


def complex_signals() -> set[str]:
    return {
        "complete",
        "build",
        "rebuild",
        "redesign",
        "implement",
        "create app",
        "homepage",
        "home page",
        "blog",
        "dashboard",
        "auth",
        "api",
        "database",
        "layout",
        "page",
        "component",
        "frontend",
        "front-end",
        "backend",
        "react",
        "full flow",
        "multi-page",
        "multipage",
        "production",
        "clone",
        "inspired by",
    }


def is_frontend_task(lower_task_text: str) -> bool:
    return contains_any(
        lower_task_text,
        {
            "frontend",
            "front-end",
            "react",
            "ui",
            "page",
            "component",
            "blog",
            "homepage",
            "home page",
            "layout",
            "app",
            "css",
            "scss",
        },
    )


def required_frontend_prefix(lower_task_text: str) -> str | None:
    if "blog-front/" in lower_task_text or "blog-front/src/" in lower_task_text:
        return "blog-front/src/"
    return None


def is_multipage_or_multisection_task(lower_task_text: str) -> bool:
    return contains_any(
        lower_task_text,
        {
            "multi-page",
            "multipage",
            "multi page",
            "homepage, navigation, blog",
            "homepage + blog",
            "blog + about",
            "about/profile",
            "article layout",
            "dashboard sections",
            "complete project",
            "complete this unfinished",
        },
    )


def is_frontend_source_path(path: str) -> bool:
    lower = path.lower()
    if lower in {"index.html", "styles.css", "style.css", "main.css"}:
        return True
    if any(lower.startswith(root) for root in FRONTEND_ROOTS):
        return is_source_file(lower)
    return bool(re.match(r"^packages/[^/]+/src/.+\.[a-z0-9]+$", lower)) and is_source_file(lower)


def is_source_file(path: str) -> bool:
    lower = path.lower()
    if is_non_source_artifact(lower) or is_test_or_audit_path(lower):
        return False
    return file_extension(lower) in SOURCE_EXTENSIONS


def is_documentation_path(path: str) -> bool:
    lower = path.lower()
    return (
        lower.startswith("docs/")
        or lower.startswith(".zargar/")
        or lower.rsplit("/", 1)[-1] in {"readme", "readme.md"}
        or file_extension(lower) in MARKDOWN_EXTENSIONS
    )


def is_non_source_artifact(path: str) -> bool:
    lower = path.lower()
    filename = lower.rsplit("/", 1)[-1]
    return (
        is_documentation_path(lower)
        or filename in METADATA_FILENAMES
        or lower.endswith((".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"))
    )


def is_test_or_audit_path(path: str) -> bool:
    lower = path.lower()
    parts = lower.split("/")
    filename = parts[-1]
    return (
        lower.startswith(".zargar/")
        or "test" in parts
        or "tests" in parts
        or filename.startswith("test_")
        or filename.endswith(".test.js")
        or filename.endswith(".test.jsx")
        or filename.endswith(".test.ts")
        or filename.endswith(".test.tsx")
        or filename.endswith(".spec.js")
        or filename.endswith(".spec.jsx")
        or filename.endswith(".spec.ts")
        or filename.endswith(".spec.tsx")
    )


def diff_is_comment_or_placeholder_only(diff_text: str) -> bool:
    if not diff_text.strip():
        return False
    added_lines = []
    for line in diff_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:].strip()
        if content:
            added_lines.append(content)
    if not added_lines:
        return True
    meaningful = [line for line in added_lines if not is_comment_or_placeholder_line(line)]
    return not meaningful


def is_comment_or_placeholder_line(line: str) -> bool:
    lowered = line.lower()
    return (
        line.startswith(("#", "//", "/*", "*", "<!--"))
        or lowered in {"todo", "placeholder", "coming soon"}
        or "todo:" in lowered
        or "placeholder" in lowered
        or "zargar developer update" in lowered
    )


def validation_markdown(result: DeveloperValidationResult) -> str:
    source_files = "\n".join(f"- {path}" for path in result.source_files) or "- None"
    frontend_files = "\n".join(f"- {path}" for path in result.frontend_source_files) or "- None"
    return (
        f"Classification: `{result.task_kind}`\n\n"
        f"Source files changed: `{result.source_file_count}`\n\n"
        f"Source changed lines: `{result.source_changed_lines}`\n\n"
        "Changed source files:\n"
        f"{source_files}\n\n"
        "Frontend source files:\n"
        f"{frontend_files}\n\n"
        f"Validation result: {result.validation_summary}"
    )


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def contains_any(text: str, signals: set[str]) -> bool:
    return any(signal in text for signal in signals)


def file_extension(path: str) -> str:
    filename = path.rsplit("/", 1)[-1]
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1]
