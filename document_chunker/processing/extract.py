from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from logging import Logger
from pathlib import Path
from typing import Any

import aiohttp
import fitz
from docx2python import docx2python

from document_chunker.config import NLPConfig
from document_chunker.processing.utils import normalize_datetime, safe_decode
from document_chunker.processing.pdf import iter_pdf_text_batches
from document_chunker.processing.word import convert_doc_to_docx, word_to_text
from document_chunker.processing.excel import excel_to_text, extract_excel_metadata


def detect_and_extract_tables(logger: Logger, text: str) -> dict[str, Any]:
    """
    Detect and extract tables from text returned by external OCR service.
    Tables are expected to be JSON-like arrays of rows inside square brackets.
    """
    table_pattern = r"\[(.*?)\]"
    tables: list[list[str]] = []
    table_map: dict[str, list[str]] = {}

    def replacer(match: re.Match[str]) -> str:
        nonlocal tables
        content = match.group(1).strip()
        if not content:
            return match.group(0)
        if not (content.startswith("'") or content.startswith('"')):
            return match.group(0)
        try:
            data = json.loads(f"[{content}]")
            if isinstance(data, list) and all(isinstance(row, str) for row in data):
                marker = f"[TABLE_{len(tables) + 1}]"
                tables.append(data)
                table_map[marker] = data
                return marker
        except json.JSONDecodeError:
            logger.debug("JSON parse failed for table candidate: '[%s...]'", content[:50])

        rows: list[str] = []
        for part in content.split(","):
            part = part.strip()
            if not part:
                continue
            if (part.startswith("'") and part.endswith("'")) or (part.startswith('"') and part.endswith('"')):
                part = part[1:-1]
            if part:
                rows.append(part)
        if rows:
            marker = f"[TABLE_{len(tables) + 1}]"
            tables.append(rows)
            table_map[marker] = rows
            return marker
        return match.group(0)

    cleaned_text = re.sub(table_pattern, replacer, text, flags=re.DOTALL)
    return {"cleaned_text": cleaned_text, "tables": tables, "table_map": table_map}


async def extract_text_metadata(
    logger: Logger,
    file_worker_url: str,
    libreoffice_timeout: int,
    file_path: Path,
    file_format: str,
    nlp_config: NLPConfig,
    session: aiohttp.ClientSession,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract textual content and metadata from a given file.
    AppConfig replaced by plain scalar arguments — no external config dependency.
    """
    elements: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    fallback_dt = datetime(1900, 1, 1, tzinfo=timezone.utc)

    # ------------------------------------------------------------------ PDF
    if file_format == ".pdf":
        try:
            with fitz.open(file_path) as doc:
                metadata["creation_date"] = normalize_datetime(doc.metadata.get("creationDate", ""))
                metadata["modification_date"] = normalize_datetime(doc.metadata.get("modDate", ""))
        except Exception as e:
            logger.warning("Failed to extract PDF metadata: %s", e)
            metadata["creation_date"] = fallback_dt
            metadata["modification_date"] = fallback_dt

        try:
            async for part in iter_pdf_text_batches(
                logger, file_worker_url, file_path, session, page_batch_size=1
            ):
                page_start = part["page_start"]
                page_end = part["page_end"]
                text = part["text"]

                result = detect_and_extract_tables(logger, text)
                cleaned_text = result["cleaned_text"]
                table_map = result["table_map"]

                if cleaned_text.strip():
                    elements.append({
                        "type": "text",
                        "content": [cleaned_text],
                        "_meta": {"page_start": page_start, "page_end": page_end},
                    })
                for marker, table_rows in table_map.items():
                    if table_rows:
                        elements.append({
                            "type": "table",
                            "content": table_rows,
                            "_meta": {"table_marker": marker, "page_start": page_start, "page_end": page_end},
                        })
        except Exception as e:
            logger.error("PDF text extraction failed: %s", e)
            raise RuntimeError(f"PDF processing failed: {e}") from e

    # ------------------------------------------------------------ DOC / DOCX
    elif file_format in (".docx", ".doc"):
        current_path = file_path
        if file_format == ".doc":
            try:
                converted_path = await convert_doc_to_docx(file_path, logger, libreoffice_timeout)
                if converted_path is None:
                    raise RuntimeError("DOC to DOCX conversion failed")
                current_path = Path(converted_path)
            except Exception as e:
                logger.error("Failed to convert .doc to .docx: %s", e)
                raise RuntimeError(f"DOC conversion failed: {e}") from e

        try:
            with docx2python(current_path) as doc_result:
                raw_metadata = {k: safe_decode(v) for k, v in doc_result.core_properties.items()}
                metadata["creation_date"] = normalize_datetime(raw_metadata.get("created", ""))
                metadata["modification_date"] = normalize_datetime(raw_metadata.get("modified", ""))
        except Exception as e:
            logger.warning("Failed to extract DOCX metadata: %s", e)
            metadata["creation_date"] = fallback_dt
            metadata["modification_date"] = fallback_dt

        try:
            elements = await word_to_text(logger, file_worker_url, current_path, session)
        except Exception as e:
            logger.error("Failed to extract text from Word document: %s", e)
            raise RuntimeError(f"Word text extraction failed: {e}") from e

    # ----------------------------------------------------------------- XLSX
    elif file_format == ".xlsx":
        elements = excel_to_text(file_path, logger, nlp_config)
        metadata = extract_excel_metadata(file_path, logger)

    # ----------------------------------------------------------------- DJVU
    elif file_format == ".djvu":
        logger.warning("DJVU format is not implemented yet: %s", file_path)
        return [], {}

    else:
        raise ValueError(f"Unsupported file format: {file_format}")

    return elements, metadata
