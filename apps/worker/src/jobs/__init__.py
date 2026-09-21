from src.jobs import ai_tasks, documents, emails, embeddings, notifications, progress

HANDLERS = {
    "embeddings": embeddings.handle,
    "documents": documents.handle,
    "notifications": notifications.handle,
    "emails": emails.handle,
    "progress": progress.handle,
    "ai_tasks": ai_tasks.handle,
}
