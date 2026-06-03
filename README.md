# Zargar Labs MVP

Zargar Labs is a Telegram-first agent context layer for businesses. This MVP stores Telegram messages as non-lossy episodes, extracts business entities and temporal facts, resolves current memory, and exposes compact context to owner-facing agents.

## Local Setup

```bash
cd backend
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

The default local CLI demo uses SQLite at `backend/zargar_demo.db` and creates its schema automatically. For the FastAPI service with PostgreSQL, start Postgres with `pgvector`, set `DATABASE_URL` in `backend/.env`, then run migrations:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

For local development without an LLM key, leave `LLM_PROVIDER=mock`. The mock provider returns deterministic JSON for tests and demos.

## MVP Flow

1. `POST /companies` creates a tenant.
2. `POST /companies/{company_id}/sources/telegram-export` imports Telegram Desktop JSON messages as episodes.
3. `POST /companies/{company_id}/process/backfill` processes imported episodes chronologically.
4. `POST /companies/{company_id}/context/search` returns compact, source-backed context.
5. `POST /companies/{company_id}/agents/memory-qa/run` answers owner questions using the context layer.

## Tests

```bash
cd backend
pytest
```

## Seed Data

Demo Telegram export data lives in `backend/seed/demo_telegram_export.json`.

## CLI Demo

Run the first vertical slice locally with deterministic mock extraction:

```bash
cd backend
source ../.venv/bin/activate
USE_MOCK_LLM=true zargar create-company --name "Demo Education Center" --industry education
```

Copy the printed `company_id`, then run:

```bash
USE_MOCK_LLM=true zargar import-telegram --company-id <id> --file seed/demo_telegram_export.json
USE_MOCK_LLM=true zargar process-backfill --company-id <id>
USE_MOCK_LLM=true zargar ask --company-id <id> --query "What is our current discount policy?"
USE_MOCK_LLM=true zargar report --company-id <id> --period week
USE_MOCK_LLM=true zargar bottlenecks --company-id <id> --period week
```

Expected Memory QA output includes the active `15%` returning-student discount, its `valid_at` date, Telegram source evidence, and a note that the older `10%` policy is outdated.

## Running Telegram Owner Bot

The Telegram bot is for the owner or manager only. It does not auto-reply to customers.

Create a bot with BotFather:

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`.
3. Follow the prompts and copy the bot token.

Set the token in your shell or in `backend/.env`:

```bash
export TELEGRAM_BOT_TOKEN="123456:your-bot-token"
```

Find your Telegram user ID by messaging `@userinfobot` or another trusted ID lookup bot, then allow only owner/manager IDs:

```bash
export TELEGRAM_ALLOWED_USER_IDS="123456789,987654321"
```

To let the bot observe a business group, add the bot to the group and allow that chat ID:

```bash
export TELEGRAM_ALLOWED_CHAT_IDS="-1001234567890"
```

Only chats listed in `TELEGRAM_ALLOWED_CHAT_IDS` are ingested. Other group messages are ignored. The bot saves group messages as pending episodes and does not auto-reply in the group.

Install the bot extra if needed:

```bash
cd backend
source ../.venv/bin/activate
pip install -e ".[bot]"
```

Run the owner bot against an imported and processed company memory:

```bash
zargar bot --company-id <id>
```

Then message your bot in Telegram:

```text
/ask What are our current policies?
/decisions_week
/complaints_week
/tasks_open
/status
```

Responses include concise sections for the answer, current facts, historical/outdated facts, and Telegram source evidence when available.

After group messages are ingested, check counts and process new live episodes manually:

```bash
zargar stats --company-id <id>
zargar process-new --company-id <id> --limit 20
```

## Senior Developer Agent MVP

The senior developer agent accepts coding tasks from Telegram or CLI and creates reviewable GitHub draft pull requests. It does not merge, approve, deploy, bypass protections, modify secrets, or push to protected/default branches.

Configure GitHub access:

```bash
export GITHUB_TOKEN="ghp_..."
export GITHUB_ALLOWED_REPOS="owner/repo,owner2/repo2"
export GITHUB_DRAFT_PRS_ONLY=true
export GITHUB_MAX_FILES_CHANGED=5
export GITHUB_MAX_LINES_CHANGED=300
export GITHUB_MAX_NEW_FILES=3
```

Execution modes:

```bash
export DEVELOPER_AGENT_MODE=plan_only          # v0.8 behavior
export DEVELOPER_AGENT_MODE=local_workspace    # v0.9 real local edits
export DEVELOPER_CODING_ENGINE=deterministic   # default safe MVP engine
export DEVELOPER_CODING_ENGINE=codex_cli       # optional, requires Codex CLI
export CODEX_CLI_PATH=codex
export DEVELOPER_COMPLEX_MIN_SOURCE_FILES=2
export DEVELOPER_COMPLEX_MIN_SOURCE_LINES=20
export DEVELOPER_FRONTEND_COMPLEX_MIN_SOURCE_FILES=2
export DEVELOPER_MULTIPAGE_MIN_RELEVANT_SOURCE_FILES=2
export MAX_CHANGED_FILES_FOR_MVP=30
export MAX_CHANGED_SOURCE_LINES_FOR_MVP=2500
export MAX_ALLOWED_DIRECTORIES_FOR_MVP=8
export ALLOW_COMPLEX_REPO_TASKS=true
```

Safety model:

- Repositories must be listed in `GITHUB_ALLOWED_REPOS`.
- Every task uses one `zargar/*` branch.
- Draft PRs are created by default.
- No merge, deploy, force push, self-approval, branch-protection bypass, secret edits, or repository administration is supported.
- Forbidden paths include `.env`, `.env.*`, `secrets.*`, `credentials*`, `*private_key*`, `*secret_key*`, `*.pem`.
- CI/CD, infrastructure, deployment, and Terraform paths are blocked by default.
- Plan-only GitHub contents limits default to `GITHUB_MAX_FILES_CHANGED=5`, `GITHUB_MAX_LINES_CHANGED=300`, `GITHUB_MAX_NEW_FILES=3`.
- Local workspace coding budgets default to `MAX_CHANGED_FILES_FOR_MVP=30`, `MAX_CHANGED_SOURCE_LINES_FOR_MVP=2500`, `MAX_ALLOWED_DIRECTORIES_FOR_MVP=8`, `ALLOW_COMPLEX_REPO_TASKS=true`.
- Implementation tasks must pass source-depth validation before commit, push, or PR creation. README/docs/.zargar-only changes are rejected for implementation tasks.
- Complex app/frontend/backend tasks require meaningful source changes, with defaults of `DEVELOPER_COMPLEX_MIN_SOURCE_FILES=2` and `DEVELOPER_COMPLEX_MIN_SOURCE_LINES=20`.
- Complex frontend or multi-page tasks require relevant app source files such as `src/`, `app/`, `pages/`, `components/`, `blog-front/src/`, or `packages/*/src`.

Branch lifecycle:

- Branch format: `zargar/<task-slug>-<short-id>`.
- One task maps to one branch and one PR lifecycle.
- Re-running the same active task reuses the same branch.
- Stale `zargar/*` branches can be cleaned after the retention period, default `BRANCH_RETENTION_DAYS=7`.
- Non-`zargar/*` branches are never deleted by cleanup policy.

Telegram commands:

```text
/dev_help
/dev_task owner/repo "Add README setup section"
/dev_status <task_id>
/dev_cancel <task_id>
```

CLI commands:

```bash
zargar dev-task --company-id <id> --repo owner/repo --task "Add README setup section"
zargar dev-task --company-id <id> --repo owner/repo --task "Add homepage and blog layout" --execute-local
zargar dev-task --company-id <id> --repo owner/repo --task "Add homepage and blog layout" --execute-local --engine codex_cli
zargar dev-status --task-id <task_id>
```

Limitations:

- `plan_only` mode creates a safe draft PR with an auditable developer task file and implementation plan.
- `local_workspace` mode clones the allowlisted repo into `/tmp/zargar-workspaces/<task_id>`, creates a `zargar/*` branch, applies safe code changes, runs detected safe tests/build commands, pushes the branch, and opens a draft PR.
- The deterministic local executor supports README edits, static HTML/CSS sites, and simple React/Vite/Next homepage/blog/about scaffolding.
- `codex_cli` mode runs Codex CLI in the cloned workspace with a bounded prompt, then inspects the diff and blocks unsafe or oversized changes before commit/push.
- Ambiguous tasks such as `Improve auth` or `Refactor backend` are rejected with clarification questions.
- Human review remains required for every PR.

## Test With Your Own Telegram Export

1. In Telegram Desktop, open the group you have permission to analyze.
2. Open the group menu, choose `Export chat history`, select `Machine-readable JSON`, and export messages.
3. Keep the exported `result.json` local and treat it as sensitive business data.

Create a company and import the export:

```bash
cd backend
source ../.venv/bin/activate
USE_MOCK_LLM=true zargar create-company --name "Your Company" --industry education
USE_MOCK_LLM=true zargar import-telegram --company-id <id> --file /path/to/result.json
USE_MOCK_LLM=true zargar stats --company-id <id>
USE_MOCK_LLM=true zargar process-backfill --company-id <id>
```

Ask source-backed memory questions:

```bash
USE_MOCK_LLM=true zargar ask --company-id <id> --query "What important decisions were made?"
USE_MOCK_LLM=true zargar ask --company-id <id> --query "What are our current policies?"
USE_MOCK_LLM=true zargar ask --company-id <id> --query "What complaints repeated?"
USE_MOCK_LLM=true zargar facts --company-id <id> --status active
USE_MOCK_LLM=true zargar facts --company-id <id> --status invalidated
USE_MOCK_LLM=true zargar entities --company-id <id>
USE_MOCK_LLM=true zargar sources --company-id <id>
```

Only import Telegram groups where you have permission to analyze the messages. Zargar stores raw episodes for source traceability, then builds compact temporal memory from extracted entities and facts.

## Using Real LLM Extraction

Mock mode is deterministic and should remain the default for tests:

```bash
USE_MOCK_LLM=true pytest
```

For real extraction, use an OpenAI-compatible API:

```bash
export USE_MOCK_LLM=false
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4.1-mini"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # optional
```

Real extraction sends business messages and their context windows to the configured LLM provider. Start small to control cost and inspect quality:

```bash
zargar process-backfill --company-id <id> --limit 100 --dry-run
zargar process-backfill --company-id <id> --limit 100
zargar review --company-id <id>
zargar facts --company-id <id> --status active
```

Failed or invalid LLM responses do not crash backfill. Episodes with invalid JSON/schema failures are marked `needs_review`; unexpected processing failures are marked `failed`; low-confidence facts appear in `zargar review`.
