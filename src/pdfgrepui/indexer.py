"""PDF text indexing pipeline: page extraction, matching, and snippet creation."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .cache import (
    get_cache_paths,
    is_cache_valid,
    load_meta,
    load_page_embedding,
    save_meta,
    save_page_embedding,
)
from .models import PdfDoc, SearchConfig, SearchMatch, SearchMode
from .renderer import ensure_render_cache
from .semantic import EmbeddingProvider, cosine_similarity


def _extract_page_text(pdf_path: Path, page_number: int, cache_dir: Path) -> str:
    """Return text for one PDF page, using cached extraction when available."""
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


def _find_matches(text: str, query: str, mode: SearchMode) -> List[Tuple[int, int]]:
    """Find all match spans for literal or regex searches."""
    if mode is SearchMode.REGEX:
        pattern = re.compile(query, re.IGNORECASE)
        return [(match.start(), match.end()) for match in pattern.finditer(text)]
    lowered = text.lower()
    needle = query.lower()
    matches = []
    start = 0
    while True:
        index = lowered.find(needle, start)
        if index == -1:
            break
        matches.append((index, index + len(needle)))
        start = index + len(needle)
    return matches


def _context_snippet(text: str, start: int, end: int, radius: int = 80) -> str:
    """Build a compact single-line context snippet around one match span."""
    left = max(start - radius, 0)
    right = min(end + radius, len(text))
    return text[left:right].strip().replace("\n", " ")


def _semantic_snippet(text: str, query: str, radius: int = 180) -> str:
    """Build a lightweight semantic snippet from the best matching paragraph."""
    paragraphs = [chunk.strip().replace("\n", " ") for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    if not paragraphs:
        return ""
    query_tokens = {token.lower() for token in re.findall(r"\w+", query)}
    best = paragraphs[0]
    best_score = -1
    for paragraph in paragraphs:
        paragraph_tokens = {token.lower() for token in re.findall(r"\w+", paragraph)}
        score = len(query_tokens & paragraph_tokens)
        if score > best_score:
            best = paragraph
            best_score = score
    return best[:radius].strip()


def _semantic_rank_score(raw_score: float, text: str) -> float:
    """Down-rank very short pages so titles and name-only pages score lower."""
    word_count = len(re.findall(r"\w+", text))
    if word_count <= 0:
        return 0.0
    if word_count < 10:
        return raw_score * max(word_count / 10.0, 0.1)
    return raw_score


def _get_page_count(pdf_path: Path) -> int:
    """Load or refresh cached PDF metadata and return page count."""
    cache_paths = get_cache_paths(pdf_path)
    meta = load_meta(cache_paths.meta_path)
    if not is_cache_valid(meta, pdf_path):
        meta = {
            "mtime": pdf_path.stat().st_mtime,
            "page_count": _count_pages(pdf_path),
        }
        save_meta(cache_paths.meta_path, meta)
    return int(meta.get("page_count", 0))


def _index_text_matches(pdf_path: Path, search_config: SearchConfig) -> PdfDoc:
    """Return literal or regex matches for a PDF."""
    page_count = _get_page_count(pdf_path)
    cache_paths = get_cache_paths(pdf_path)
    matches: List[SearchMatch] = []
    match_index = 0
    for page_number in range(1, page_count + 1):
        text = _extract_page_text(pdf_path, page_number, cache_paths.text_dir)
        for start, end in _find_matches(text, search_config.query, search_config.mode):
            match_index += 1
            matches.append(
                SearchMatch(
                    pdf_path=pdf_path,
                    page_number=page_number,
                    match_index=match_index,
                    context=_context_snippet(text, start, end),
                )
            )
    ensure_render_cache(pdf_path, page_count)
    return PdfDoc(path=pdf_path, page_count=page_count, matches=matches)


def _load_semantic_embeddings(
    pdf_path: Path,
    page_texts: Sequence[str],
    provider: EmbeddingProvider,
    provider_name: str,
    model_name: str,
) -> List[Sequence[float]]:
    """Load cached page embeddings and compute any missing ones in one batch."""
    cache_paths = get_cache_paths(pdf_path)
    embeddings: List[Optional[Sequence[float]]] = []
    missing_pages: List[int] = []
    missing_texts: List[str] = []
    for page_number, page_text in enumerate(page_texts, start=1):
        cached = load_page_embedding(cache_paths, pdf_path, page_number, provider_name, model_name)
        embeddings.append(cached)
        if cached is None:
            missing_pages.append(page_number)
            missing_texts.append(page_text)
    if missing_texts:
        generated = provider.embed(missing_texts)
        if len(generated) != len(missing_pages):
            raise ValueError("Embedding provider returned an unexpected number of page embeddings")
        for page_number, embedding in zip(missing_pages, generated):
            vector = [float(value) for value in embedding]
            embeddings[page_number - 1] = vector
            save_page_embedding(cache_paths, pdf_path, page_number, provider_name, model_name, vector)
    return [embedding if embedding is not None else [] for embedding in embeddings]


def _index_semantic_matches(
    pdf_path: Path,
    search_config: SearchConfig,
    provider: EmbeddingProvider,
    query_embedding: Sequence[float],
) -> PdfDoc:
    """Return semantic matches for a PDF ranked by page similarity."""
    page_count = _get_page_count(pdf_path)
    cache_paths = get_cache_paths(pdf_path)
    page_texts = [
        _extract_page_text(pdf_path, page_number, cache_paths.text_dir)
        for page_number in range(1, page_count + 1)
    ]
    page_embeddings = _load_semantic_embeddings(
        pdf_path,
        page_texts,
        provider,
        search_config.embedding.provider,
        search_config.embedding.model,
    )
    scored_pages: List[Tuple[float, int, str]] = []
    for page_number, text, embedding in zip(range(1, page_count + 1), page_texts, page_embeddings):
        if not text.strip():
            continue
        score = _semantic_rank_score(cosine_similarity(query_embedding, embedding), text)
        if score > 0.0:
            scored_pages.append((score, page_number, text))
    scored_pages.sort(reverse=True)
    matches = [
        SearchMatch(
            pdf_path=pdf_path,
            page_number=page_number,
            match_index=index,
            context=_semantic_snippet(text, search_config.query),
            score=score,
        )
        for index, (score, page_number, text) in enumerate(scored_pages, start=1)
    ]
    ensure_render_cache(pdf_path, page_count)
    return PdfDoc(path=pdf_path, page_count=page_count, matches=matches)


def index_pdf(
    pdf_path: Path,
    search_config: SearchConfig,
    embedding_provider: Optional[EmbeddingProvider] = None,
    query_embedding: Optional[Sequence[float]] = None,
) -> PdfDoc:
    """Index one PDF for either literal/regex or semantic search."""
    if search_config.mode is SearchMode.SEMANTIC:
        if embedding_provider is None or query_embedding is None:
            raise ValueError("Semantic search requires an embedding provider and query embedding")
        return _index_semantic_matches(pdf_path, search_config, embedding_provider, query_embedding)
    return _index_text_matches(pdf_path, search_config)
