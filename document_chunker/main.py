import logging
import tempfile
from pathlib import Path

import aiohttp
from fastapi import FastAPI, File, Form, UploadFile, HTTPException

from document_chunker.config import get_settings, get_nlp_config
from document_chunker.schemas import ChunkResponse, ChunkSchema
from document_chunker.processing.extract import extract_text_metadata
from document_chunker.processing.chunk import chunker

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="document-chunker", version="0.1.0")

SUPPORTED_FORMATS = {".pdf", ".docx", ".doc", ".xlsx"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chunk", response_model=ChunkResponse)
async def chunk_document(
    file: UploadFile = File(...),
    chunk_size: int = Form(default=None),
    overlap: int = Form(default=None),
):
    settings = get_settings()
    nlp = get_nlp_config()

    effective_chunk_size = chunk_size if chunk_size is not None else settings.chunk_size
    effective_overlap = overlap if overlap is not None else settings.overlap

    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported format '{suffix}'. Supported: {sorted(SUPPORTED_FORMATS)}",
        )

    # Save upload to a temp file so processing libs can open it by path
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        async with aiohttp.ClientSession() as session:
            elements, metadata = await extract_text_metadata(
                logger=logger,
                file_worker_url=settings.file_worker_url,
                libreoffice_timeout=settings.libreoffice_timeout,
                file_path=tmp_path,
                file_format=suffix,
                nlp_config=nlp,
                session=session,
            )
    except Exception as e:
        logger.error("Extraction failed: %s", e, exc_info=True)
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    raw_chunks = chunker(
        logger=logger,
        nlp_config=nlp,
        elements=elements,
        max_tokens=effective_chunk_size,
        overlap=effective_overlap,
    )

    return ChunkResponse(
        file_name=file.filename,
        file_format=suffix,
        creation_date=str(metadata.get("creation_date", "")),
        modification_date=str(metadata.get("modification_date", "")),
        chunks=[
            ChunkSchema(
                raw=ch["raw"],
                lemmas=ch["lemmas"],
                meta=ch.get("_meta", {}),
            )
            for ch in raw_chunks
        ],
    )
