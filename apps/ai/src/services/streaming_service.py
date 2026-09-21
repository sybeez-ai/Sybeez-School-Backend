from collections.abc import AsyncIterator


async def stream_tokens(text: str) -> AsyncIterator[str]:
    for word in text.split():
        yield word + " "
