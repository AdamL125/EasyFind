"""PDF text indexing pipeline: page extraction, matching, and snippet creation."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Tuple

from .cache import get_cache_paths, is_cache_valid, load_meta, save_meta
from .models import PdfDoc, SearchMatch
from .renderer import ensure_render_cache


def _extract_page_text(pdf_path: Path, page_number: int, cache_dir: Path) -> str:
    """Return text for one PDF page, using cached extraction when available.

    Side effects:
        Reads/writes per-page text cache files.
        Calls external `pdftotext` when cache is missing.
    """
    cache_path = cache_dir / f"page_{page_number}.txt"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="ignore")
    command = [
        "pdftotext",
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        str(pdf_path),
        "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr.strip()}")
    cache_path.write_text(result.stdout, encoding="utf-8")
    return result.stdout


def _count_pages(pdf_path: Path) -> int:
    """Return PDF page count by calling `pdfinfo`."""
    command = ["pdfinfo", str(pdf_path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pdfinfo failed: {result.stderr.strip()}")
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError("Unable to determine page count")


def _find_matches(text: str, query: str, regex: bool) -> List[Tuple[int, int]]:
    """Find all match spans in `text` using regex or case-insensitive literal search."""
    if regex:
        pattern = re.compile(query, re.IGNORECASE)
        return [(m.start(), m.end()) for m in pattern.finditer(text)]
    lowered = text.lower()
    needle = query.lower()
    matches = []
    start = 0
    while True:
        idx = lowered.find(needle, start)
        if idx == -1:
            break
        matches.append((idx, idx + len(needle)))
        start = idx + len(needle)
    return matches


def _context_snippet(text: str, start: int, end: int, radius: int = 80) -> str:
    """Build a compact single-line context snippet around one match span."""
    left = max(start - radius, 0)
    right = min(end + radius, len(text))
    snippet = text[left:right].strip().replace("\n", " ")
    return snippet


def index_pdf(pdf_path: Path, query: str, regex: bool) -> PdfDoc:
    """Index one PDF and return all matches plus metadata.

    Args:
        pdf_path: PDF file to index.
        query: Search query string.
        regex: Whether query should be treated as regex.

    Returns:
        PdfDoc with page count and ordered matches.

    Side effects:
        Reads/writes cache metadata and per-page text cache.
        Calls external `pdfinfo`, `pdftotext`, and rendering cache warmup.
    """
    # --- Search pipeline ---
    # NOTE: cache validity is based on source PDF mtime to avoid re-indexing unchanged files.
    cache_paths = get_cache_paths(pdf_path)
    meta = load_meta(cache_paths.meta_path)
    if not is_cache_valid(meta, pdf_path):
        meta = {
            "mtime": pdf_path.stat().st_mtime,
            "page_count": _count_pages(pdf_path),
        }
        save_meta(cache_paths.meta_path, meta)
    page_count = int(meta.get("page_count", 0))
    matches: List[SearchMatch] = []
    match_index = 0
    for page in range(1, page_count + 1):
        text = _extract_page_text(pdf_path, page, cache_paths.text_dir)
        for start, end in _find_matches(text, query, regex):
            match_index += 1
            snippet = _context_snippet(text, start, end)
            matches.append(
                SearchMatch(
                    pdf_path=pdf_path,
                    page_number=page,
                    match_index=match_index,
                    context=snippet,
                )
            )
    ensure_render_cache(pdf_path, page_count)
    return PdfDoc(path=pdf_path, page_count=page_count, matches=matches)
