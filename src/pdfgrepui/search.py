from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List


def find_candidate_pdfs(query: str, root: Path) -> List[Path]:
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
