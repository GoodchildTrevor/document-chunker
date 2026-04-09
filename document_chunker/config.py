import os
from dataclasses import dataclass, field
from functools import lru_cache

import pymorphy3
import tiktoken
from stop_words import get_stop_words
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    file_worker_url: str = "http://localhost:9000/parse"
    libreoffice_timeout: int = 60
    chunk_size: int = 512
    overlap: int = 1

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@dataclass
class NLPConfig:
    """NLP tools: morphological analyser, tokenizer, stopwords."""
    stopwords: set = field(default_factory=lambda: set(get_stop_words("ru")))
    morph: pymorphy3.MorphAnalyzer = field(default_factory=pymorphy3.MorphAnalyzer)
    tokenizer: tiktoken.Encoding = field(
        default_factory=lambda: tiktoken.get_encoding("cl100k_base")
    )


@lru_cache
def get_nlp_config() -> NLPConfig:
    """Singleton NLPConfig — heavy models loaded once."""
    return NLPConfig()
