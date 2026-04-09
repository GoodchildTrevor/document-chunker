import asyncio
import re
import shutil
import tempfile
from logging import Logger
from pathlib import Path
from typing import Any, Pattern

import aiohttp
from docx2python import docx2python


async def word_to_text(
    logger: Logger,
    file_worker_url: str,
    file_path: Path,
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """
    Extract text and tables from a Word document.
    Returns a list of elements with 'type' (text/table/image) and 'content'.
    """
    elements: list[dict[str, Any]] = []
    doc_result = docx2python(file_path, html=True)
    try:
        img_pattern: Pattern[str] = re.compile(r"----(?:media/)?(image\d+\.\w+)----")
        for section_idx, section in enumerate(doc_result.body):
            for item_idx, item in enumerate(section):
                if is_table_structure(item):
                    table_marker = f"[TABLE_{section_idx}]"
                    table_rows = extract_table_data(item)
                    if item_idx == 0:
                        headers = table_rows
                        formatted_rows = format_table(headers, table_rows)
                        if formatted_rows:
                            elements.append({
                                "type": "table",
                                "content": formatted_rows,
                                "_meta": {"table_marker": table_marker, "item_idx": item_idx},
                            })
                        continue
                if isinstance(item, list):
                    flat_text: str = " ".join(flatten(item)).strip()
                    if flat_text:
                        clean_text = clean_html(flat_text)
                        if clean_text:
                            images_in_text = img_pattern.findall(clean_text)
                            if images_in_text:
                                parts = re.split(img_pattern, clean_text)
                                if parts and parts[0].strip():
                                    elements.append({"type": "text", "content": [parts[0].strip()]})
                                for img_idx, img_name in enumerate(images_in_text):
                                    img_data = doc_result.images.get(img_name)
                                    img_text = ""
                                    if img_data:
                                        try:
                                            form = aiohttp.FormData()
                                            form.add_field("file", img_data, filename=img_name)
                                            async with session.post(
                                                file_worker_url,
                                                data=form,
                                                timeout=aiohttp.ClientTimeout(total=600),
                                            ) as response:
                                                if response.status == 200:
                                                    img_text = await response.text()
                                                    if not img_text or not img_text.strip():
                                                        img_text = f"[Image: {img_name} - empty response]"
                                                else:
                                                    logger.error("Image service status %d for %s", response.status, img_name)
                                        except aiohttp.ClientError as e:
                                            logger.error("Failed to process image %s: %s", img_name, e)
                                            img_text = f"[Image: {img_name} - network error]"
                                        except Exception as e:
                                            logger.error("Unexpected error processing image %s: %s", img_name, e)
                                            img_text = f"[Image: {img_name} - processing error]"
                                    elements.append({"type": "image", "content": [img_text]})
                                    if img_idx + 1 < len(parts) and parts[img_idx + 1].strip():
                                        elements.append({"type": "text", "content": [parts[img_idx + 1].strip()]})
                            else:
                                elements.append({"type": "text", "content": [clean_text]})
    finally:
        doc_result.close()

    text_count = sum(1 for el in elements if el["type"] == "text")
    table_count = sum(1 for el in elements if el["type"] == "table")
    image_count = sum(1 for el in elements if el["type"] == "image")
    logger.info(
        "Extracted %d elements: %d text, %d tables, %d images",
        len(elements), text_count, table_count, image_count,
    )
    return elements


async def convert_doc_to_docx(doc_path: Path, logger: Logger, timeout: int = 60) -> str | None:
    """Convert .doc to .docx using LibreOffice."""
    doc_path = Path(doc_path)
    if not shutil.which("libreoffice"):
        logger.error("LibreOffice not found. Required for .doc conversion.")
        return None

    tmp_dir = tempfile.mkdtemp()
    try:
        proc = await asyncio.create_subprocess_exec(
            "libreoffice", "--headless", "--convert-to", "docx",
            "--outdir", tmp_dir, str(doc_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.error("LibreOffice conversion timed out after %d seconds", timeout)
            return None

        if proc.returncode != 0:
            logger.error("LibreOffice conversion failed: %s", (stderr.decode()[:200] if stderr else "unknown"))
            return None

        expected = Path(tmp_dir) / f"{doc_path.stem}.docx"
        if not expected.exists():
            logger.error("LibreOffice succeeded but output not found: %s", expected)
            return None

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as out:
            out_path = out.name
        shutil.move(str(expected), out_path)
        return out_path

    except Exception as e:
        logger.error("Unexpected conversion error: %s", e, exc_info=True)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Word helpers
# ---------------------------------------------------------------------------

def is_table_structure(item: Any) -> bool:
    if not isinstance(item, list):
        return False
    if len(item) > 0 and isinstance(item[0], list) and len(item) >= 2:
        row_lengths = [len([c for c in row if isinstance(c, (str, list))]) for row in item if isinstance(row, list)]
        if len(row_lengths) >= 2:
            return True
    return False


def extract_table_data(item: list[Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in item:
        if isinstance(row, list):
            cells: list[str] = []
            for cell in row:
                cell_text = " ".join(flatten(cell)).strip() if isinstance(cell, list) else str(cell).strip()
                cell_text = clean_html(cell_text)
                cell_text = re.sub(r"\s+", " ", cell_text.replace("\n", " ").replace("\r", " ")).strip()
                if cell_text:
                    cells.append(cell_text)
            if cells:
                rows.append(cells)
    return rows


def flatten(item: Any) -> list[str]:
    if isinstance(item, str):
        return [item]
    result: list[str] = []
    for sub in item:
        result.extend(flatten(sub))
    return result


def clean_html(text: str) -> str:
    text = re.sub(
        r"Created with an evaluation copy of Aspose\.Words\..*?</span>", "",
        text, flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r'<a href="https://products\.aspose\.com/words/temporary-license/">.*?</a>', "",
        text, flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def format_table(headers: list[list[str]], table_data: list[list[str]]) -> list[str]:
    if not table_data or not headers or not headers[0] or not table_data[0]:
        return []
    if len(headers[0]) == 1 and len(table_data[0]) == 1:
        return [row[0].strip() for row in table_data if row and row[0]]
    formatted = []
    for row in table_data:
        if not row:
            continue
        parts = []
        for col_idx, cell in enumerate(row):
            if col_idx < len(headers[0]) and headers[0][col_idx]:
                header_text = headers[0][col_idx].strip()
                cell_text = cell.strip() if cell else ""
                if cell_text:
                    parts.append(f"{header_text}: {cell_text}")
            elif cell and cell.strip():
                parts.append(cell.strip())
        if parts:
            formatted.append(" | ".join(parts))
    return formatted
