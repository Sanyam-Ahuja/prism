"""Shared sentence encoder. See docs/ARCHITECTURE.md section 6.

One model instance serves both the query cache and the deeplink catalog index.
CPU-only by design: the GPU is reserved for the Stage 2 extractor, and 7.5 GiB
of VRAM is contended.
"""
from __future__ import annotations

import functools
import os

MODEL_ID = os.environ.get("PRISM_EMBED_MODEL", "BAAI/bge-small-en-v1.5")


@functools.lru_cache(maxsize=1)
def get_encoder():
    """Load the encoder once. Deterministic: eval mode, fixed revision, CPU."""
    import torch
    from sentence_transformers import SentenceTransformer

    torch.manual_seed(0)
    m = SentenceTransformer(MODEL_ID, device="cpu")
    m.eval()
    return m


# bge-* retrieval models are trained with an asymmetric query prefix. Omitting it
# measurably degrades ranking; documents are embedded without it.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def encode_query(texts, normalize: bool = True):
    return encode([QUERY_PREFIX + t for t in texts], normalize=normalize)


def encode(texts, normalize: bool = True):
    import torch
    with torch.inference_mode():
        return get_encoder().encode(list(texts), normalize_embeddings=normalize)
