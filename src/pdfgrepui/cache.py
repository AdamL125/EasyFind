"""Cache keying and metadata helpers shared by indexing and rendering modules."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

CACHE_ROOT = Path.home() / ".cache" / "pdfgrepui"
SEMANTIC_SETTINGS_PATH = CACHE_ROOT / "semantic_settings.json"


@dataclass
class CachePaths:
    """Filesystem locations for one PDF's cached artifacts."""

    root: Path
    text_dir: Path
    render_dir: Path
    embedding_dir: Path
    meta_path: Path


def _hash_path(path: Path) -> str:
    """Build stable cache key from resolved absolute PDF path."""
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()


def get_cache_paths(pdf_path: Path) -> CachePaths:
    """Return and create cache directories/metadata path for a PDF.

    Side effects:
        Creates cache directories if missing.
    """
    cache_key = _hash_path(pdf_path)
    root = CACHE_ROOT / cache_key
    text_dir = root / "texts"
    render_dir = root / "renders"
    embedding_dir = root / "embeddings"
    meta_path = CACHE_ROOT / "meta" / f"{cache_key}.json"
    text_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    embedding_dir.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    return CachePaths(
        root=root,
        text_dir=text_dir,
        render_dir=render_dir,
        embedding_dir=embedding_dir,
        meta_path=meta_path,
    )


def load_meta(meta_path: Path) -> Dict[str, Any]:
    """Load cache metadata JSON; return empty dict when missing/invalid."""
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_meta(meta_path: Path, data: Dict[str, Any]) -> None:
    """Write cache metadata JSON file."""
    meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_cache_valid(meta: Dict[str, Any], pdf_path: Path) -> bool:
    """Return True when cache metadata corresponds to current PDF mtime."""
    if not meta:
        return False
    try:
        return meta.get("mtime") == pdf_path.stat().st_mtime
    except FileNotFoundError:
        return False


def invalidate_cache(cache_paths: CachePaths) -> None:
    """Delete metadata file for a cached PDF, forcing metadata rebuild next run."""
    if cache_paths.meta_path.exists():
        cache_paths.meta_path.unlink()


def load_semantic_settings() -> Dict[str, str]:
    """Load persisted semantic-search provider/model settings."""
    if not SEMANTIC_SETTINGS_PATH.exists():
        return {}
    try:
        payload = json.loads(SEMANTIC_SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    provider = payload.get("provider")
    model = payload.get("model")
    if not isinstance(provider, str) or not isinstance(model, str):
        return {}
    return {"provider": provider, "model": model}


def save_semantic_settings(provider: str, model: str) -> None:
    """Persist semantic-search provider/model settings for future runs."""
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"provider": provider, "model": model}
    SEMANTIC_SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _embedding_cache_key(provider: str, model: str) -> str:
    payload = f"{provider}:{model}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def get_embedding_cache_path(
    cache_paths: CachePaths,
    page_number: int,
    provider: str,
    model: str,
) -> Path:
    """Return cache path for one page embedding and embedding config."""
    key = _embedding_cache_key(provider, model)
    return cache_paths.embedding_dir / f"{key}_page_{page_number}.json"


def load_page_embedding(
    cache_paths: CachePaths,
    pdf_path: Path,
    page_number: int,
    provider: str,
    model: str,
) -> Optional[List[float]]:
    """Load one cached page embedding when it matches the current PDF/config."""
    path = get_embedding_cache_path(cache_paths, page_number, provider, model)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    try:
        if payload.get("mtime") != pdf_path.stat().st_mtime:
            return None
    except FileNotFoundError:
        return None
    if payload.get("provider") != provider or payload.get("model") != model:
        return None
    embedding = payload.get("embedding")
    if not isinstance(embedding, list):
        return None
    return [float(value) for value in embedding]


def save_page_embedding(
    cache_paths: CachePaths,
    pdf_path: Path,
    page_number: int,
    provider: str,
    model: str,
    embedding: List[float],
) -> None:
    """Persist one page embedding with the metadata needed for invalidation."""
    path = get_embedding_cache_path(cache_paths, page_number, provider, model)
    payload = {
        "mtime": pdf_path.stat().st_mtime,
        "provider": provider,
        "model": model,
        "page_number": page_number,
        "embedding": embedding,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
