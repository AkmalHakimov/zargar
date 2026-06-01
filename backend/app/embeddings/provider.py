import hashlib
import math
import random

from app.config import Settings
from app.embeddings.base import EmbeddingProvider


class HashEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dimensions: int = 1536):
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)
        values = [rng.uniform(-1, 1) for _ in range(self.dimensions)]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return HashEmbeddingProvider(settings.embedding_dimensions)

