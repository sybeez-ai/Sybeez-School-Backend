from src.modules.platform.purge import deletion_order


def test_a_child_row_is_deleted_before_the_parent_it_references():
    order = deletion_order(
        ["students", "enrollments", "schools_extra"],
        [("enrollments", "students")],
    )
    assert order.index("enrollments") < order.index("students")


def test_cycles_are_kept_so_the_caller_can_retry():
    order = deletion_order(["a", "b"], [("a", "b"), ("b", "a")])
    assert set(order) == {"a", "b"}
