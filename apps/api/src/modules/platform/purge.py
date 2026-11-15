"""Delete every row that belongs to one school, in foreign-key order.

The list is built from the live catalog (tables with school_id, plus every
table that references those tables). Nothing is hand-picked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def deletion_order(tables: list[str], references: list[tuple[str, str]]) -> list[str]:
    """`references` are (child, parent). The child is deleted first."""
    nodes = list(dict.fromkeys(tables))
    present = set(nodes)
    incoming = {table: 0 for table in nodes}
    parents_of: dict[str, set[str]] = {table: set() for table in nodes}
    for child, parent in references:
        if child == parent or child not in present or parent not in present:
            continue
        if parent in parents_of[child]:
            continue
        parents_of[child].add(parent)
        incoming[parent] += 1
    ready = sorted(table for table in nodes if incoming[table] == 0)
    order: list[str] = []
    while ready:
        table = ready.pop(0)
        order.append(table)
        for parent in sorted(parents_of[table]):
            incoming[parent] -= 1
            if incoming[parent] == 0:
                ready.append(parent)
                ready.sort()
    leftover = [table for table in nodes if table not in set(order)]
    return order + leftover


@dataclass(frozen=True)
class ForeignKey:
    child: str
    parent: str
    child_cols: tuple[str, ...]
    parent_cols: tuple[str, ...]


@dataclass(frozen=True)
class DeleteStep:
    table: str
    sql: str
    where: str


def quote_ident(name: str) -> str:
    if not _IDENT.fullmatch(name or ""):
        raise ValueError(f"Unsafe identifier: {name!r}")
    return f'"{name}"'


def tables_in_purge(school_tables: set[str], foreign_keys: list[ForeignKey]) -> set[str]:
    """Tables with school_id, and every table that points at one of them, except schools."""
    owned = {table for table in school_tables if table != "schools"}
    found = set(owned)
    changed = True
    while changed:
        changed = False
        for fk in foreign_keys:
            if fk.child == "schools" or fk.child in found:
                continue
            if fk.parent == "schools" or fk.parent in found:
                found.add(fk.child)
                changed = True
    return found


def _belongs(table: str, school_tables: set[str], foreign_keys: list[ForeignKey], seen: set[str]) -> str:
    if table == "schools":
        return "id = :sid"
    if table in school_tables and table != "schools":
        return "school_id = :sid"
    if table in seen:
        return "FALSE"
    seen.add(table)
    parts: list[str] = []
    for fk in foreign_keys:
        if fk.child != table or not fk.child_cols or len(fk.child_cols) != len(fk.parent_cols):
            continue
        if fk.parent != "schools" and fk.parent not in school_tables and fk.parent not in seen and fk.parent == table:
            continue
        parent_where = _belongs(fk.parent, school_tables, foreign_keys, seen)
        child_cols = ", ".join(quote_ident(col) for col in fk.child_cols)
        parent_cols = ", ".join(quote_ident(col) for col in fk.parent_cols)
        if len(fk.child_cols) == 1:
            parts.append(
                f"{quote_ident(fk.child_cols[0])} IN "
                f"(SELECT {quote_ident(fk.parent_cols[0])} FROM {quote_ident(fk.parent)} WHERE {parent_where})"
            )
        else:
            parts.append(
                f"({child_cols}) IN (SELECT {parent_cols} FROM {quote_ident(fk.parent)} WHERE {parent_where})"
            )
    if not parts:
        return "FALSE"
    return "(" + " OR ".join(parts) + ")"


def purge_steps(school_tables: set[str], foreign_keys: list[ForeignKey]) -> list[DeleteStep]:
    """One DELETE per table. A child is listed before the parent it references."""
    owned = {table for table in school_tables if table and table != "schools"}
    targets = tables_in_purge(owned, foreign_keys)
    edges = [
        (fk.child, fk.parent)
        for fk in foreign_keys
        if fk.child in targets and fk.parent in targets and fk.child != fk.parent
    ]
    steps: list[DeleteStep] = []
    for table in deletion_order(sorted(targets), edges):
        where = _belongs(table, owned, foreign_keys, set())
        if where == "FALSE":
            continue
        steps.append(
            DeleteStep(table=table, sql=f"DELETE FROM {quote_ident(table)} WHERE {where}", where=where)
        )
    return steps


def missing_from_purge(school_tables: set[str], foreign_keys: list[ForeignKey], planned: set[str]) -> list[str]:
    """Tables the catalog says belong to a school but the plan never deletes."""
    expected = tables_in_purge(school_tables, foreign_keys)
    return sorted(expected - planned)
