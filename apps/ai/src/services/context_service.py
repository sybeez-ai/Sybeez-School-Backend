from src.modules.chat.schemas import ChatRequest


class ContextService:
    async def build(self, req: ChatRequest) -> str:
        parts = [f"school_id={req.school_id}", f"mode={req.mode}"]
        if req.teaching_record_id:
            parts.append(f"teaching_record_id={req.teaching_record_id}")
        if req.subject_id:
            parts.append(f"subject_id={req.subject_id}")
        if req.class_id:
            parts.append(f"class_id={req.class_id}")
        return "Context: " + ", ".join(parts)
