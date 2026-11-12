"""Evening "how was my child's day" summary for opted-in parents.

Facts only (attendance, homework, teacher updates) taken from the database and put into fixed,
reviewed phrases per language - no AI is involved, so nothing can be invented or mistranslated live.
Languages without a reviewed phrase table fall back to English (flagged for native review).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import func, select

from src.modules.fees.reminder_plan import school_now

logger = logging.getLogger("sybeez.daily_summary")

SEND_FROM = time(18, 30)
SEND_UNTIL = time(20, 30)  # before the default 21:00 quiet hours

# key -> {lang: text}. {n} {child} are the only placeholders.
PHRASES: dict[str, dict[str, str]] = {
    "title": {
        "en": "How {child}'s day went",
        "hi": "{child} का आज का दिन कैसा रहा",
        "ta": "{child} இன்றைய நாள் எப்படி இருந்தது",
        "te": "{child} ఈ రోజు ఎలా గడిచింది",
        "kn": "{child} ಇಂದಿನ ದಿನ ಹೇಗಿತ್ತು",
        "ml": "{child} ഇന്ന് എങ്ങനെയായിരുന്നു",
    },
    "PRESENT": {"en": "Present at school.", "hi": "स्कूल में उपस्थित।", "ta": "பள்ளியில் வந்திருந்தார்.", "te": "పాఠశాలకు హాజరయ్యారు.", "kn": "ಶಾಲೆಗೆ ಹಾಜರಾಗಿದ್ದರು.", "ml": "സ്കൂളിൽ ഹാജരായി."},
    "ABSENT": {"en": "Absent today.", "hi": "आज अनुपस्थित।", "ta": "இன்று வரவில்லை.", "te": "ఈ రోజు గైర్హాజరు.", "kn": "ಇಂದು ಗೈರುಹಾಜರಾಗಿದ್ದರು.", "ml": "ഇന്ന് ഹാജരായില്ല."},
    "LATE": {"en": "Arrived late.", "hi": "देर से पहुँचे।", "ta": "தாமதமாக வந்தார்.", "te": "ఆలస్యంగా వచ్చారు.", "kn": "ತಡವಾಗಿ ಬಂದರು.", "ml": "വൈകിയാണ് എത്തിയത്."},
    "HALF_DAY": {"en": "Attended half a day.", "hi": "आधे दिन उपस्थित।", "ta": "அரை நாள் வந்திருந்தார்.", "te": "అర్ధ రోజు హాజరయ్యారు.", "kn": "ಅರ್ಧ ದಿನ ಹಾಜರಾಗಿದ್ದರು.", "ml": "അര ദിവസം ഹാജരായി."},
    "EXCUSED": {"en": "Excused absence.", "hi": "अनुमति सहित अनुपस्थित।", "ta": "அனுமதியுடன் விடுப்பு.", "te": "అనుమతితో గైర్హాజరు.", "kn": "ಅನುಮತಿಯೊಂದಿಗೆ ಗೈರು.", "ml": "അനുമതിയോടെ അവധി."},
    "hw_new": {"en": "{n} new homework today.", "hi": "आज {n} नया होमवर्क मिला।", "ta": "இன்று {n} புதிய வீட்டுப்பாடம்.", "te": "ఈ రోజు {n} కొత్త హోంవర్క్.", "kn": "ಇಂದು {n} ಹೊಸ ಹೋಮ್‌ವರ್ಕ್.", "ml": "ഇന്ന് {n} പുതിയ ഹോംവർക്ക്."},
    "hw_due": {"en": "{n} homework due tomorrow.", "hi": "कल {n} होमवर्क जमा करना है।", "ta": "நாளை {n} வீட்டுப்பாடம் ஒப்படைக்க வேண்டும்.", "te": "రేపు {n} హోంవర్క్ సమర్పించాలి.", "kn": "ನಾಳೆ {n} ಹೋಮ್‌ವರ್ಕ್ ಸಲ್ಲಿಸಬೇಕು.", "ml": "നാളെ {n} ഹോംവർക്ക് സമർപ്പിക്കണം."},
    "updates": {"en": "{n} new note from the teacher.", "hi": "शिक्षक का {n} नया संदेश।", "ta": "ஆசிரியரிடமிருந்து {n} புதிய குறிப்பு.", "te": "ఉపాధ్యాయుల నుండి {n} కొత్త సందేశం.", "kn": "ಶಿಕ್ಷಕರಿಂದ {n} ಹೊಸ ಸಂದೇಶ.", "ml": "അധ്യാപകരിൽ നിന്ന് {n} പുതിയ കുറിപ്പ്."},
}
REVIEWED = {"en", "hi", "ta", "te", "kn", "ml"}


def phrase(key: str, lang: str, **kw) -> str:
    table = PHRASES[key]
    return table.get(lang, table["en"]).format(**kw)


def compose(lang: str, child: str, attendance: str | None, hw_new: int, hw_due: int, updates: int) -> tuple[str, str] | None:
    """(title, body) in the parent's language, or None when there is nothing factual to say."""
    lines: list[str] = []
    if attendance and attendance in PHRASES:
        lines.append(phrase(attendance, lang))
    if hw_new:
        lines.append(phrase("hw_new", lang, n=hw_new))
    if hw_due:
        lines.append(phrase("hw_due", lang, n=hw_due))
    if updates:
        lines.append(phrase("updates", lang, n=updates))
    if not lines:
        return None
    return phrase("title", lang, child=child), " ".join(lines)


def in_summary_window(local_now: datetime) -> bool:
    return SEND_FROM <= local_now.time() < SEND_UNTIL


async def run_for_school(db, school_id: UUID, *, now: datetime | None = None) -> dict:
    """Send today's summary once per parent/child. Safe to call every few minutes."""
    from src.db.models import (
        Attendance,
        Enrollment,
        Homework,
        Parent,
        ParentMessagePreference,
        SchoolAlertSettings,
        Student,
        StudentParent,
        StudentProgressUpdate,
        User,
    )
    from src.modules.alerts.service import dispatch_alert, school_display_name
    from src.modules.notifications.service import create_in_app_notification

    cfg = await db.get(SchoolAlertSettings, school_id)
    local = school_now(cfg.timezone if cfg else None, now)
    if not in_summary_window(local):
        return {"sent": 0, "outside_window": True}
    today: date = local.date()
    tomorrow = today + timedelta(days=1)

    prefs = (
        await db.execute(
            select(ParentMessagePreference).where(
                ParentMessagePreference.school_id == school_id,
                ParentMessagePreference.whatsapp_opt_in.is_(True),  # the parent's explicit consent
            )
        )
    ).scalars().all()
    if not prefs:
        return {"sent": 0}
    school_name = await school_display_name(db, school_id)
    sent = skipped = 0
    dispatched: set = set()
    for pref in prefs:
        rows = (
            await db.execute(
                select(StudentParent.student_id)
                .join(Parent, Parent.id == StudentParent.parent_id)
                .where(Parent.user_id == pref.user_id, StudentParent.school_id == school_id)
            )
        ).scalars().all()
        for student_id in rows:
            student = await db.get(Student, student_id)
            if student is None:
                continue
            user = await db.get(User, student.user_id)
            child = (user.full_name if user else "Your child").split(" ")[0]
            att = (
                await db.execute(
                    select(Attendance.status).where(
                        Attendance.student_id == student_id, Attendance.attended_on == today
                    ).limit(1)
                )
            ).scalar_one_or_none()
            class_ids = (
                await db.execute(
                    select(Enrollment.class_id).where(Enrollment.student_id == student_id, Enrollment.status == "ACTIVE")
                )
            ).scalars().all()
            hw_new = hw_due = 0
            if class_ids:
                day_start = datetime.combine(today, time.min, tzinfo=local.tzinfo)
                day_end = day_start + timedelta(days=1)
                hw_new = (
                    await db.execute(
                        select(func.count()).select_from(Homework).where(
                            Homework.class_id.in_(class_ids),
                            Homework.status == "PUBLISHED",
                            Homework.assigned_at >= day_start,
                            Homework.assigned_at < day_end,
                        )
                    )
                ).scalar_one()
                hw_due = (
                    await db.execute(
                        select(func.count()).select_from(Homework).where(
                            Homework.class_id.in_(class_ids),
                            Homework.status == "PUBLISHED",
                            Homework.due_at >= day_end,
                            Homework.due_at < day_end + timedelta(days=1),
                        )
                    )
                ).scalar_one()
            updates = (
                await db.execute(
                    select(func.count()).select_from(StudentProgressUpdate).where(
                        StudentProgressUpdate.student_id == student_id,
                        StudentProgressUpdate.is_published.is_(True),
                        StudentProgressUpdate.published_at >= datetime.combine(today, time.min, tzinfo=local.tzinfo),
                    )
                )
            ).scalar_one()
            lang = pref.preferred_language or "en"
            msg = compose(lang, child, att, int(hw_new), int(hw_due), int(updates))
            if msg is None:
                skipped += 1
                continue
            title, body = msg
            # In-app: unique (event_key, user) makes a second run a no-op.
            await create_in_app_notification(
                db,
                school_id=school_id,
                user_id=pref.user_id,
                title=title,
                body=body,
                category="SYSTEM",
                link_path="/parent/dashboard",
                event_key=f"DAILY_SUMMARY:{student_id}:{today.isoformat()}",
            )
            # WhatsApp: once per child. dispatch_alert resolves each linked parent, honours that parent's own
            # consent, language, phone, opt-out and quiet hours, and dedupes per parent and channel.
            if student_id not in dispatched:
                dispatched.add(student_id)
                facts = (att, int(hw_new), int(hw_due), int(updates))
                localized = {}
                for code in REVIEWED:
                    m = compose(code, child, *facts)
                    if m:
                        localized[code] = {"title": m[0], "preview": m[1]}
                en = compose("en", child, *facts)
                await dispatch_alert(
                    db,
                    school_id=school_id,
                    event_key="progress_update",
                    variables={"title": en[0], "preview": en[1], "school_name": school_name},
                    student_id=student_id,
                    dedupe_key=f"daily_summary:{student_id}:{today.isoformat()}",
                    link_path="/parent/dashboard",
                    localized_variables=localized,
                    urgent=False,
                )
            sent += 1
    await db.commit()
    return {"sent": sent, "skipped_nothing_to_report": skipped}
