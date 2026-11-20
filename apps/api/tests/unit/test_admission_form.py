from src.modules.admissions.form import honeypot_tripped, missing_required, normalize_form, public_fields


def test_student_name_stays_required_and_unknown_fields_are_dropped():
    form = normalize_form(
        {
            "intro": "  Apply  ",
            "fields": [
                {"key": "student_name", "label": "Child", "enabled": False, "required": False},
                {"key": "phone", "label": "Mobile", "enabled": True, "required": True},
                {"key": "favourite_colour", "label": "Colour", "enabled": True},
            ],
        }
    )
    assert form["intro"] == "Apply"
    by_key = {field["key"]: field for field in form["fields"]}
    assert by_key["student_name"]["enabled"] is True
    assert by_key["student_name"]["required"] is True
    assert by_key["student_name"]["label"] == "Child"
    assert by_key["phone"]["required"] is True
    assert "favourite_colour" not in by_key
    shown = [field["key"] for field in public_fields(form)]
    assert shown == ["student_name", "parent_name", "phone", "email", "notes"]


def test_hidden_fields_are_not_required_and_the_honeypot_is_a_nonempty_value():
    form = normalize_form({"fields": [{"key": "email", "enabled": False, "required": True}]})
    assert missing_required(form, {"student_name": "Ada", "email": ""}) == []
    assert missing_required(form, {"student_name": "  "}) == ["Student name"]
    assert honeypot_tripped("https://spam.example") is True
    assert honeypot_tripped("  ") is False
    assert honeypot_tripped(None) is False
