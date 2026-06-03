import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestCommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class TestRunSummary:
    commands: list[TestCommandResult]

    @property
    def passed(self) -> bool:
        return all(result.returncode == 0 for result in self.commands)

    @property
    def ran(self) -> bool:
        return bool(self.commands)

    def summary_text(self) -> str:
        if not self.commands:
            return "No safe test/build commands detected."
        lines = []
        for result in self.commands:
            status = "passed" if result.returncode == 0 else "failed"
            lines.append(f"{display_command(result.command)}: {status}")
        return "\n".join(lines)


class TestRunner:
    def detect_commands(self, workspace: Path) -> list[list[str]]:
        commands: list[list[str]] = []
        package_json = workspace / "package.json"
        if package_json.exists():
            scripts = read_package_scripts(package_json)
            if "build" in scripts:
                commands.append(["npm", "run", "build"])
            if "test" in scripts:
                commands.append(["npm", "test"])
            if "lint" in scripts:
                commands.append(["npm", "run", "lint"])
        if (workspace / "pyproject.toml").exists() and (workspace / "tests").exists():
            commands.append([sys.executable, "-m", "pytest"])
        return commands

    def run(self, workspace: Path) -> TestRunSummary:
        results = []
        for command in self.detect_commands(workspace):
            result = subprocess.run(command, cwd=workspace, text=True, capture_output=True, check=False, timeout=120)
            results.append(
                TestCommandResult(
                    command=command,
                    returncode=result.returncode,
                    stdout=result.stdout[-4000:],
                    stderr=result.stderr[-4000:],
                )
            )
        return TestRunSummary(commands=results)


def read_package_scripts(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data.get("scripts") or {}


def display_command(command: list[str]) -> str:
    if len(command) >= 3 and command[1:] == ["-m", "pytest"]:
        return "python -m pytest"
    return " ".join(command)
