from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Sequence
from typing import Literal, Protocol

import httpx

from brain.config import Settings

InputType = Literal["document", "query"]


class Embedder(Protocol):
    name: str
    dim: int
    default_min_similarity: float

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]: ...


class HashEmbedder:
    """Deterministic bag-of-words hashing. Tests only: it measures word overlap, not meaning."""

    name = "hash"
    dim = 256
    default_min_similarity = 0.05

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=4).digest()
            bucket = int.from_bytes(digest[:3], "big") % self.dim
            vec[bucket] += 1.0 if digest[3] % 2 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class LocalEmbedder:
    """fastembed BAAI/bge-small-en-v1.5 on CPU (ONNX); downloads the model once on first use."""

    name = "local:bge-small-en-v1.5"
    dim = 384
    default_min_similarity = 0.60
    _QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

    def __init__(self) -> None:
        from fastembed import TextEmbedding  # noqa: PLC0415  # heavy (onnxruntime); only load when chosen

        self._model = TextEmbedding("BAAI/bge-small-en-v1.5")

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        prepared = [self._QUERY_INSTRUCTION + t if input_type == "query" else t for t in texts]
        vectors = await asyncio.to_thread(lambda: list(self._model.embed(prepared)))
        return [[float(x) for x in v] for v in vectors]


class VoyageEmbedder:
    name = "voyage:voyage-4"
    dim = 1024
    default_min_similarity = 0.35
    _URL = "https://api.voyageai.com/v1/embeddings"
    _BATCH = 64

    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._client = client

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self._BATCH):
            batch = list(texts[start : start + self._BATCH])
            response = await self._client.post(
                self._URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": "voyage-4",
                    "input": batch,
                    "input_type": input_type,
                    "output_dimension": self.dim,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            out.extend([float(x) for x in item["embedding"]] for item in data)
        return out


def build_embedder(settings: Settings, http: httpx.AsyncClient) -> Embedder:
    match settings.resolved_embedder:
        case "voyage":
            if not settings.voyage_api_key:
                raise RuntimeError("BRAIN_EMBEDDER=voyage needs VOYAGE_API_KEY")
            return VoyageEmbedder(settings.voyage_api_key, http)
        case "local":
            return LocalEmbedder()
        case "hash":
            return HashEmbedder()
