from __future__ import annotations

import re
from collections import deque
from logging import Logger
from typing import Any

from razdel import sentenize, tokenize

from document_chunker.config import NLPConfig


def preprocess_text(
    logger: Logger,
    nlp_config: NLPConfig,
    text: str,
) -> list[dict[str, Any]]:
    """
    Tokenize and lemmatize raw text into sentence-level units.

    Each returned sentence dict contains:
      - raw: original sentence text
      - lemmas: space-joined lemma tokens (stopwords filtered)
      - pairs: list of (raw_token, lemma) pairs
    """
    sentences = list(sentenize(text))
    processed: list[dict[str, Any]] = []

    for s in sentences:
        raw = s.text.strip()
        if not raw:
            continue

        tokens = [
            t.text.lower()
            for t in tokenize(raw)
            if any(c.isalnum() for c in t.text)
            and (t.text.lower() not in nlp_config.stopwords or t.text.isupper())
        ]
        if not tokens:
            continue

        lemmas: list[str] = []
        pairs: list[tuple[str, str]] = []
        for tok in tokens:
            parsed = nlp_config.morph.parse(tok)
            lemma = parsed[0].normal_form if parsed else tok
            lemmas.append(lemma)
            pairs.append((tok, lemma))

        lemmatized = " ".join(lemmas).strip()
        if lemmatized:
            processed.append({"raw": raw, "lemmas": lemmatized, "pairs": pairs})

    logger.info("Tokenization finished: %d valid sentences", len(processed))
    return processed


def split_long_sentence(
    logger: Logger,
    nlp_config: NLPConfig,
    sentence: dict[str, Any],
    max_tokens: int,
) -> list[dict[str, str]]:
    """Iteratively split an oversized sentence into smaller parts."""
    tokenizer = nlp_config.tokenizer
    token_cache: dict[str, int] = {}

    def count(text: str) -> int:
        if text not in token_cache:
            token_cache[text] = len(tokenizer.encode(text, disallowed_special=()))
        return token_cache[text]

    pairs = sentence.get("pairs", [])
    raw_text = sentence.get("raw", "").strip()
    lemmas_text = sentence.get("lemmas", "").strip()

    if not pairs or not raw_text:
        return [{"raw": raw_text, "lemmas": lemmas_text}]

    threshold = int(max_tokens * 1.5)
    if count(lemmas_text) <= threshold:
        return [{"raw": raw_text, "lemmas": lemmas_text}]

    queue = deque([(0, len(pairs))])
    parts: list[dict[str, str]] = []

    while queue:
        start, end = queue.popleft()
        slice_pairs = pairs[start:end]
        part_raw = " ".join(r for r, _ in slice_pairs)
        part_lemmas = " ".join(l for _, l in slice_pairs)

        if count(part_lemmas) <= threshold or end - start <= 1:
            parts.append({"raw": part_raw, "lemmas": part_lemmas})
            continue

        mid = (start + end) // 2
        queue.append((start, mid))
        queue.append((mid, end))

    return parts or [{"raw": raw_text, "lemmas": lemmas_text}]


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------

def text_chunker(
    logger: Logger,
    nlp_config: NLPConfig,
    text: str,
    max_tokens: int,
    overlap: int,
    min_tokens: int = 3,
) -> list[dict[str, Any]]:
    """Split text into chunks respecting sentence boundaries."""
    processed = preprocess_text(logger, nlp_config, text)
    if not processed:
        return []

    lemma_texts = [s["lemmas"] for s in processed]
    token_counts: list[int] = [0] * len(lemma_texts)
    batch_size = 256

    for start in range(0, len(lemma_texts), batch_size):
        batch = lemma_texts[start:start + batch_size]
        encoded_batch = nlp_config.tokenizer.encode_batch(batch, disallowed_special=())
        for local_idx, encoded in enumerate(encoded_batch):
            token_counts[start + local_idx] = len(encoded)

    sentences = [
        {"raw": s["raw"], "lemmas": s["lemmas"], "tokens": token_counts[i], "pairs": s.get("pairs", [])}
        for i, s in enumerate(processed)
    ]

    chunks: list[dict[str, Any]] = []
    total = len(sentences)
    i = 0

    while i < total:
        start_idx = max(0, i - overlap)
        current_raw: list[str] = []
        current_lemmas: list[str] = []
        current_tokens = 0
        j = start_idx

        while j < total:
            sent = sentences[j]
            sent_tokens = sent["tokens"]

            if sent_tokens > max_tokens:
                if current_raw and current_tokens >= min_tokens:
                    chunks.append({
                        "raw": " ".join(current_raw),
                        "lemmas": " ".join(current_lemmas),
                        "_meta": {"tokens": current_tokens, "start_sentence": start_idx, "end_sentence": j - 1},
                    })
                    current_raw, current_lemmas, current_tokens = [], [], 0

                long_parts = split_long_sentence(logger, nlp_config, sent, max_tokens)
                if long_parts:
                    lemma_parts = [p["lemmas"] for p in long_parts]
                    encoded_parts = nlp_config.tokenizer.encode_batch(lemma_parts, disallowed_special=())
                    for part_idx, (part, encoded) in enumerate(zip(long_parts, encoded_parts), start=1):
                        part_tokens = len(encoded)
                        if part_tokens >= min_tokens:
                            chunks.append({
                                "raw": part["raw"],
                                "lemmas": part["lemmas"],
                                "_meta": {"tokens": part_tokens, "start_sentence": j, "end_sentence": j, "from_long_sentence": True, "part": part_idx},
                            })
                j += 1
                i = max(i + 1, j)
                break

            if current_tokens + sent_tokens > max_tokens:
                break

            current_raw.append(sent["raw"])
            current_lemmas.append(sent["lemmas"])
            current_tokens += sent_tokens
            j += 1

        if current_raw and current_tokens >= min_tokens:
            chunks.append({
                "raw": " ".join(current_raw),
                "lemmas": " ".join(current_lemmas),
                "_meta": {"tokens": current_tokens, "start_sentence": start_idx, "end_sentence": j - 1},
            })

        if j <= i:
            logger.warning("Cursor stuck at %d (j=%d), forcing advance.", i, j)
            i += 1
        else:
            i = j

    avg_tokens = sum(ch["_meta"].get("tokens", 0) for ch in chunks) / max(1, len(chunks))
    logger.info("Chunking finished: %d chunks, avg tokens: %.1f", len(chunks), avg_tokens)
    return chunks


def chunker(
    logger: Logger,
    nlp_config: NLPConfig,
    elements: list[dict[str, Any]],
    max_tokens: int,
    overlap: int,
    min_tokens: int = 3,
) -> list[dict[str, Any]]:
    """
    Process and chunk document elements based on their type (text / table / image).
    """
    chunks: list[dict[str, Any]] = []

    for el in elements:
        element_type = el.get("type")
        content = el.get("content")
        base_meta = dict(el.get("_meta", {}))

        try:
            if element_type == "text":
                if not content or not content[0] or not content[0].strip():
                    continue
                text_chunks = text_chunker(logger, nlp_config, content[0], max_tokens, overlap, min_tokens)
                for chunk in text_chunks:
                    meta = dict(base_meta)
                    meta.update(chunk.get("_meta", {}))
                    chunk["_meta"] = meta
                chunks.extend(text_chunks)

            elif element_type == "table":
                table_marker = base_meta.get("table_marker", "unknown")
                page_meta = {k: v for k, v in base_meta.items() if k in ("page_start", "page_end")}
                row_data: list[tuple[int, str, str]] = []
                for row_idx, row in enumerate(content):
                    if not row or not row.strip():
                        continue
                    processed_rows = preprocess_text(logger, nlp_config, row)
                    if not processed_rows:
                        continue
                    combined_raw = " ".join(p["raw"] for p in processed_rows)
                    combined_lemmas = " ".join(p["lemmas"] for p in processed_rows)
                    row_data.append((row_idx, combined_raw, combined_lemmas))

                if row_data:
                    lemma_texts = [lemmas for _, _, lemmas in row_data]
                    encoded_batch = nlp_config.tokenizer.encode_batch(lemma_texts, disallowed_special=())
                    for (row_idx, combined_raw, combined_lemmas), encoded in zip(row_data, encoded_batch):
                        token_count = len(encoded)
                        if token_count >= min_tokens:
                            chunks.append({
                                "raw": combined_raw,
                                "lemmas": combined_lemmas,
                                "_meta": {"table_row": True, "row_index": row_idx, "table_marker": table_marker, **page_meta},
                            })

            elif element_type == "image":
                if not content or not content[0] or not content[0].strip():
                    continue
                processed_image = preprocess_text(logger, nlp_config, content[0])
                if processed_image:
                    combined_raw = " ".join(p["raw"] for p in processed_image)
                    combined_lemmas = " ".join(p["lemmas"] for p in processed_image)
                    token_count = len(nlp_config.tokenizer.encode(combined_lemmas, disallowed_special=()))
                    if token_count >= min_tokens:
                        chunks.append({"raw": combined_raw, "lemmas": combined_lemmas, "_meta": dict(base_meta)})

            else:
                logger.warning("Unknown element type: %s, skipping", element_type)

        except Exception as e:
            logger.error("Error %s during processing %s", e, element_type)
            raise

    filtered_chunks = [
        chunk for chunk in chunks
        if any(c.isalnum() for c in chunk.get("raw", ""))
        and not re.search(r"\b(nan|inf)\b", chunk.get("raw", ""), re.IGNORECASE)
    ]
    logger.info("Total chunks: %d (filtered out %d)", len(filtered_chunks), len(chunks) - len(filtered_chunks))
    return filtered_chunks
