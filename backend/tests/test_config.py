from app.config import Settings


def test_settings_loads_github_developer_agent_env_vars(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "owner/repo,owner2/repo2")
    monkeypatch.setenv("GITHUB_DRAFT_PRS_ONLY", "true")
    monkeypatch.setenv("GITHUB_MAX_FILES_CHANGED", "7")
    monkeypatch.setenv("GITHUB_MAX_LINES_CHANGED", "400")
    monkeypatch.setenv("GITHUB_MAX_NEW_FILES", "4")
    monkeypatch.setenv("DEVELOPER_AGENT_MODE", "local_workspace")
    monkeypatch.setenv("DEVELOPER_CODING_ENGINE", "codex_cli")
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("DEVELOPER_COMPLEX_MIN_SOURCE_FILES", "3")
    monkeypatch.setenv("DEVELOPER_COMPLEX_MIN_SOURCE_LINES", "50")
    monkeypatch.setenv("DEVELOPER_FRONTEND_COMPLEX_MIN_SOURCE_FILES", "3")
    monkeypatch.setenv("DEVELOPER_MULTIPAGE_MIN_RELEVANT_SOURCE_FILES", "3")
    monkeypatch.setenv("MAX_CHANGED_FILES_FOR_MVP", "12")
    monkeypatch.setenv("MAX_CHANGED_SOURCE_LINES_FOR_MVP", "800")
    monkeypatch.setenv("MAX_ALLOWED_DIRECTORIES_FOR_MVP", "4")
    monkeypatch.setenv("ALLOW_COMPLEX_REPO_TASKS", "true")

    settings = Settings()

    assert settings.github_token == "token"
    assert settings.github_allowed_repos == "owner/repo,owner2/repo2"
    assert settings.github_draft_prs_only is True
    assert settings.github_max_files_changed == 7
    assert settings.github_max_lines_changed == 400
    assert settings.github_max_new_files == 4
    assert settings.developer_agent_mode == "local_workspace"
    assert settings.developer_coding_engine == "codex_cli"
    assert settings.codex_cli_path == "/usr/local/bin/codex"
    assert settings.developer_complex_min_source_files == 3
    assert settings.developer_complex_min_source_lines == 50
    assert settings.developer_frontend_complex_min_source_files == 3
    assert settings.developer_multipage_min_relevant_source_files == 3
    assert settings.max_changed_files_for_mvp == 12
    assert settings.max_changed_source_lines_for_mvp == 800
    assert settings.max_allowed_directories_for_mvp == 4
    assert settings.allow_complex_repo_tasks is True
