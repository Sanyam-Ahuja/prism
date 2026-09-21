# Smart Guided Troubleshooting Engine - Theme 02
# Build:  podman build -t prism-engine .
# Run:    podman run --rm -p 8000:8000 prism-engine
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PRISM_EMBED_MODEL=/app/vendor/bge-small-en-v1.5 \
    PRISM_EXTRACT_MODEL=gemma3:4b \
    OMP_NUM_THREADS=4

# CPU-only torch: the GPU is reserved for the extractor, and the CUDA wheels
# would add ~2 GB to the image for nothing.
COPY requirements.lock .
RUN pip install --no-cache-dir \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.lock \
 && find /usr/local/lib/python3.12/site-packages -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && rm -rf /root/.cache

# Encoder weights, so cold start needs no network (PDF section 2, component 4).
COPY vendor/bge-small-en-v1.5 /app/vendor/bge-small-en-v1.5

COPY schema.py ./
COPY engine/ engine/
COPY validators/ validators/
COPY api/ api/
COPY scripts/ scripts/
COPY data/ data/
COPY artifacts/ artifacts/

RUN useradd -r -u 10001 prism && chown -R prism:prism /app
USER prism

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=90s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
