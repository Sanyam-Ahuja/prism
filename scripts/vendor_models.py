"""Snapshot the encoder into vendor/ so the container needs no network.

The image sets PRISM_EMBED_MODEL to this path and HF_HUB_OFFLINE=1; without it
the first request would try to reach huggingface.co, which is exactly the
cold-start failure the PDF warns about (section 2, component 4).
"""
import os
import sys

DEST = os.environ.get("PRISM_VENDOR_DIR", "vendor/bge-small-en-v1.5")
REPO = os.environ.get("PRISM_EMBED_REPO", "BAAI/bge-small-en-v1.5")
# Pinned: tracking "main" would let the encoder change under us, which would
# silently move every cached embedding and invalidate the calibrated thresholds.
REV = os.environ.get("PRISM_EMBED_REV", "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a")


def main() -> int:
    from huggingface_hub import snapshot_download
    os.makedirs(os.path.dirname(DEST) or ".", exist_ok=True)
    path = snapshot_download(
        repo_id=REPO, revision=REV, local_dir=DEST,
        allow_patterns=["*.json", "*.txt", "*.safetensors", "*.md",
                        "1_Pooling/*"],
        ignore_patterns=["*.onnx", "*.h5", "*.ot", "openvino/*", "onnx/*"],
    )
    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(path) for f in fs)
    print(f"vendored {REPO}@{REV} -> {path}  ({total/1e6:.0f} MB)")

    # Prove it loads offline from the vendored path before we bake an image.
    os.environ["HF_HUB_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(path, device="cpu")
    v = m.encode(["smoke test"], normalize_embeddings=True)
    print(f"offline load OK, dim={v.shape[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
