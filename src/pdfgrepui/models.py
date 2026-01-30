from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class SearchMatch:
    pdf_path: Path
    page_number: int
    match_index: int
    context: str


@dataclass
class PdfDoc:
    path: Path
    page_count: int
    matches: List[SearchMatch] = field(default_factory=list)
