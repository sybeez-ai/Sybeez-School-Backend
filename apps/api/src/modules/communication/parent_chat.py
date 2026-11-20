"""Parent messaging is WhatsApp only. In-app chat stays off while setup is on hold."""

from src.core.exceptions import ForbiddenError
from src.shared.enums import UserRole

PARENT_CHAT_ON_HOLD = (
    "Parent messages go out on WhatsApp. That setup is on hold, so in-app chat is not available."
)


def reject_parent_chat(role: str | UserRole | None) -> None:
    value = role.value if isinstance(role, UserRole) else role
    if value == UserRole.PARENT.value:
        raise ForbiddenError(PARENT_CHAT_ON_HOLD, code="parent_chat_on_hold")
