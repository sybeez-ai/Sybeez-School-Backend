from src.jobs import (
    ai_tasks,
    deliveries,
    documents,
    emails,
    embeddings,
    fee_reminders,
    notifications,
    payroll,
    progress,
)

HANDLERS = {
    "embeddings": embeddings.handle,
    "documents": documents.handle,
    "notifications": notifications.handle,
    "emails": emails.handle,
    "deliveries": deliveries.handle,
    "progress": progress.handle,
    "ai_tasks": ai_tasks.handle,
    "payroll_generate": payroll.handle,
    "fee_reminders": fee_reminders.handle,
}
