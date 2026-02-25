# pdfgrepui

Terminal UI for searching text-based PDFs and previewing rendered pages in a two-pane interface.

## Requirements

System packages (Ubuntu/Mint):

- `ripgrep-all` (`rga`)
- `poppler-utils` (`pdftotext`, `pdftoppm`)
- `wezterm` (recommended terminal with inline image support)

Python packages:

- `textual`
- `textual-image`
- `rich`

## Install

```bash
pip install -e .
```

## Run

```bash
pdfgrepui "some term" .
# or
python -m pdfgrepui "some term" .
```

## Keybindings

Global:

- `q`: quit
- `h`: focus left pane
- `l`: focus right pane
- `/`: open in-app search popup (top-center)

Left pane (results list):

- `j`/`k`: move selection down/up
- `Enter`: jump preview to selected match

Right pane (PDF preview):

- `j`: next page
- `k`: previous page
- `n`: next match
- `N`: previous match

Search popup:

- `Enter`: run a new search with the input text, then close popup
- `Esc`: close popup without changing the current search

## Notes

- Only PDFs that `rga` reports as having matches are indexed per-page.
- Cached assets are stored under `~/.cache/pdfgrepui/` and invalidated when the PDF changes.
- In-app search updates are executed against the same root path and regex mode selected at startup.
