import subprocess
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.services.github_service import validate_safe_path
from app.services.local_workspace import RepoSummary


@dataclass
class CodingResult:
    changed_files: list[str]
    summary: str
    plan: list[str]
    debug_log: dict = field(default_factory=dict)


class CodingExecutorError(RuntimeError):
    def __init__(self, message: str, debug_log: dict | None = None):
        super().__init__(message)
        self.debug_log = debug_log or {}


class CodingExecutor:
    def __init__(self, settings: Settings):
        self.settings = settings

    def execute(self, task_text: str, repo_summary: RepoSummary, company_context: str = "") -> CodingResult:
        engine = self.settings.developer_coding_engine
        if engine == "codex_cli":
            return run_codex_cli(self.settings, task_text, repo_summary, company_context)
        if engine != "deterministic":
            raise RuntimeError(f"Unsupported developer coding engine: {engine}")
        return deterministic_execute(task_text, repo_summary)


def deterministic_execute(task_text: str, repo_summary: RepoSummary) -> CodingResult:
    lower = task_text.lower()
    plan = [
        "Inspect detected repository structure.",
        "Apply deterministic safe project edits.",
        "Avoid forbidden paths and CI/CD, secrets, infra, or deployment files.",
    ]
    changed: list[str] = []
    explicit_changes = apply_explicit_file_task(repo_summary.path, task_text)
    if explicit_changes:
        changed.extend(explicit_changes)
    elif "readme" in lower:
        changed.append(update_readme(repo_summary.path, task_text))
    elif repo_summary.project_type in {"static_html", "unknown"} and (repo_summary.path / "index.html").exists():
        changed.extend(update_static_site(repo_summary.path, task_text))
    elif repo_summary.project_type in {"vite", "react"}:
        changed.extend(update_react_site(repo_summary.path, task_text))
    elif repo_summary.project_type == "next":
        changed.extend(update_next_site(repo_summary.path, task_text))
    elif is_ui_or_app_task(lower):
        raise RuntimeError("Deterministic executor could not find safe app source files to modify.")
    else:
        changed.append(update_readme(repo_summary.path, task_text))
    unique_changed = list(dict.fromkeys(changed))
    return CodingResult(
        changed_files=unique_changed,
        summary=f"Applied deterministic changes to {', '.join(unique_changed)}.",
        plan=plan,
        debug_log={"engine": "deterministic", "changed_files": unique_changed},
    )


def apply_explicit_file_task(workspace: Path, task_text: str) -> list[str]:
    paths = extract_safe_paths(task_text)
    if not paths:
        return []
    changed = []
    heading = extract_quoted_heading(task_text) or "Zargar test passed"
    lower = task_text.lower()
    wants_blog_layout = is_blog_layout_task(lower)
    for rel in paths:
        path = workspace / rel
        if not path.exists() or not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in {".jsx", ".tsx", ".js", ".ts"}:
            did_change = write_blog_react_app(path, task_text) if wants_blog_layout else insert_jsx_heading(path, heading)
            if did_change:
                changed.append(rel)
        elif suffix in {".scss", ".css"}:
            did_change = write_blog_styles(path) if wants_blog_layout else ("style" in lower and add_heading_style(path))
            if did_change:
                changed.append(rel)
        elif suffix in {".html"} and insert_html_heading(path, heading):
            changed.append(rel)
        elif path.name.lower().startswith("readme") and append_file_note(path, task_text):
            changed.append(rel)
    return changed


def is_ui_or_app_task(lower_task_text: str) -> bool:
    terms = {
        "ui",
        "frontend",
        "front-end",
        "app",
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
    }
    return any(term in lower_task_text for term in terms)


def is_blog_layout_task(lower_task_text: str) -> bool:
    required = any(term in lower_task_text for term in {"blog", "homepage", "home page", "personal blog"})
    layout_terms = {"navigation", "about", "profile", "article", "sample posts", "post"}
    return required and any(term in lower_task_text for term in layout_terms)


def extract_safe_paths(task_text: str) -> list[str]:
    candidates = re.findall(r"[\w./-]+\.(?:jsx|tsx|js|ts|scss|css|html|md)", task_text)
    paths = []
    for candidate in candidates:
        rel = candidate.strip(".,;:'\"`()[]{}")
        try:
            validate_safe_path(rel)
        except Exception:
            continue
        if rel not in paths:
            paths.append(rel)
    return paths


def extract_quoted_heading(task_text: str) -> str | None:
    quoted = re.findall(r'"([^"]+)"', task_text)
    for item in quoted:
        if 1 <= len(item) <= 120:
            return item
    return None


def insert_jsx_heading(path: Path, heading: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if heading in text:
        return False
    heading_node = f'<h1 className="zargar-test-heading">{escape_jsx(heading)}</h1>'
    replacements = [
        ("return <div>", f"return <div>{heading_node}"),
        ("return (<div>", f"return (<div>{heading_node}"),
        ("return (\n    <div", f"return (\n    <div>{heading_node}"),
        ("return (\n      <div", f"return (\n      <div>{heading_node}"),
        ("return (\n    <>", f"return (\n    <>{heading_node}"),
        ("return (\n      <>", f"return (\n      <>{heading_node}"),
    ]
    updated = text
    for needle, replacement in replacements:
        if needle in updated:
            updated = updated.replace(needle, replacement, 1)
            break
    else:
        updated = text.rstrip() + f"\n\nexport const ZargarTestHeading = () => {heading_node};\n"
    path.write_text(updated, encoding="utf-8")
    return True


def insert_html_heading(path: Path, heading: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if heading in text:
        return False
    heading_node = f'<h1 class="zargar-test-heading">{escape_html(heading)}</h1>'
    if "<body" in text and ">" in text.split("<body", 1)[1]:
        prefix, rest = text.split("<body", 1)
        body_open, tail = rest.split(">", 1)
        updated = f"{prefix}<body{body_open}>{heading_node}{tail}"
    else:
        updated = heading_node + "\n" + text
    path.write_text(updated, encoding="utf-8")
    return True


def add_heading_style(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if ".zargar-test-heading" in text:
        return False
    path.write_text(
        text.rstrip()
        + "\n\n.zargar-test-heading {\n  margin: 0 0 1rem;\n  font-size: 2rem;\n  line-height: 1.15;\n}\n",
        encoding="utf-8",
    )
    return True


def write_blog_react_app(path: Path, task_text: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    updated = blog_react_app(task_text)
    if text == updated:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def write_blog_styles(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    updated = blog_site_css()
    if text == updated:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def append_file_note(path: Path, task_text: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "Zargar Developer Update" in text:
        return False
    path.write_text(text.rstrip() + f"\n\n## Zargar Developer Update\n\n{task_text}\n", encoding="utf-8")
    return True


def update_readme(workspace: Path, task_text: str) -> str:
    rel = "README.md"
    path = workspace / rel
    existing = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else "# Project\n"
    addition = (
        "\n\n## Zargar Developer Update\n\n"
        f"Task implemented by the Zargar developer agent:\n\n{task_text}\n"
    )
    path.write_text(existing.rstrip() + addition + "\n", encoding="utf-8")
    return rel


def update_static_site(workspace: Path, task_text: str) -> list[str]:
    html = workspace / "index.html"
    css = workspace / "styles.css"
    html.write_text(static_html(task_text), encoding="utf-8")
    css.write_text(site_css(), encoding="utf-8")
    return ["index.html", "styles.css"]


def update_react_site(workspace: Path, task_text: str) -> list[str]:
    src = workspace / "src"
    src.mkdir(exist_ok=True)
    app = src / "App.jsx"
    css = src / "App.css"
    app.write_text(react_app(task_text), encoding="utf-8")
    css.write_text(site_css(), encoding="utf-8")
    return ["src/App.jsx", "src/App.css"]


def update_next_site(workspace: Path, task_text: str) -> list[str]:
    app_dir = workspace / "app"
    app_dir.mkdir(exist_ok=True)
    page = app_dir / "page.tsx"
    css = app_dir / "globals.css"
    page.write_text(next_page(task_text), encoding="utf-8")
    css.write_text(site_css(), encoding="utf-8")
    return ["app/page.tsx", "app/globals.css"]


def static_html(task_text: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Personal Blog</title>
    <link rel="stylesheet" href="styles.css">
  </head>
  <body>
    <header class="site-header">
      <a class="brand" href="#home">Personal Blog</a>
      <nav>
        <a href="#blog">Blog</a>
        <a href="#about">About</a>
      </nav>
    </header>
    <main id="home">
      <section class="hero">
        <p class="eyebrow">Personal writing and notes</p>
        <h1>Thoughtful essays, project updates, and field notes.</h1>
        <p>{escape_html(task_text)}</p>
      </section>
      <section id="blog" class="grid">
        <article>
          <span>Featured</span>
          <h2>Building with care</h2>
          <p>A clean article layout for long-form posts and updates.</p>
        </article>
        <article>
          <span>Notes</span>
          <h2>Recent observations</h2>
          <p>Short posts can live here with clear dates and summaries.</p>
        </article>
      </section>
      <section id="about" class="about">
        <h2>About</h2>
        <p>A profile section for biography, work, and contact links.</p>
      </section>
    </main>
  </body>
</html>
"""


def react_app(task_text: str) -> str:
    return f"""import './App.css';

const posts = [
  {{ title: 'Building with care', type: 'Featured', summary: 'A polished layout for long-form writing.' }},
  {{ title: 'Recent observations', type: 'Notes', summary: 'Short updates and personal field notes.' }},
];

export default function App() {{
  return (
    <main className="page">
      <header className="site-header">
        <a className="brand" href="#home">Personal Blog</a>
        <nav>
          <a href="#blog">Blog</a>
          <a href="#about">About</a>
        </nav>
      </header>
      <section id="home" className="hero">
        <p className="eyebrow">Personal writing and notes</p>
        <h1>Thoughtful essays, project updates, and field notes.</h1>
        <p>{escape_jsx(task_text)}</p>
      </section>
      <section id="blog" className="grid">
        {{posts.map((post) => (
          <article key={{post.title}}>
            <span>{{post.type}}</span>
            <h2>{{post.title}}</h2>
            <p>{{post.summary}}</p>
          </article>
        ))}}
      </section>
      <section id="about" className="about">
        <h2>About</h2>
        <p>A profile section for biography, work, and contact links.</p>
      </section>
    </main>
  );
}}
"""


def blog_react_app(task_text: str) -> str:
    return f"""const posts = [
  {{
    title: 'Designing Calm Digital Products',
    date: 'May 18, 2026',
    summary: 'Notes on building interfaces that feel focused, useful, and easy to revisit.',
  }},
  {{
    title: 'Lessons From a Working Week',
    date: 'May 11, 2026',
    summary: 'A short reflection on projects, decisions, and the habits that keep work moving.',
  }},
  {{
    title: 'Reading List for Builders',
    date: 'May 4, 2026',
    summary: 'Books, articles, and references worth keeping close while making software.',
  }},
];

export default function App() {{
  return (
    <main className="site-shell">
      <header className="top-nav">
        <a className="brand" href="#home">Azim Pulatov</a>
        <nav aria-label="Primary navigation">
          <a href="#posts">Blog</a>
          <a href="#about">About</a>
          <a href="#article">Article</a>
        </nav>
      </header>

      <section id="home" className="hero">
        <p className="eyebrow">Personal blog</p>
        <h1>Writing about product, engineering, and thoughtful work.</h1>
        <p className="hero-copy">
          A minimal personal site for essays, project notes, and professional updates.
        </p>
      </section>

      <section id="posts" className="section">
        <div className="section-heading">
          <p className="eyebrow">Latest posts</p>
          <h2>Blog</h2>
        </div>
        <div className="post-grid">
          {{posts.map((post) => (
            <article className="post-card" key={{post.title}}>
              <time>{{post.date}}</time>
              <h3>{{post.title}}</h3>
              <p>{{post.summary}}</p>
            </article>
          ))}}
        </div>
      </section>

      <section id="about" className="about section">
        <div>
          <p className="eyebrow">About</p>
          <h2>Profile</h2>
        </div>
        <p>
          This profile section introduces the author, current interests, and the themes
          readers can expect across the blog.
        </p>
      </section>

      <section id="article" className="article-preview section">
        <p className="eyebrow">Featured article</p>
        <h2>How small decisions shape better products</h2>
        <p>
          A simple article layout with clear typography, readable spacing, and a focused
          summary for long-form writing.
        </p>
      </section>
    </main>
  );
}}
"""


def next_page(task_text: str) -> str:
    return f"""const posts = [
  {{ title: 'Building with care', type: 'Featured', summary: 'A polished layout for long-form writing.' }},
  {{ title: 'Recent observations', type: 'Notes', summary: 'Short updates and personal field notes.' }},
];

export default function Home() {{
  return (
    <main className="page">
      <header className="site-header">
        <a className="brand" href="#home">Personal Blog</a>
        <nav>
          <a href="#blog">Blog</a>
          <a href="#about">About</a>
        </nav>
      </header>
      <section id="home" className="hero">
        <p className="eyebrow">Personal writing and notes</p>
        <h1>Thoughtful essays, project updates, and field notes.</h1>
        <p>{escape_jsx(task_text)}</p>
      </section>
      <section id="blog" className="grid">
        {{posts.map((post) => (
          <article key={{post.title}}>
            <span>{{post.type}}</span>
            <h2>{{post.title}}</h2>
            <p>{{post.summary}}</p>
          </article>
        ))}}
      </section>
      <section id="about" className="about">
        <h2>About</h2>
        <p>A profile section for biography, work, and contact links.</p>
      </section>
    </main>
  );
}}
"""


def site_css() -> str:
    return """:root {
  color: #1d1d1f;
  background: #f7f6f2;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

body {
  margin: 0;
}

.page,
main {
  max-width: 1080px;
  margin: 0 auto;
  padding: 0 24px 64px;
}

.site-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24px 0;
}

.brand,
nav a {
  color: inherit;
  text-decoration: none;
}

nav {
  display: flex;
  gap: 20px;
}

.hero {
  padding: 72px 0 48px;
  border-bottom: 1px solid #d8d3c7;
}

.eyebrow,
article span {
  color: #6f6a60;
  text-transform: uppercase;
  font-size: 0.78rem;
  letter-spacing: 0;
}

h1 {
  max-width: 760px;
  font-size: 3rem;
  line-height: 1.05;
  margin: 12px 0 18px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 18px;
  padding: 36px 0;
}

article {
  border: 1px solid #d8d3c7;
  border-radius: 8px;
  padding: 20px;
  background: #fffdf8;
}

.about {
  max-width: 680px;
  padding-top: 24px;
}
"""


def blog_site_css() -> str:
    return """:root {
  color: #181818;
  background: #fbfaf7;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
}

a {
  color: inherit;
  text-decoration: none;
}

.site-shell {
  width: min(1120px, calc(100% - 32px));
  margin: 0 auto;
  padding-bottom: 72px;
}

.top-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 0;
  border-bottom: 1px solid #e5e0d7;
}

.brand {
  font-weight: 700;
}

.top-nav nav {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  color: #5e5a52;
}

.hero {
  padding: 84px 0 64px;
}

.eyebrow {
  margin: 0 0 12px;
  color: #7c7569;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1,
h2,
h3,
p {
  margin-top: 0;
}

h1 {
  max-width: 800px;
  margin-bottom: 22px;
  font-size: clamp(2.35rem, 8vw, 5.25rem);
  line-height: 0.98;
}

h2 {
  margin-bottom: 18px;
  font-size: clamp(1.65rem, 4vw, 2.6rem);
  line-height: 1.08;
}

h3 {
  margin-bottom: 12px;
  font-size: 1.2rem;
}

.hero-copy,
.about p,
.article-preview p,
.post-card p {
  max-width: 680px;
  color: #555047;
  font-size: 1rem;
  line-height: 1.7;
}

.section {
  padding: 48px 0;
  border-top: 1px solid #e5e0d7;
}

.section-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;
}

.post-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.post-card {
  min-height: 220px;
  padding: 24px;
  border: 1px solid #e1dbcf;
  border-radius: 8px;
  background: #fffdf9;
}

.post-card time {
  display: block;
  margin-bottom: 18px;
  color: #7c7569;
  font-size: 0.9rem;
}

.about {
  display: grid;
  grid-template-columns: minmax(180px, 280px) 1fr;
  gap: 32px;
}

.article-preview {
  max-width: 760px;
}

@media (max-width: 760px) {
  .top-nav,
  .about,
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .post-grid {
    grid-template-columns: 1fr;
  }

  .hero {
    padding: 56px 0 44px;
  }
}
"""


def run_codex_cli(settings: Settings, task_text: str, repo_summary: RepoSummary, company_context: str) -> CodingResult:
    prompt = bounded_codex_prompt(task_text, repo_summary, company_context)
    status_before = git_output(repo_summary.path, ["status", "--porcelain"])
    command = [
        settings.codex_cli_path,
        "exec",
        "--cd",
        str(repo_summary.path),
        "--sandbox",
        "workspace-write",
        "-",
    ]
    result = subprocess.run(
        command,
        cwd=repo_summary.path,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    status_after = git_output(repo_summary.path, ["status", "--porcelain"])
    diff_stat_after = git_output(repo_summary.path, ["diff", "--stat"])
    diff_numstat_after = git_output(repo_summary.path, ["diff", "--numstat"])
    changed_after = changed_paths_from_status(status_after)
    debug_log = {
        "engine": "codex_cli",
        "workspace_path": str(repo_summary.path),
        "cwd": str(repo_summary.path),
        "command": command,
        "prompt_chars": len(prompt),
        "prompt_preview": truncate_debug(prompt, limit=1200),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "git_status_before": status_before,
        "git_status_after": status_after,
        "git_diff_stat_after": diff_stat_after,
        "git_diff_numstat_after": diff_numstat_after,
        "changed_files_after": changed_after,
    }
    if result.returncode != 0:
        raise CodingExecutorError(
            "Codex CLI failed "
            f"(cwd={repo_summary.path}, exit_code={result.returncode}, command={command}): "
            f"stdout={truncate_debug(result.stdout)} stderr={truncate_debug(result.stderr)}",
            debug_log,
        )
    if not changed_after:
        raise CodingExecutorError(
            "Codex CLI completed but produced no repository changes "
            f"(cwd={repo_summary.path}, command={command}, exit_code={result.returncode}). "
            f"stdout={truncate_debug(result.stdout)} stderr={truncate_debug(result.stderr)} "
            f"git_status_before={status_before!r} git_status_after={status_after!r} git_diff_stat_after={diff_stat_after!r}",
            debug_log,
        )
    for rel in changed_after:
        validate_safe_path(rel)
    return CodingResult(
        changed_files=changed_after,
        summary="Codex CLI completed. Diff safety and source-depth checks run after execution.",
        plan=["Run bounded Codex CLI prompt inside workspace."],
        debug_log=debug_log,
    )


def bounded_codex_prompt(task_text: str, repo_summary: RepoSummary, company_context: str) -> str:
    tree = "\n".join(f"- {path}" for path in repo_summary.tree[:120])
    key_files = "\n".join(f"- {path}" for path in sorted(repo_summary.key_files.keys()))
    return (
        "You are a senior developer editing a cloned repository for a draft pull request.\n"
        "Hard safety rules: do not access or modify secrets, .env files, credentials, keys, CI/CD, GitHub workflows, "
        "deployment, infrastructure, billing, permissions, or branch protection. Do not deploy or merge.\n\n"
        "Workflow:\n"
        "1. First inspect repository structure from the working tree.\n"
        "2. Identify framework, source roots, pages, routes, components, styles, and build/test commands.\n"
        "3. Make a concrete implementation plan internally before editing.\n"
        "4. Edit real application source files. Do not solve implementation tasks by writing summaries, README updates, docs, "
        "or .zargar files.\n"
        "5. For multi-page/frontend tasks, modify existing pages/components/styles instead of putting everything into one generic file.\n"
        "6. For backend tasks, modify the relevant route/service/model/tests rather than only comments or docs.\n"
        "7. After editing, run available safe build/test commands if detectable.\n"
        "8. Stop only after git diff shows meaningful source changes that satisfy the task.\n\n"
        f"Detected project type: {repo_summary.project_type}\n"
        f"Current branch: {repo_summary.branch}\n"
        f"Default branch: {repo_summary.default_branch}\n\n"
        f"Known key files:\n{key_files or '- none'}\n\n"
        f"Repository tree sample:\n{tree or '- none'}\n\n"
        f"Task:\n{task_text}\n\n"
        f"Company context:\n{company_context[:1000]}"
    )


def git_output(workspace: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return (result.stderr or result.stdout or "").strip()
    return result.stdout


def changed_paths_from_status(status: str) -> list[str]:
    paths = []
    for line in status.splitlines():
        if not line:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in paths:
            paths.append(path)
    return paths


def truncate_debug(text: str, limit: int = 4000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_jsx(text: str) -> str:
    return text.replace("{", "&#123;").replace("}", "&#125;")
