from datetime import UTC, datetime
from secrets import token_urlsafe


def utcnow() -> datetime:
    return datetime.now(UTC)


def generate_token(nbytes: int = 32) -> str:
    return token_urlsafe(nbytes)



def format_class_label(name: str | None, section: str | None = None) -> str:
    """Build a class label without repeating a section already in the name.

    "Grade 8-A" + section "A" stays "Grade 8-A". "Grade 8" + "A" becomes "Grade 8-A".
    """
    label = (name or "").strip()
    sec = (section or "").strip()
    if not sec:
        return label
    suffix = f"-{sec}"
    upper = label.upper()
    if upper.endswith(suffix.upper()) or upper.endswith(f" {sec.upper()}") or upper.endswith(sec.upper()):
        # Avoid "Grade 8-A-A". A bare name equal to the section (unlikely) is left as-is.
        if upper.endswith(suffix.upper()) or (len(label) > len(sec) and upper.endswith(sec.upper())):
            return label
    return f"{label}{suffix}"
