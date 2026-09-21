from src.core.config import get_settings


async def embed_texts(texts: list[str]) -> list[list[float]]:
    dim = get_settings().ai_embedding_dim
    # Placeholder zero vectors — worker/real provider fills production embeddings
    return [[0.0] * dim for _ in texts]
