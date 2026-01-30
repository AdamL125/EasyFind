from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
import re
from typing import Optional

from .cache import get_cache_paths, load_meta, save_meta


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
    generated = cache_paths.render_dir / f"page-{page_number}.png"
    if generated.exists():
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
    pattern = re.compile(r"page-(\d+)\\.png$")
    rendered_pages = []
    for generated in cache_paths.render_dir.glob("page-*.png"):
        match = pattern.search(generated.name)
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


def _wezterm_available() -> bool:
    return shutil.which("wezterm") is not None


def _chafa_available() -> bool:
    return shutil.which("chafa") is not None


def _render_with_wezterm(png_path: Path) -> str:
    command = ["wezterm", "imgcat", str(png_path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "wezterm imgcat failed")
    return result.stdout


def _render_with_chafa(png_path: Path) -> str:
    command = ["chafa", str(png_path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "chafa failed")
    return result.stdout


def render_page(pdf_path: Path, page_number: int) -> str:
    png_path = _render_page_png(pdf_path, page_number)
    preferred = os.environ.get("PDFGREPUI_RENDERER", "").lower()
    if preferred == "wezterm":
        return _render_with_wezterm(png_path)
    if preferred == "chafa":
        return _render_with_chafa(png_path)
    if _wezterm_available():
        try:
            return _render_with_wezterm(png_path)
        except RuntimeError:
            pass
    if _chafa_available():
        return _render_with_chafa(png_path)
    raise RuntimeError("No renderer available (wezterm or chafa)")
