"""Cross-encoder reranker (fastembed, CPU), behind RERANK_ENABLED."""

from __future__ import annotations

import asyncio


class Reranker:
    def __init__(self, model: str, cache_dir: str) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.name = model
        self._model = TextCrossEncoder(model, cache_dir=cache_dir)

    async def scores(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        out = await asyncio.to_thread(lambda: list(self._model.rerank(query, documents)))
        return [float(s) for s in out]
