FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    PRISM_EXTRACT_MODEL=gemma3:4b

COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

# Vendor the encoder weights so no network is needed at runtime or cold start.
COPY vendor/bge-small-en-v1.5 /app/vendor/bge-small-en-v1.5
ENV PRISM_EMBED_MODEL=/app/vendor/bge-small-en-v1.5

COPY schema.py Makefile ./
COPY engine/ engine/
COPY validators/ validators/
COPY api/ api/
COPY scripts/ scripts/
COPY data/ data/
COPY artifacts/ artifacts/

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/health').status_code==200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
