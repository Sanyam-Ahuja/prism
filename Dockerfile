# Smart Guided Troubleshooting Engine - Theme 02
# Everything, including Ollama and the model:  docker compose up --build
# API only (point OLLAMA_HOST at an Ollama server for live plans):
#   docker build -t prism-engine .
#   docker run --rm -p 8000:8000 -e OLLAMA_HOST=http://host.docker.internal:11434 prism-engine
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=4

# CPU-only torch: the GPU is reserved for the extractor, and the CUDA wheels
# would add ~2 GB to the image for nothing.
COPY requirements.lock .
RUN pip install --no-cache-dir \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.lock \
 && find /usr/local/lib/python3.12/site-packages -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && rm -rf /root/.cache

# Encoder weights are downloaded once at build time (pinned revision) and baked
# into the image, so the running container needs no network (PDF section 2,
# component 4).
COPY scripts/vendor_models.py scripts/
RUN PRISM_VENDOR_DIR=/app/vendor/bge-small-en-v1.5 python scripts/vendor_models.py \
 && rm -rf /root/.cache

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PRISM_EMBED_MODEL=/app/vendor/bge-small-en-v1.5 \
    PRISM_EXTRACT_MODEL=qwen2.5:1.5b

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
