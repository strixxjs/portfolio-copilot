"""Embedders. `fastembed` (bge-small, ONNX on CPU) is the real one.

`hash` is a deterministic signed feature-hashing embedder used only where model downloads are
impossible (sandboxed environments). It is lexical, not semantic; every result produced with it
is labelled `embedding_provider=hash`.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from itertools import pairwise
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dim: int

    async def embed_query(self, text: str) -> list[float]: ...

    async def embed_passages(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    def __init__(self, model: str, dim: int, cache_dir: str, batch_size: int = 32) -> None:
        from fastembed import TextEmbedding

        self.name = model
        self.dim = dim
        self._batch = batch_size
        self._model = TextEmbedding(model, cache_dir=cache_dir)

    async def embed_query(self, text: str) -> list[float]:
        vecs = await asyncio.to_thread(lambda: list(self._model.query_embed(text)))
        return [float(x) for x in vecs[0]]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        vecs = await asyncio.to_thread(lambda: list(self._model.passage_embed(texts, batch_size=self._batch)))
        return [[float(x) for x in v] for v in vecs]


class HashEmbedder:
    def __init__(self, dim: int) -> None:
        self.name = "hash"
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN.findall(text.lower())
        grams = tokens + [f"{a} {b}" for a, b in pairwise(tokens)]
        for g in grams:
            h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "little")
            vec[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]


def build_embedder(provider: str, model: str, dim: int, cache_dir: str) -> Embedder:
    if provider == "hash":
        return HashEmbedder(dim)
    return FastEmbedder(model, dim, cache_dir)
