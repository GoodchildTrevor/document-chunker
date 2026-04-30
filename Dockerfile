FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY document_chunker/ ./document_chunker/

EXPOSE 8001

CMD ["uvicorn", "document_chunker.main:app", "--host", "0.0.0.0", "--port", "8001"]
