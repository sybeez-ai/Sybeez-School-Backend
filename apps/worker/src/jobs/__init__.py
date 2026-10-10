from src.jobs import ai_tasks, deliveries, documents, emails, embeddings, fee_reminders, notifications, progress

HANDLERS = {
    "embeddings": embeddings.handle,
    "documents": documents.handle,
    "notifications": notifications.handle,
    "emails": emails.handle,
    "deliveries": deliveries.handle,
    "progress": progress.handle,
    "ai_tasks": ai_tasks.handle,
    "fee_reminders": fee_reminders.handle,
}
