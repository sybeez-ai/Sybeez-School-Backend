from src.shared.utils import format_class_label


def test_format_class_label_skips_duplicate_section():
    assert format_class_label("Grade 8-A", "A") == "Grade 8-A"
    assert format_class_label("Grade 8-A", "a") == "Grade 8-A"


def test_format_class_label_appends_when_needed():
    assert format_class_label("Grade 8", "A") == "Grade 8-A"
    assert format_class_label("Grade 8", None) == "Grade 8"
    assert format_class_label("Grade 8", "") == "Grade 8"


def test_format_class_label_empty_name():
    assert format_class_label(None, "A") == "-A"
    assert format_class_label("", None) == ""
