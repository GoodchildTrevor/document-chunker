# document-chunker

FastAPI microservice that parses documents (PDF, DOCX, DOC, XLSX) and returns lemmatized text chunks ready for embedding.

## API

### `POST /chunk`

Upload a file and receive a list of chunks.

**Request:** `multipart/form-data`
- `file` — document file (PDF / DOCX / DOC / XLSX)
- `chunk_size` *(optional, int, default 512)* — max tokens per chunk
- `overlap` *(optional, int, default 1)* — sentence overlap between chunks

**Response:** `application/json`
```json
{
  "file_name": "report.pdf",
  "file_format": ".pdf",
  "creation_date": "2023-01-01T00:00:00+00:00",
  "modification_date": "2024-06-01T00:00:00+00:00",
  "chunks": [
    {
      "raw": "Original sentence text.",
      "lemmas": "original sentence text",
      "meta": {"page_start": 1, "page_end": 1, "tokens": 48}
    }
  ]
}
```

### `GET /health`

Returns `{"status": "ok"}`.

## Install

```bash
# From PyPI-style git tag:
pip install git+https://github.com/GoodchildTrevor/document-chunker@v0.1.0

# Or locally:
pip install -e .
```

## Run

```bash
uvicorn document_chunker.main:app --host 0.0.0.0 --port 8001
```

Or via Docker:
```bash
docker build -t document-chunker .
docker run -p 8001:8001 -e FILE_WORKER_URL=http://your-ocr-service/parse document-chunker
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `FILE_WORKER_URL` | `http://localhost:9000/parse` | OCR/extraction service for PDF pages and images |
| `LIBREOFFICE_TIMEOUT` | `60` | Seconds allowed for `.doc` → `.docx` conversion |
| `CHUNK_SIZE` | `512` | Default max tokens per chunk |
| `OVERLAP` | `1` | Default sentence overlap |
