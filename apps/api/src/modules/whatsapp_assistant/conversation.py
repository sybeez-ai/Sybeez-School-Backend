"""Decide what a WhatsApp turn is allowed to do before any school data is read."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from src.modules.alerts.phone import is_opt_out_keyword
from src.modules.whatsapp_assistant.identity import ProfileChoice

UNKNOWN_REPLY = (
    "This WhatsApp number is not saved on a Sybeez School profile, so I can't look anything up. "
    "Add this number on your profile in the Sybeez app, or send LINK and the one-time code from your profile."
)
OPT_OUT_ACK = "You are opted out of WhatsApp messages from Sybeez. We will not send anything else to this number."
OPTED_OUT_REPLY = "This number is opted out, so I won't look anything up or send a reply."
NO_SCHOOL_REPLY = (
    "This number matches a Sybeez login that is not a member of a school, so I can't open school records from WhatsApp."
)
NO_CHILD_REPLY = "I can't find a child linked to this parent profile, so I can't open student records."
LINK_INVALID = "That code is not valid or it has expired. Generate a new one from your Sybeez profile and send LINK followed by the code."
LINK_PREFIX = "This number is now linked to your Sybeez profile. "

_GREETING = re.compile(r"^(hi|hello|hey|help|menu)[!.?\s]*$", re.I)
_SWITCH_PROFILE = re.compile(r"\b(switch|change)\b.+\b(profile|school|account)\b", re.I)
_SWITCH_CHILD = re.compile(r"\b(switch|change)\b.+\b(child|kid|student)\b", re.I)
_LINK = re.compile(r"^\s*link\s+([0-9]{6})\s*$", re.I)


@dataclass(frozen=True)
class ChildChoice:
    student_id: uuid.UUID
    label: str


@dataclass
class SessionState:
    user_id: uuid.UUID | None = None
    school_id: uuid.UUID | None = None
    role: str | None = None
    student_id: uuid.UUID | None = None
    pending: str | None = None
    options: list[dict] = field(default_factory=list)
    pending_text: str | None = None


@dataclass(frozen=True)
class Decision:
    kind: str
    text: str
    run_tools: bool = False
    send: bool = True
    profile: ProfileChoice | None = None
    child: ChildChoice | None = None
    pending: str | None = None
    options: list[dict] = field(default_factory=list)
    pending_text: str | None = None
    clear_selection: bool = False


def link_code_in(text: str) -> str | None:
    match = _LINK.match(text or "")
    return match.group(1) if match else None


def persona_for(role: str | None) -> str:
    return "AI teacher" if role == "STUDENT" else "AI assistant"


def greeting_for(role: str | None) -> str:
    if role == "STUDENT":
        return (
            "I'm your AI teacher. I answer from what your teacher uploaded for your class, "
            "and I can also check your attendance, homework, timetable, and results."
        )
    return (
        "I'm the Sybeez AI assistant. I can check attendance, homework, the timetable, results, "
        "fees, announcements, your payslip, and your leave balance."
    )


def match_option(text: str, options: list[dict]) -> dict | None:
    raw = (text or "").strip()
    if raw.isdigit():
        number = int(raw)
        for option in options:
            if int(option.get("n") or 0) == number:
                return option
        return None
    low = raw.lower()
    if len(low) < 2:
        return None
    hits = [option for option in options if low in str(option.get("label") or "").lower()]
    return hits[0] if len(hits) == 1 else None


def _profile_options(profiles: list[ProfileChoice]) -> list[dict]:
    options = []
    for index, profile in enumerate(profiles, start=1):
        options.append(
            {
                "n": index,
                "user_id": str(profile.user_id),
                "school_id": "" if profile.school_id is None else str(profile.school_id),
                "role": profile.role,
                "student_id": "" if profile.student_id is None else str(profile.student_id),
                "label": profile.label,
            }
        )
    return options


def _child_options(children: list[ChildChoice]) -> list[dict]:
    return [{"n": index, "student_id": str(child.student_id), "label": child.label} for index, child in enumerate(children, start=1)]


def _ask(kind: str, lines: list[str], options: list[dict], pending: str, pending_text: str | None) -> Decision:
    body = "\n".join(lines)
    return Decision(
        kind=kind,
        text=body,
        run_tools=False,
        pending=pending,
        options=options,
        pending_text=pending_text,
    )


def _find_profile(profiles: list[ProfileChoice], option: dict) -> ProfileChoice | None:
    school = option.get("school_id") or None
    student = option.get("student_id") or None
    for profile in profiles:
        if str(profile.user_id) != option.get("user_id"):
            continue
        if ("" if profile.school_id is None else str(profile.school_id)) != (school or ""):
            continue
        if profile.role != option.get("role"):
            continue
        if ("" if profile.student_id is None else str(profile.student_id)) != (student or ""):
            continue
        return profile
    return None


def _selected_profile(session: SessionState, profiles: list[ProfileChoice]) -> ProfileChoice | None:
    if session.user_id is None:
        return None
    for profile in profiles:
        if profile.user_id == session.user_id and profile.school_id == session.school_id and profile.role == session.role:
            return profile
    return None


def _selected_child(session: SessionState, children: list[ChildChoice]) -> ChildChoice | None:
    if session.student_id is None:
        return None
    for child in children:
        if child.student_id == session.student_id:
            return child
    return None


def decide_turn(
    text: str,
    *,
    profiles: list[ProfileChoice],
    children: list[ChildChoice],
    session: SessionState,
    opted_out: bool,
    link_status: str | None = None,
) -> Decision:
    """Choose a profile and, for a parent, a child. Tools run only after that choice is unambiguous."""
    raw = (text or "").strip()
    if is_opt_out_keyword(raw):
        return Decision(kind="opt_out", text=OPT_OUT_ACK, run_tools=False, send=True, clear_selection=True)
    if opted_out:
        return Decision(kind="opted_out", text=OPTED_OUT_REPLY, run_tools=False, send=False)
    if link_status in {"invalid", "expired"}:
        return Decision(kind="link_invalid", text=LINK_INVALID, run_tools=False)
    prefix = LINK_PREFIX if link_status == "linked" else ""

    selected = None if _SWITCH_PROFILE.search(raw) else _selected_profile(session, profiles)
    if selected is None and session.pending == "profile" and not _SWITCH_PROFILE.search(raw):
        chosen = match_option(raw, session.options)
        if chosen is None:
            options = _profile_options(profiles)
            lines = ["Which profile should I use? Reply with the number."]
            lines.extend(f"{item['n']}. {item['label']}" for item in options)
            return _ask("ask_profile", lines, options, "profile", session.pending_text)
        selected = _find_profile(profiles, chosen)
        if selected is not None and session.pending_text:
            raw = session.pending_text

    if selected is None:
        if not profiles:
            return Decision(kind="unknown", text=prefix + UNKNOWN_REPLY, run_tools=False)
        if len(profiles) == 1:
            selected = profiles[0]
        else:
            options = _profile_options(profiles)
            lines = [prefix + "This number matches more than one Sybeez profile. Reply with the number."]
            lines.extend(f"{item['n']}. {item['label']}" for item in options)
            question = None if _GREETING.match(raw) or _SWITCH_PROFILE.search(raw) else raw
            return _ask("ask_profile", lines, options, "profile", question)

    if selected.school_id is None:
        return Decision(kind="no_school", text=prefix + NO_SCHOOL_REPLY, run_tools=False, profile=selected, clear_selection=True)

    if selected.role != "PARENT":
        child = None
        if selected.student_id is not None:
            child = ChildChoice(selected.student_id, selected.label)
        if _GREETING.match(raw) or raw == "":
            return Decision(
                kind="greeting",
                text=prefix + greeting_for(selected.role),
                run_tools=False,
                profile=selected,
                child=child,
            )
        return Decision(kind="answer", text=raw, run_tools=True, profile=selected, child=child)

    child = None if _SWITCH_CHILD.search(raw) else _selected_child(session, children)
    if child is None and session.pending == "child" and not _SWITCH_CHILD.search(raw):
        chosen = match_option(raw, session.options)
        if chosen is None:
            options = _child_options(children)
            lines = ["Which child should I use? Reply with the number."]
            lines.extend(f"{item['n']}. {item['label']}" for item in options)
            return _ask("ask_child", lines, options, "child", session.pending_text)
        for item in children:
            if str(item.student_id) == chosen.get("student_id"):
                child = item
                break
        if session.pending_text:
            raw = session.pending_text

    if child is None:
        if not children:
            return Decision(kind="no_child", text=prefix + NO_CHILD_REPLY, run_tools=False, profile=selected)
        if len(children) == 1:
            child = children[0]
        else:
            options = _child_options(children)
            lines = [prefix + "Which child should I use? Reply with the number."]
            lines.extend(f"{item['n']}. {item['label']}" for item in options)
            question = None if _GREETING.match(raw) or _SWITCH_CHILD.search(raw) else raw
            return _ask("ask_child", lines, options, "child", question)

    if _GREETING.match(raw) or raw == "":
        return Decision(
            kind="greeting",
            text=prefix + greeting_for("PARENT") + f" I'm using {child.label}.",
            run_tools=False,
            profile=selected,
            child=child,
        )
    return Decision(kind="answer", text=raw, run_tools=True, profile=selected, child=child)
