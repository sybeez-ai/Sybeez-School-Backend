"""Order school-scoped deletes so a child row goes before the parent it references."""

from __future__ import annotations


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
