from src.providers.embeddings import embed_texts


class EmbeddingService:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await embed_texts(texts)
