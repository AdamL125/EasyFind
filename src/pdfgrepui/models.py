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


class ViewState(str, Enum):
    """High-level UI state for normal search and quick-access modes."""

    NORMAL = "normal"
    QUICK_ACCESS_BROWSER = "quick_access_browser"
    QUICK_ACCESS_DOCUMENT = "quick_access_document"


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
    """One search hit in a PDF page or quick-access entry."""

    pdf_path: Path
    page_number: int
    match_index: int
    context: str
    score: Optional[float] = None
    source_id: Optional[str] = None


@dataclass
class PdfDoc:
    """Indexed PDF metadata plus all matches found in that document."""

    path: Path
    page_count: int
    matches: List[SearchMatch] = field(default_factory=list)


@dataclass
class QuickAccessEntry:
    """One saved PDF page plus a searchable user note."""

    id: str
    pdf_path: Path
    page_number: int
    note: str
    page_text: str = ""

    def combined_search_text(self) -> str:
        """Return text searched when filtering a quick-access entry."""
        return f"{self.note}\n\n{self.page_text}".strip()


@dataclass
class QuickAccessList:
    """Persistent named collection of saved PDF pages."""

    id: str
    name: str
    entries: List[QuickAccessEntry] = field(default_factory=list)
