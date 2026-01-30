# pdfgrepui

Terminal UI for searching text-based PDFs and previewing rendered pages in a two-pane interface.

## Requirements

System packages (Ubuntu/Mint):

- `ripgrep-all` (`rga`)
- `poppler-utils` (`pdftotext`, `pdftoppm`)
- `wezterm` (for inline image rendering)
- `chafa` (optional fallback renderer)

Python packages:

- `textual`
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

Left pane (results list):

- `j`/`k`: move selection down/up
- `Enter`: jump preview to selected match

Right pane (PDF preview):

- `j`: next page
- `k`: previous page
- `n`: next match
- `N`: previous match

## Renderer selection

By default, the app tries `wezterm imgcat` for inline images. If it fails or `wezterm` is unavailable, it falls back to `chafa`.

You can force a mode with:

```bash
PDFGREPUI_RENDERER=wezterm pdfgrepui "term" .
PDFGREPUI_RENDERER=chafa pdfgrepui "term" .
```

## Notes

- Only PDFs that `rga` reports as having matches are indexed per-page.
- Cached assets are stored under `~/.cache/pdfgrepui/` and invalidated when the PDF changes.
