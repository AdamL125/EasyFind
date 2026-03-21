"""Shared dataclasses used by search/index/render orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional


class SearchMode(str, Enum):
    """Supported search modes for the CLI and TUI."""

    LITERAL = "literal"
    REGEX = "regex"
    SEMANTIC = "semantic"


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding provider settings used for semantic search."""

    provider: str = "ollama"
    model: str = ""


@dataclass(frozen=True)
class SearchConfig:
    """Normalized search configuration shared by search/index/app layers."""

    query: str
    mode: SearchMode = SearchMode.LITERAL
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)


@dataclass
class SearchMatch:
    """One search hit in a PDF page with lightweight display context."""

    pdf_path: Path
    page_number: int
    match_index: int
    context: str
    score: Optional[float] = None


@dataclass
class PdfDoc:
    """Indexed PDF metadata plus all matches found in that document."""

    path: Path
    page_count: int
    matches: List[SearchMatch] = field(default_factory=list)
