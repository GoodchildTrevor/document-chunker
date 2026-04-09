from typing import Any
from pydantic import BaseModel


class ChunkSchema(BaseModel):
    raw: str
    lemmas: str
    meta: dict[str, Any] = {}


class ChunkResponse(BaseModel):
    file_name: str
    file_format: str
    creation_date: str
    modification_date: str
    chunks: list[ChunkSchema]
