"""Candidate discovery: locate PDFs likely to contain the query string."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List


def find_candidate_pdfs(query: str, root: Path) -> List[Path]:
    """Return sorted candidate PDF paths by delegating to ripgrep-all (`rga`).

    Side effects:
        Executes external `rga`.
    """
    # --- Search pipeline ---
    # NOTE: this prefilter narrows expensive per-page indexing to PDFs that already match.
    command = [
        "rga",
        "--files-with-matches",
        "--glob",
        "*.pdf",
        query,
        str(root),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"rga failed: {result.stderr.strip()}")
    paths = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    return sorted(paths)


def find_all_pdfs(root: Path) -> List[Path]:
    """Return all PDFs under `root` for semantic-search candidate discovery."""
    if root.is_file():
        return [root] if root.suffix.lower() == ".pdf" else []
    return sorted(path for path in root.rglob("*.pdf") if path.is_file())
