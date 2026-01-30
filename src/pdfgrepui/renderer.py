from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .cache import get_cache_paths, load_meta, save_meta


def _find_generated_page(render_dir: Path, page_number: int) -> Path | None:
    pattern = re.compile(r"page-(\d+)\.png$")
    for generated in render_dir.glob("page-*.png"):
        match = pattern.search(generated.name)
        if not match:
            continue
        if int(match.group(1)) == page_number:
            return generated
    return None


def _render_page_png(pdf_path: Path, page_number: int) -> Path:
    cache_paths = get_cache_paths(pdf_path)
    png_path = cache_paths.render_dir / f"page_{page_number}.png"
    if png_path.exists():
        return png_path
    prefix = cache_paths.render_dir / "page"
    command = [
        "pdftoppm",
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        "-png",
        str(pdf_path),
        str(prefix),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {result.stderr.strip()}")
    generated = _find_generated_page(cache_paths.render_dir, page_number)
    if generated is None:
        raise RuntimeError(f"pdftoppm did not generate page {page_number}")
    generated.rename(png_path)
    meta = load_meta(cache_paths.meta_path)
    meta.setdefault("rendered_pages", []).append(page_number)
    save_meta(cache_paths.meta_path, meta)
    return png_path


def ensure_render_cache(pdf_path: Path, page_count: int) -> None:
    cache_paths = get_cache_paths(pdf_path)
    expected = {cache_paths.render_dir / f"page_{n}.png" for n in range(1, page_count + 1)}
    if all(path.exists() for path in expected):
        return
    prefix = cache_paths.render_dir / "page"
    command = [
        "pdftoppm",
        "-png",
        str(pdf_path),
        str(prefix),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {result.stderr.strip()}")
    rendered_pages = []
    for generated in cache_paths.render_dir.glob("page-*.png"):
        match = re.search(r"page-(\d+)\.png$", generated.name)
        if not match:
            continue
        page_number = int(match.group(1))
        target = cache_paths.render_dir / f"page_{page_number}.png"
        if target.exists():
            generated.unlink()
        else:
            generated.rename(target)
        rendered_pages.append(page_number)
    meta = load_meta(cache_paths.meta_path)
    meta.setdefault("rendered_pages", [])
    meta["rendered_pages"] = sorted(set(meta["rendered_pages"]) | set(rendered_pages))
    save_meta(cache_paths.meta_path, meta)


def render_page(pdf_path: Path, page_number: int) -> Path:
    return _render_page_png(pdf_path, page_number)
