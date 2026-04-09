# document-chunker

FastAPI microservice that parses documents (PDF, DOCX, DOC, XLSX) and returns lemmatized text chunks ready for vector embedding.

## API

### `POST /chunk`

Upload a file and receive a list of chunks.

**Request:** `multipart/form-data`
- `file` — document file (PDF / DOCX / DOC / XLSX)
- `chunk_size` *(optional, int, default from `CHUNK_SIZE` env)* — max tokens per chunk
- `overlap` *(optional, int, default from `OVERLAP` env)* — sentence overlap between chunks

**Response:** `application/json`
```json
{
  "file_name": "report.pdf",
  "file_format": ".pdf",
  "creation_date": "2023-01-01T00:00:00+00:00",
  "modification_date": "2024-06-01T00:00:00+00:00",
  "chunks": [
    {
      "raw": "Оригинальный текст предложения.",
      "lemmas": "оригинальный текст предложение",
      "meta": {"page_start": 1, "page_end": 1, "tokens": 48}
    }
  ]
}
```

### `GET /health`

Returns `{"status": "ok"}`.

---

## Chunking algorithm

The chunker splits document text into semantically coherent chunks using **sentence boundaries** rather than fixed character or token windows. The pipeline has three stages:

### 1. Sentence segmentation

`razdel.sentenize()` splits the raw text into sentences with correct handling of Russian abbreviations, initials, and punctuation edge cases. Each sentence is filtered — sentences that produce zero meaningful tokens after stopword removal are discarded.

### 2. Lemmatization

Every sentence is tokenized with `razdel.tokenize()`. Tokens are lowercased and filtered: stopwords (Russian, via `stop_words`) and punctuation-only tokens are dropped. Each remaining token is lemmatized with `pymorphy3.MorphAnalyzer` (the first parse candidate is used). The result is two parallel representations per sentence:

- **`raw`** — the original text, preserved verbatim for display and retrieval.
- **`lemmas`** — space-joined normal forms, used for token counting and BM25 sparse vectors.

### 3. Greedy sentence-window packing

Token budget is measured on the `lemmas` string using `tiktoken` (`cl100k_base` encoding). The chunker iterates sentences greedily:

```
while sentences remain:
    start a new chunk from cursor - overlap sentences (for context continuity)
    keep appending sentences until budget (chunk_size tokens) is exceeded
    emit the chunk, advance cursor to where the budget ran out
```

**Overlap** (`overlap=1` by default) means the last sentence of the previous chunk is repeated as the first sentence of the next one. This prevents context loss at chunk boundaries without duplicating large amounts of text.

**Oversized sentences** (a single sentence exceeding `chunk_size`) are handled separately: the sentence is recursively split in half by token pairs until each part fits within `1.5 × chunk_size`. These parts are emitted as individual chunks and tagged with `"from_long_sentence": true` in `meta`.

**Tables** are treated differently: each row is processed as an independent unit (no multi-row packing), so structured data is never merged across rows.

### Example

Given `chunk_size=512, overlap=1` and a document with 20 sentences of ~30 tokens each:

```
Chunk 1: sentences  1–17  (~510 tokens)
Chunk 2: sentences 17–20  (sentence 17 repeated for overlap)
```

Each chunk carries `meta` with token count, sentence indices, and (for PDFs) the source page range.

---

## Install

```bash
# Locally:
pip install -e .
```

## Run

```bash
uvicorn document_chunker.main:app --host 0.0.0.0 --port 8001
```

Or via Docker Compose:
```bash
cp .env.example .env
# Set FILE_WORKER_URL in .env
docker compose up --build
```

## Environment variables

`FILE_WORKER_URL` is **required** — the service will refuse to start without it.

| Variable | Default | Description |
|---|---|---|
| `FILE_WORKER_URL` | *(required)* | URL of the OCR/extraction service for PDF pages and images |
| `LIBREOFFICE_TIMEOUT` | `60` | Seconds allowed for `.doc` → `.docx` conversion |
| `CHUNK_SIZE` | `512` | Default max tokens per chunk |
| `OVERLAP` | `1` | Default sentence overlap between adjacent chunks |
| `APP_PORT` | `8001` | Host port (docker-compose only) |
