"""Shared dataclasses used by search/index/render orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class SearchMatch:
    """One search hit in a PDF page with lightweight display context."""

    pdf_path: Path
    page_number: int
    match_index: int
    context: str


@dataclass
class PdfDoc:
    """Indexed PDF metadata plus all matches found in that document."""

    path: Path
    page_count: int
    matches: List[SearchMatch] = field(default_factory=list)
