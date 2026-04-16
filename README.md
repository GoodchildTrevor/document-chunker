# document-chunker

FastAPI microservice that parses documents (PDF, DOCX, DOC, XLSX) and returns lemmatized text chunks ready for vector embedding. Also supports chunking plain text (model responses, user messages) via a dedicated endpoint.

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

---

### `POST /chunk-text`

Chunk plain text (e.g. a model response or a user message) without uploading a file.

**Request:** `application/json`
```json
{
  "text": "Текст ответа модели или вопроса пользователя...",
  "chunk_size": 512,
  "overlap": 1
}
```
- `text` — raw text to chunk *(required)*
- `chunk_size` *(optional)* — max tokens per chunk, uses `CHUNK_SIZE` env default if omitted
- `overlap` *(optional)* — sentence overlap, uses `OVERLAP` env default if omitted

**Fast path:** if the entire text fits within `chunk_size` tokens, it is returned immediately as a single chunk — no sentence splitting or lemmatization is performed. The chunk will have `"lemmas": ""` and `"meta": {"tokens": N, "single_chunk": true}`.

**Full path:** if the text exceeds `chunk_size` tokens, the full pipeline runs (sentence segmentation → lemmatization → greedy packing), identical to `/chunk`.

**Response:** same `ChunkResponse` shape as `/chunk`, with `file_name: ""` and `file_format: "text"`.

```json
{
  "file_name": "",
  "file_format": "text",
  "creation_date": "",
  "modification_date": "",
  "chunks": [
    {
      "raw": "Полный текст, если он короткий.",
      "lemmas": "",
      "meta": {"tokens": 12, "single_chunk": true}
    }
  ]
}
```

---

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

`FILE_WORKER_URL` is **required** for `/chunk` (file parsing). It is not used by `/chunk-text`.

| Variable | Default | Description |
|---|---|---|
| `FILE_WORKER_URL` | *(required)* | URL of the OCR/extraction service for PDF pages and images |
| `LIBREOFFICE_TIMEOUT` | `60` | Seconds allowed for `.doc` → `.docx` conversion |
| `CHUNK_SIZE` | `512` | Default max tokens per chunk |
| `OVERLAP` | `1` | Default sentence overlap between adjacent chunks |
| `APP_PORT` | `8001` | Host port (docker-compose only) |

## External dependencies

`/chunk` requires a running **file-worker** service reachable at `FILE_WORKER_URL`. This service handles OCR for PDF pages and image extraction. It is not included in this repository — configure `FILE_WORKER_URL` to point to your own deployment.

`/chunk-text` has no external dependencies beyond the Python packages listed in `pyproject.toml`.
