from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

CACHE_ROOT = Path.home() / ".cache" / "pdfgrepui"


@dataclass
class CachePaths:
    root: Path
    text_dir: Path
    render_dir: Path
    meta_path: Path


def _hash_path(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()


def get_cache_paths(pdf_path: Path) -> CachePaths:
    cache_key = _hash_path(pdf_path)
    root = CACHE_ROOT / cache_key
    text_dir = root / "texts"
    render_dir = root / "renders"
    meta_path = CACHE_ROOT / "meta" / f"{cache_key}.json"
    text_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    return CachePaths(root=root, text_dir=text_dir, render_dir=render_dir, meta_path=meta_path)


def load_meta(meta_path: Path) -> Dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_meta(meta_path: Path, data: Dict[str, Any]) -> None:
    meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_cache_valid(meta: Dict[str, Any], pdf_path: Path) -> bool:
    if not meta:
        return False
    try:
        return meta.get("mtime") == pdf_path.stat().st_mtime
    except FileNotFoundError:
        return False


def invalidate_cache(cache_paths: CachePaths) -> None:
    if cache_paths.meta_path.exists():
        cache_paths.meta_path.unlink()
