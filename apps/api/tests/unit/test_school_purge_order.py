from src.modules.platform.purge import ForeignKey, deletion_order, missing_from_purge, purge_steps


def test_a_child_row_is_deleted_before_the_parent_it_references():
    order = deletion_order(
        ["students", "enrollments", "schools_extra"],
        [("enrollments", "students")],
    )
    assert order.index("enrollments") < order.index("students")


def test_cycles_are_kept_so_the_caller_can_retry():
    order = deletion_order(["a", "b"], [("a", "b"), ("b", "a")])
    assert set(order) == {"a", "b"}


def test_a_table_that_only_references_students_is_purged_before_them():
    school_tables = {"students", "fee_ledger_entries", "fee_audit_logs"}
    keys = [
        ForeignKey("students", "schools", ("school_id",), ("id",)),
        ForeignKey("fee_ledger_entries", "schools", ("school_id",), ("id",)),
        ForeignKey("fee_ledger_entries", "students", ("student_id",), ("id",)),
        ForeignKey("fee_audit_logs", "schools", ("school_id",), ("id",)),
        ForeignKey("student_notes", "students", ("student_id",), ("id",)),
    ]
    steps = purge_steps(school_tables, keys)
    names = [step.table for step in steps]
    assert "student_notes" in names
    assert names.index("student_notes") < names.index("students")
    assert names.index("fee_ledger_entries") < names.index("students")
    notes = next(step for step in steps if step.table == "student_notes")
    assert "DELETE FROM \"student_notes\"" in notes.sql
    assert "students" in notes.sql
    assert "school_id = :sid" in notes.sql
    assert missing_from_purge(school_tables, keys, {step.table for step in steps}) == []


def test_fee_rows_without_school_id_are_reached_through_students():
    """fee_audit_logs -> fee_ledger_entries -> students, and neither fee table has school_id."""
    school_tables = {"students"}
    keys = [
        ForeignKey("students", "schools", ("school_id",), ("id",)),
        ForeignKey("fee_ledger_entries", "students", ("student_id",), ("id",)),
        ForeignKey("fee_audit_logs", "fee_ledger_entries", ("ledger_entry_id",), ("id",)),
    ]
    steps = purge_steps(school_tables, keys)
    names = [step.table for step in steps]
    assert names.index("fee_audit_logs") < names.index("fee_ledger_entries") < names.index("students")
    audit = next(step for step in steps if step.table == "fee_audit_logs")
    assert "fee_ledger_entries" in audit.where
    assert "students" in audit.where
    assert "school_id = :sid" in audit.where
    assert missing_from_purge(school_tables, keys, {step.table for step in steps}) == []


def test_a_referencing_table_left_out_of_the_plan_fails_the_check():
    keys = [ForeignKey("student_notes", "students", ("student_id",), ("id",))]
    missing = missing_from_purge({"students"}, keys, {"students"})
    assert missing == ["student_notes"]
