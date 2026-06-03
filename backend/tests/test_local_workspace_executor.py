import asyncio
import sys
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.developer_agent import DeveloperAgent, DeveloperAgentInput, validate_meaningful_code_changes
from app.config import Settings
from app.db import Base
from app.models import Company
from app.services.coding_executor import CodingExecutor, CodingExecutorError
from app.services.github_service import GitHubSafetyError, validate_not_protected_branch
from app.services.local_workspace import ChangedFile, LocalWorkspaceService, github_clone_url, redact_token
from app.services.developer_validation import (
    COMPLEX_IMPLEMENTATION_TASK,
    DOCUMENTATION_TASK,
    SIMPLE_CODE_TASK,
    TINY_TEST_TASK,
    classify_developer_task,
    validate_developer_diff,
)
from app.services.test_runner import TestRunner
from tests.test_developer_agent import MockGitHub


def test_local_workspace_is_created(tmp_path):
    service = LocalWorkspaceService(Settings(), root=tmp_path)

    workspace = service.create_workspace("task-1")

    assert workspace == tmp_path / "task-1"
    assert workspace.exists()


def test_github_clone_url_uses_x_access_token_and_redacts_token():
    url = github_clone_url("owner/repo", "ghp_token/with+chars")

    assert url.startswith("https://x-access-token:")
    assert "@github.com/owner/repo.git" in url
    assert "ghp_token/with+chars" not in url
    assert redact_token("failed ghp_token/with+chars", "ghp_token/with+chars") == "failed [REDACTED]"


def test_zargar_branch_is_created_in_workspace(tmp_path):
    repo = make_static_repo(tmp_path / "repo")
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")

    service.create_branch(repo, "zargar/test-dev001", "main")

    assert service.current_branch(repo) == "zargar/test-dev001"


def test_deterministic_executor_modifies_real_static_files(tmp_path):
    repo = make_static_repo(tmp_path / "repo")
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/site-dev001", "main")
    summary = service.build_repo_summary(repo, "main", "zargar/site-dev001")

    result = CodingExecutor(Settings()).execute("Complete homepage, navigation, blog page, and about section", summary)

    assert "index.html" in result.changed_files
    assert "styles.css" in result.changed_files
    assert "Personal Blog" in (repo / "index.html").read_text()
    assert "Blog" in (repo / "index.html").read_text()


def test_deterministic_executor_modifies_explicit_nested_react_file(tmp_path):
    repo = tmp_path / "repo"
    (repo / "blog-front" / "src").mkdir(parents=True)
    (repo / "blog-front" / "src" / "App.jsx").write_text("export default function App(){return <div>Old</div>}")
    (repo / "blog-front" / "src" / "index.scss").write_text("body { margin: 0; }\n")
    init_git(repo)
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/nested-dev001", "main")
    summary = service.build_repo_summary(repo, "main", "zargar/nested-dev001")

    result = CodingExecutor(Settings()).execute(
        "Modify blog-front/src/App.jsx and blog-front/src/index.scss. Replace the current React app with a clean minimal personal blog layout: homepage hero, top navigation, blog index with three sample posts, about/profile section, and article preview section. Use simple responsive CSS in blog-front/src/index.scss. Do not modify README.md. The task is incomplete unless blog-front/src/App.jsx changes.",
        summary,
    )

    assert "blog-front/src/App.jsx" in result.changed_files
    assert "blog-front/src/index.scss" in result.changed_files
    app_text = (repo / "blog-front" / "src" / "App.jsx").read_text()
    css_text = (repo / "blog-front" / "src" / "index.scss").read_text()
    assert "top-nav" in app_text
    assert "Latest posts" in app_text
    assert "Profile" in app_text
    assert ".post-grid" in css_text
    assert service.enforce_diff_safety(repo, "main")


def test_codex_cli_executor_runs_in_workspace_and_detects_changes(tmp_path):
    repo = make_react_repo(tmp_path / "repo")
    (repo / "src" / "index.scss").write_text("body { margin: 0; }\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add styles"], cwd=repo, check=True, capture_output=True)
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/codex-dev001", "main")
    summary = service.build_repo_summary(repo, "main", "zargar/codex-dev001")
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "echo fake codex cwd=$PWD\n"
        "echo fake codex args=$*\n"
        "cat >/dev/null\n"
        "printf '\\nexport const CodexChanged = () => <p>Changed by Codex</p>;\\n' >> src/App.jsx\n"
        "printf '\\n.codex-changed { color: #111; }\\n' >> src/index.scss\n"
    )
    fake_codex.chmod(0o755)

    result = CodingExecutor(Settings(developer_coding_engine="codex_cli", codex_cli_path=str(fake_codex))).execute(
        "Complete React homepage and styles",
        summary,
    )

    assert result.changed_files == ["src/App.jsx", "src/index.scss"]
    assert result.debug_log["cwd"] == str(repo)
    assert result.debug_log["command"][:2] == [str(fake_codex), "exec"]
    assert "--cd" in result.debug_log["command"]
    assert str(repo) in result.debug_log["command"]
    assert result.debug_log["exit_code"] == 0
    assert "src/App.jsx" in result.debug_log["git_status_after"]
    assert "src/index.scss" in result.debug_log["git_diff_stat_after"]
    assert "Changed by Codex" in (repo / "src" / "App.jsx").read_text()


def test_codex_cli_no_diff_error_includes_debug_output(tmp_path):
    repo = make_react_repo(tmp_path / "repo")
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/codex-empty-dev001", "main")
    summary = service.build_repo_summary(repo, "main", "zargar/codex-empty-dev001")
    fake_codex = tmp_path / "fake-codex-empty"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "echo no changes stdout\n"
        "echo no changes stderr >&2\n"
        "cat >/dev/null\n"
    )
    fake_codex.chmod(0o755)

    with pytest.raises(CodingExecutorError, match="no repository changes") as exc:
        CodingExecutor(Settings(developer_coding_engine="codex_cli", codex_cli_path=str(fake_codex))).execute(
            "Complete React homepage and styles",
            summary,
        )

    assert "no changes stdout" in str(exc.value)
    assert "no changes stderr" in str(exc.value)
    assert exc.value.debug_log["cwd"] == str(repo)
    assert exc.value.debug_log["git_status_before"] == ""
    assert exc.value.debug_log["git_status_after"] == ""


def test_ui_task_does_not_fall_back_to_readme(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\n")
    init_git(repo)
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/ui-dev001", "main")
    summary = service.build_repo_summary(repo, "main", "zargar/ui-dev001")

    with pytest.raises(RuntimeError, match="safe app source files"):
        CodingExecutor(Settings()).execute("Build a React homepage and blog layout", summary)

    assert "Zargar Developer Update" not in (repo / "README.md").read_text()


def test_no_changes_fail_validation():
    with pytest.raises(GitHubSafetyError, match="no repository changes"):
        validate_developer_diff("Fix button label", [])


def test_task_complexity_classification():
    assert classify_developer_task("Update README setup guide") == DOCUMENTATION_TASK
    assert classify_developer_task('Add heading "Zargar test passed"') == TINY_TEST_TASK
    assert classify_developer_task("Fix button label in header") == SIMPLE_CODE_TASK
    assert classify_developer_task("Complete React blog homepage and about page") == COMPLEX_IMPLEMENTATION_TASK


def test_meaningful_change_validation_rejects_readme_only_for_implementation_task():
    with pytest.raises(GitHubSafetyError, match="no source-code changes"):
        validate_meaningful_code_changes(
            "Build a React homepage",
            [ChangedFile(path="README.md", status="M", added=3, deleted=0)],
        )


def test_meaningful_change_validation_rejects_docs_only_changes():
    with pytest.raises(GitHubSafetyError, match="no source-code changes"):
        validate_meaningful_code_changes(
            "Update backend behavior",
            [ChangedFile(path="docs/plan.md", status="M", added=3, deleted=0)],
        )


def test_zargar_only_changes_fail_for_implementation_task():
    with pytest.raises(GitHubSafetyError, match="no source-code changes"):
        validate_developer_diff(
            "Implement auth login flow",
            [ChangedFile(path=".zargar/developer-tasks/DEV-123.md", status="A", added=10, deleted=0)],
        )


def test_markdown_only_changes_fail_for_implementation_task():
    with pytest.raises(GitHubSafetyError, match="no source-code changes"):
        validate_developer_diff(
            "Build API pagination",
            [ChangedFile(path="notes.md", status="M", added=10, deleted=0)],
        )


def test_docs_only_passes_for_explicit_documentation_task():
    result = validate_developer_diff(
        "Update README setup guide",
        [ChangedFile(path="README.md", status="M", added=10, deleted=2)],
    )

    assert result.task_kind == DOCUMENTATION_TASK


def test_simple_code_change_passes_for_simple_code_task():
    result = validate_developer_diff(
        "Fix header button label",
        [ChangedFile(path="src/Header.jsx", status="M", added=1, deleted=1)],
    )

    assert result.task_kind == SIMPLE_CODE_TASK


def test_blog_front_task_requires_blog_front_src_change():
    with pytest.raises(GitHubSafetyError, match="blog-front/src/"):
        validate_meaningful_code_changes(
            "Modify blog-front/src/App.jsx and build a blog homepage",
            [ChangedFile(path="README.md", status="M", added=40, deleted=0), ChangedFile(path="src/App.jsx", status="M", added=40, deleted=0)],
        )


def test_complex_frontend_task_with_one_tiny_app_change_fails():
    with pytest.raises(GitHubSafetyError, match="too shallow"):
        validate_developer_diff(
            "Complete React personal blog with homepage, blog, about, and article layout",
            [ChangedFile(path="src/App.jsx", status="M", added=1, deleted=0)],
        )


def test_complex_frontend_task_with_app_and_styles_passes():
    result = validate_developer_diff(
        "Complete React personal blog with homepage, blog, about, and article layout",
        [
            ChangedFile(path="src/App.jsx", status="M", added=35, deleted=5),
            ChangedFile(path="src/index.scss", status="M", added=30, deleted=3),
        ],
    )

    assert result.source_file_count == 2
    assert result.frontend_source_file_count == 2


def test_multipage_blog_task_requires_two_meaningful_source_files():
    with pytest.raises(GitHubSafetyError, match="frontend source file"):
        validate_developer_diff(
            "Complete this unfinished multi-page blog with homepage + blog + about + article",
            [ChangedFile(path="src/App.jsx", status="M", added=100, deleted=10)],
        )


def test_readme_plus_shallow_source_change_still_fails_for_complex_task():
    with pytest.raises(GitHubSafetyError, match="too shallow"):
        validate_developer_diff(
            "Build React blog homepage and layout",
            [
                ChangedFile(path="README.md", status="M", added=30, deleted=0),
                ChangedFile(path="src/App.jsx", status="M", added=1, deleted=0),
            ],
        )


def test_readme_plus_meaningful_source_changes_passes():
    result = validate_developer_diff(
        "Build React blog homepage and layout",
        [
            ChangedFile(path="README.md", status="M", added=30, deleted=0),
            ChangedFile(path="src/App.jsx", status="M", added=30, deleted=3),
            ChangedFile(path="src/index.scss", status="M", added=30, deleted=3),
        ],
    )

    assert result.source_file_count == 2


def test_tiny_explicit_heading_task_can_pass_with_one_source_file():
    result = validate_developer_diff(
        'Add heading "Zargar test passed" to App.jsx',
        [ChangedFile(path="src/App.jsx", status="M", added=1, deleted=0)],
    )

    assert result.task_kind == TINY_TEST_TASK


def test_complex_task_below_minimum_source_line_threshold_fails():
    with pytest.raises(GitHubSafetyError, match="source line"):
        validate_developer_diff(
            "Implement backend API pagination",
            [
                ChangedFile(path="app/api/users.py", status="M", added=5, deleted=0),
                ChangedFile(path="app/services/users.py", status="M", added=5, deleted=0),
            ],
        )


def test_comment_only_source_change_fails():
    with pytest.raises(GitHubSafetyError, match="comment-only"):
        validate_developer_diff(
            "Build React homepage",
            [
                ChangedFile(path="src/App.jsx", status="M", added=20, deleted=0),
                ChangedFile(path="src/index.scss", status="M", added=5, deleted=0),
            ],
            diff_text="diff --git a/src/App.jsx b/src/App.jsx\n+// TODO: placeholder\n+// coming soon\n",
        )


def test_forbidden_file_changes_are_blocked(tmp_path):
    repo = make_static_repo(tmp_path / "repo")
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/unsafe-dev001", "main")
    (repo / ".env").write_text("SECRET=1")

    with pytest.raises(GitHubSafetyError, match="Forbidden paths touched") as exc:
        service.enforce_diff_safety(repo, "main")

    assert ".env" in str(exc.value)
    assert "Draft PR created: no" in str(exc.value)


def test_github_workflow_changes_are_blocked(tmp_path):
    repo = make_static_repo(tmp_path / "repo")
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/workflow-dev001", "main")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "deploy.yml").write_text("name: deploy\n")

    with pytest.raises(GitHubSafetyError, match="Forbidden paths touched") as exc:
        service.enforce_diff_safety(repo, "main")

    assert ".github/workflows/deploy.yml" in str(exc.value)
    assert "Draft PR created: no" in str(exc.value)


def test_excessive_diff_is_blocked(tmp_path):
    repo = make_static_repo(tmp_path / "repo")
    settings = Settings(max_changed_source_lines_for_mvp=2)
    service = LocalWorkspaceService(settings, root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/large-dev001", "main")
    (repo / "index.html").write_text("one\ntwo\nthree\nfour\n")

    with pytest.raises(GitHubSafetyError, match="MAX_CHANGED_SOURCE_LINES_FOR_MVP") as exc:
        service.enforce_diff_safety(repo, "main")

    assert "Changed source line count:" in str(exc.value)
    assert "Top-level directories touched:" in str(exc.value)
    assert "Draft PR created: no" in str(exc.value)


def test_exceeding_max_changed_files_is_blocked_with_details(tmp_path):
    repo = make_static_repo(tmp_path / "repo")
    settings = Settings(max_changed_files_for_mvp=2)
    service = LocalWorkspaceService(settings, root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/files-dev001", "main")
    for index in range(3):
        (repo / f"src{index}.js").write_text(f"export const value{index} = {index};\n")

    with pytest.raises(GitHubSafetyError, match="MAX_CHANGED_FILES_FOR_MVP") as exc:
        service.enforce_diff_safety(repo, "main")

    assert "Changed file count: 3 / 2" in str(exc.value)


def test_exceeding_max_allowed_directories_is_blocked_with_details(tmp_path):
    repo = make_static_repo(tmp_path / "repo")
    settings = Settings(max_allowed_directories_for_mvp=2)
    service = LocalWorkspaceService(settings, root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/dirs-dev001", "main")
    for dirname in ["src", "app", "pages"]:
        (repo / dirname).mkdir()
        (repo / dirname / "file.js").write_text("export const value = 1;\n")

    with pytest.raises(GitHubSafetyError, match="MAX_ALLOWED_DIRECTORIES_FOR_MVP") as exc:
        service.enforce_diff_safety(repo, "main")

    assert "Top-level directories touched:" in str(exc.value)
    assert "(3 / 2)" in str(exc.value)


def test_legitimate_frontend_blog_implementation_is_allowed(tmp_path):
    repo = tmp_path / "repo"
    (repo / "blog-front" / "src").mkdir(parents=True)
    (repo / "blog-front" / "src" / "App.jsx").write_text("export default function App(){return <div>Old</div>}\n")
    (repo / "blog-front" / "src" / "index.scss").write_text("body { margin: 0; }\n")
    init_git(repo)
    service = LocalWorkspaceService(Settings(), root=tmp_path / "workspaces")
    service.create_branch(repo, "zargar/blog-dev001", "main")
    (repo / "blog-front" / "src" / "App.jsx").write_text("\n".join(f"export const Section{i}=()=> <section>{i}</section>;" for i in range(40)))
    (repo / "blog-front" / "src" / "index.scss").write_text("\n".join(f".section-{i} {{ margin: {i}px; }}" for i in range(40)))

    changed = service.enforce_diff_safety(repo, "main")

    assert {item.path for item in changed} == {"blog-front/src/App.jsx", "blog-front/src/index.scss"}

def test_safe_build_test_command_detection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"scripts":{"build":"vite build","test":"vitest","lint":"eslint ."}}')
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (repo / "tests").mkdir()

    commands = TestRunner().detect_commands(repo)

    assert ["npm", "run", "build"] in commands
    assert ["npm", "test"] in commands
    assert ["npm", "run", "lint"] in commands
    assert [sys.executable, "-m", "pytest"] in commands


def test_failed_tests_are_reported(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_fail.py").write_text("def test_fail():\n    assert False\n")

    result = TestRunner().run(repo)

    assert result.ran
    assert not result.passed
    assert "python -m pytest: failed" in result.summary_text()


def test_local_developer_agent_creates_pr_with_real_file_changes(tmp_path):
    db, company = setup_db()
    source_repo = make_react_repo(tmp_path / "source")
    workspace = MockWorkspace(Settings(github_allowed_repos="akmal/zargar"), source_repo, tmp_path / "workspaces")
    github = MockGitHub()
    settings = Settings(github_allowed_repos="akmal/zargar", developer_agent_mode="local_workspace")
    agent = DeveloperAgent(github=github, settings=settings, allowed_requester_ids={123}, workspace=workspace, test_runner=NoopTestRunner())

    result = asyncio.run(
        agent.run(
            db,
            DeveloperAgentInput(
                company_id=company.id,
                repo="akmal/zargar",
                task_text="Add homepage and blog layout",
                requester_id="123",
                execute_local=True,
            ),
        )
    )

    assert result["status"] == "pr_created"
    assert workspace.pushed_branch.startswith("zargar/")
    assert "src/App.jsx" in result["message"]
    assert ".zargar/developer-tasks" not in github.created_prs[0]["body"]
    assert "src/App.jsx" in github.created_prs[0]["body"]
    assert "Source Depth Validation" in github.created_prs[0]["body"]


def test_complex_safe_task_creates_draft_pr_only(tmp_path):
    db, company = setup_db()
    source_repo = make_react_repo(tmp_path / "source")
    workspace = MockWorkspace(Settings(github_allowed_repos="akmal/zargar"), source_repo, tmp_path / "workspaces")
    github = MockGitHub()
    settings = Settings(github_allowed_repos="akmal/zargar", developer_agent_mode="local_workspace")
    agent = DeveloperAgent(
        github=github,
        settings=settings,
        allowed_requester_ids={123},
        workspace=workspace,
        coding_executor=DeepFrontendCodingExecutor(),
        test_runner=NoopTestRunner(),
    )

    result = asyncio.run(
        agent.run(
            db,
            DeveloperAgentInput(
                company_id=company.id,
                repo="akmal/zargar",
                task_text="Complete React personal blog with homepage, blog, about, and article layout",
                requester_id="123",
                execute_local=True,
            ),
        )
    )

    assert result["status"] == "pr_created"
    assert github.created_prs[0]["draft"] is True
    assert workspace.pushed_branch.startswith("zargar/")
    assert "No merge or deploy performed." in result["message"]


def test_local_developer_agent_blocks_forbidden_path_before_pr(tmp_path):
    db, company = setup_db()
    source_repo = make_react_repo(tmp_path / "source")
    workspace = MockWorkspace(Settings(github_allowed_repos="akmal/zargar"), source_repo, tmp_path / "workspaces")
    github = MockGitHub()
    settings = Settings(github_allowed_repos="akmal/zargar", developer_agent_mode="local_workspace")
    agent = DeveloperAgent(
        github=github,
        settings=settings,
        allowed_requester_ids={123},
        workspace=workspace,
        coding_executor=EnvChangingExecutor(),
        test_runner=NoopTestRunner(),
    )

    result = asyncio.run(
        agent.run(
            db,
            DeveloperAgentInput(
                company_id=company.id,
                repo="akmal/zargar",
                task_text="Complete React personal blog with homepage, blog, about, and article layout",
                requester_id="123",
                execute_local=True,
            ),
        )
    )

    assert result["status"] == "failed"
    assert "Forbidden paths touched" in result["message"]
    assert "Draft PR created: no" in result["message"]
    assert not github.created_prs
    assert workspace.pushed_branch == ""


def test_local_developer_agent_does_not_create_pr_when_validation_fails(tmp_path):
    db, company = setup_db()
    source_repo = make_react_repo(tmp_path / "source")
    workspace = MockWorkspace(Settings(github_allowed_repos="akmal/zargar"), source_repo, tmp_path / "workspaces")
    github = MockGitHub()
    settings = Settings(github_allowed_repos="akmal/zargar", developer_agent_mode="local_workspace")
    agent = DeveloperAgent(
        github=github,
        settings=settings,
        allowed_requester_ids={123},
        workspace=workspace,
        coding_executor=ShallowCodingExecutor(),
        test_runner=NoopTestRunner(),
    )

    result = asyncio.run(
        agent.run(
            db,
            DeveloperAgentInput(
                company_id=company.id,
                repo="akmal/zargar",
                task_text="Complete React personal blog with homepage, blog, about, and article layout",
                requester_id="123",
                execute_local=True,
            ),
        )
    )

    assert result["status"] == "failed"
    assert not github.created_prs
    assert workspace.pushed_branch == ""


def test_local_developer_agent_blocks_docs_only_implementation(tmp_path):
    db, company = setup_db()
    source_repo = make_react_repo(tmp_path / "source")
    workspace = MockWorkspace(Settings(github_allowed_repos="akmal/zargar"), source_repo, tmp_path / "workspaces")
    github = MockGitHub()
    settings = Settings(github_allowed_repos="akmal/zargar", developer_agent_mode="local_workspace")
    agent = DeveloperAgent(
        github=github,
        settings=settings,
        allowed_requester_ids={123},
        workspace=workspace,
        coding_executor=DocsOnlyExecutor(),
        test_runner=NoopTestRunner(),
    )

    result = asyncio.run(
        agent.run(
            db,
            DeveloperAgentInput(
                company_id=company.id,
                repo="akmal/zargar",
                task_text="Build React blog homepage and layout",
                requester_id="123",
                execute_local=True,
            ),
        )
    )

    assert result["status"] == "failed"
    assert "no source-code changes" in result["message"]
    assert not github.created_prs


def test_direct_merge_or_deploy_task_is_blocked():
    db, company = setup_db()
    github = MockGitHub()
    agent = DeveloperAgent(github=github, settings=Settings(github_allowed_repos="akmal/zargar"), allowed_requester_ids={123})

    merge_result = asyncio.run(
        agent.run(db, DeveloperAgentInput(company_id=company.id, repo="akmal/zargar", task_text="Build homepage and merge to main", requester_id="123"))
    )
    deploy_result = asyncio.run(
        agent.run(db, DeveloperAgentInput(company_id=company.id, repo="akmal/zargar", task_text="Build homepage and deploy to production", requester_id="123"))
    )

    assert merge_result["status"] == "failed"
    assert "Direct merge to main is forbidden" in merge_result["message"]
    assert deploy_result["status"] == "failed"
    assert "Production deployment is forbidden" in deploy_result["message"]
    assert not github.created_prs


def test_no_push_to_main_possible():
    with pytest.raises(GitHubSafetyError):
        validate_not_protected_branch("main", "main")


def test_telegram_dev_task_returns_pr_summary():
    db, company = setup_db()
    agent = FakeLocalDeveloperAgent()
    from app.bot.telegram_owner_bot import TelegramOwnerBot

    bot = TelegramOwnerBot(company.id, session_factory(db), allowed_user_ids={123}, developer_agent=agent)
    response = asyncio.run(bot.handle_message(123, '/dev_task akmal/zargar "Add homepage and blog layout"', chat_id="1", message_id="2"))

    assert "Draft PR Created" in response[0]
    assert "Changed files:" in response[0]
    assert "PR URL:" in response[0]


def make_static_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text("<html><body>Old</body></html>")
    init_git(path)
    return path


def make_react_repo(path: Path) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "package.json").write_text('{"dependencies":{"@vitejs/plugin-react":"latest","vite":"latest","react":"latest"}}')
    (path / "src" / "App.jsx").write_text("export default function App(){return <div>Old</div>}")
    init_git(path)
    return path


def init_git(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "zargar@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Zargar Tests"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


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


def session_factory(db):
    @contextmanager
    def factory():
        yield db

    return factory


class MockWorkspace(LocalWorkspaceService):
    def __init__(self, settings, source_repo: Path, root: Path):
        super().__init__(settings, root=root)
        self.source_repo = source_repo
        self.pushed_branch = ""

    def clone_repo(self, repo: str, task_id: str) -> Path:
        workspace = self.create_workspace(task_id)
        for item in self.source_repo.iterdir():
            target = workspace / item.name
            if item.is_dir():
                subprocess.run(["cp", "-R", str(item), str(target)], check=True)
            else:
                target.write_bytes(item.read_bytes())
        return workspace

    def push_branch(self, workspace: Path, branch: str) -> None:
        self.pushed_branch = branch


class NoopTestRunner:
    def run(self, workspace: Path):
        from app.services.test_runner import TestRunSummary

        return TestRunSummary(commands=[])


class ShallowCodingExecutor:
    def execute(self, task_text, repo_summary, company_context=""):
        from app.services.coding_executor import CodingResult

        app = repo_summary.path / "src" / "App.jsx"
        app.write_text('export default function App(){return <h1>Zargar test passed</h1>}\n')
        return CodingResult(changed_files=["src/App.jsx"], summary="Added one heading.", plan=["Edit one file."])


class DeepFrontendCodingExecutor:
    def execute(self, task_text, repo_summary, company_context=""):
        from app.services.coding_executor import CodingResult

        app = repo_summary.path / "src" / "App.jsx"
        styles = repo_summary.path / "src" / "index.scss"
        styles.write_text("body { margin: 0; }\n")
        app.write_text("\n".join(f"export const BlogSection{i}=()=> <section>{i}</section>;" for i in range(40)))
        styles.write_text("\n".join(f".blog-section-{i} {{ padding: {i}px; }}" for i in range(40)))
        return CodingResult(changed_files=["src/App.jsx", "src/index.scss"], summary="Built blog UI.", plan=["Edit app and styles."])


class EnvChangingExecutor:
    def execute(self, task_text, repo_summary, company_context=""):
        from app.services.coding_executor import CodingResult

        (repo_summary.path / ".env").write_text("SECRET=1\n")
        return CodingResult(changed_files=[".env"], summary="Changed env.", plan=["Unsafe edit."])


class DocsOnlyExecutor:
    def execute(self, task_text, repo_summary, company_context=""):
        from app.services.coding_executor import CodingResult

        (repo_summary.path / "docs").mkdir(exist_ok=True)
        (repo_summary.path / "docs" / "implementation.md").write_text("Implemented in docs only.\n")
        return CodingResult(changed_files=["docs/implementation.md"], summary="Wrote docs.", plan=["Docs only."])


class FakeLocalDeveloperAgent:
    async def run(self, db, request):
        return {
            "status": "pr_created",
            "task_id": "task",
            "message": "Draft PR Created\n\nChanged files:\n- src/App.jsx\n\nPR URL: https://github.example/pull/1",
        }
