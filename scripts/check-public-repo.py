"""Small fail-closed tracked-file check; complements, not replaces, a secret scanner."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {".xlsx", ".xls", ".db", ".sqlite", ".sqlite3", ".pem", ".key"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yaml", ".yml", ".md", ".txt", ".example"}
SECRET_PREFIXES = ("sk-" + "proj-", "gh" + "p_", "gh" + "o_", "gl" + "pat-")
ASSIGNMENT = re.compile(r"(?mi)^\s*(OPENAI_API_KEY|SUPABASE_SERVICE_ROLE_KEY|ASSISTANT_API_TOKEN)\s*=\s*([^\s#]+)")


def main() -> None:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
    problems = []
    for raw in tracked:
        if not raw:
            continue
        name = raw.decode("utf-8")
        path = ROOT / name
        if path.suffix.lower() in FORBIDDEN or path.name.startswith(".env") and path.name != ".env.example":
            problems.append(f"forbidden_tracked_file:{name}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if any(prefix in content for prefix in SECRET_PREFIXES):
            problems.append(f"possible_secret_prefix:{name}")
        if path.name != ".env.example" and ASSIGNMENT.search(content):
            problems.append(f"possible_secret_assignment:{name}")
    if problems:
        raise SystemExit("\n".join(problems))
    print(f"public_repo_check_ok: {len(tracked) - 1} tracked paths scanned")


if __name__ == "__main__":
    main()
