from src.core.config import get_settings
from src.providers.base import LLMProvider


class EchoLLM(LLMProvider):
    """Dev fallback when no OPENAI_API_KEY is set."""

    async def complete(self, system: str, user: str) -> str:
        return (
            "I am your AI Teacher. Based on today's lesson, let's work through this together.\n\n"
            f"You asked: {user[:500]}"
        )


def get_llm() -> LLMProvider:
    settings = get_settings()
    if settings.openai_api_key:
        # Plug real OpenAI/Azure provider here
        return EchoLLM()
    return EchoLLM()
