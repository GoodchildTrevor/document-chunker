import asyncio
import os
import tempfile
from logging import Logger
from pathlib import Path
from typing import Any, AsyncIterator

import aiohttp
import fitz


async def iter_pdf_text_batches(
    logger: Logger,
    file_worker_url: str,
    file_path: Path,
    session: aiohttp.ClientSession,
    page_batch_size: int = 1,
    max_retries: int = 2,
) -> AsyncIterator[dict[str, Any]]:
    """
    Yield text for PDF in page batches.

    Each yielded item:
      {
        "page_start": int,
        "page_end": int,
        "text": str,
        "success": bool,
      }
    """
    try:
        with fitz.open(file_path) as doc:
            total_pages = len(doc)
            logger.info("PDF has %d pages", total_pages)
    except Exception as e:
        logger.error("Failed to open PDF %s: %s", file_path, e)
        raise RuntimeError(f"Cannot open PDF: {e}") from e

    if total_pages == 0:
        logger.warning("PDF has zero pages, yielding nothing")
        return

    for batch_start in range(0, total_pages, page_batch_size):
        batch_end = min(batch_start + page_batch_size, total_pages)
        batch_idx = batch_start // page_batch_size + 1
        logger.info(
            "Processing PDF batch %d: pages %d-%d of %d",
            batch_idx, batch_start + 1, batch_end, total_pages,
        )

        temp_pdf_path: str | None = None

        try:
            with fitz.open(file_path) as src_doc:
                dst_doc = fitz.open()
                try:
                    for page_num in range(batch_start, batch_end):
                        dst_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                        temp_pdf_path = tmp.name
                        dst_doc.save(temp_pdf_path)
                finally:
                    dst_doc.close()

            last_error: str | None = None
            batch_success = False

            for attempt in range(1, max_retries + 1):
                try:
                    with open(temp_pdf_path, "rb") as f:
                        file_data = f.read()

                    form = aiohttp.FormData()
                    form.add_field(
                        "file", file_data,
                        filename=os.path.basename(temp_pdf_path),
                        content_type="application/pdf",
                    )

                    async with session.post(
                        file_worker_url,
                        data=form,
                        timeout=aiohttp.ClientTimeout(total=600),
                    ) as response:
                        if response.status == 200:
                            batch_text = await response.text() or ""
                            logger.info("Batch %d succeeded on attempt %d", batch_idx, attempt)
                            batch_success = True
                            yield {
                                "page_start": batch_start + 1,
                                "page_end": batch_end,
                                "text": batch_text,
                                "success": True,
                            }
                            break

                        error_text = await response.text() if response.content_length else "no content"
                        last_error = f"Worker returned {response.status}: {error_text[:200]}"
                        raise RuntimeError(last_error)

                except (aiohttp.ClientError, RuntimeError) as e:
                    last_error = str(e)
                    logger.warning("Batch %d attempt %d/%d failed: %s", batch_idx, attempt, max_retries, e)
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** (attempt - 1) * 2)
                    else:
                        logger.error("All %d attempts failed for batch %d", max_retries, batch_idx)
                        yield {
                            "page_start": batch_start + 1,
                            "page_end": batch_end,
                            "text": f"\n[FAILED BATCH: pages {batch_start+1}-{batch_end}] {last_error or ''}\n",
                            "success": False,
                        }

        except Exception as e:
            logger.error("Unexpected error processing batch %d: %s", batch_idx, e)
            yield {
                "page_start": batch_start + 1,
                "page_end": batch_end,
                "text": f"\n[CRITICAL FAILURE: pages {batch_start+1}-{batch_end}]\n",
                "success": False,
            }
        finally:
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                try:
                    os.unlink(temp_pdf_path)
                except OSError as e:
                    logger.warning("Could not delete temp file %s: %s", temp_pdf_path, e)
