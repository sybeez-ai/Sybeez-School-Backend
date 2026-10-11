"""Split migration scripts so each asyncpg call is one statement."""

from __future__ import annotations


def sql_statements(script: str) -> list[str]:
    """Return executable statements. Blank lines and `--` comments are skipped.

    Dollar-quoted bodies are not used in the scripts this splits. A semicolon
    ends a statement only when it is the last character on the line.
    """
    statements: list[str] = []
    current: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not current and (not stripped or stripped.startswith("--")):
            continue
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            text = "\n".join(current).strip()
            if text.endswith(";"):
                text = text[:-1].strip()
            if text:
                statements.append(text)
            current = []
    tail = "\n".join(current).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements
