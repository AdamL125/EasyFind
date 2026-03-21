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
- `s/`: open semantic search popup
- `space l s q a`: open quick access lists
- `Esc`: close popup, leave quick access mode, or exit fullscreen preview

Left pane (results list):

- `j`/`k`: move selection down/up
- `Enter`: jump preview to selected match

Right pane (PDF preview):

- `j`: next page
- `k`: previous page
- `n`: next match
- `N`: previous match
- `mpl`: save the current page to a quick access list

Search popup:

- `Enter`: run a new search with the input text, then close popup
- `Esc`: close popup without changing the current search

Quick access browser:

- `Enter`: open selected quick access list
- `space a`: create a new quick access list
- `space r`: rename the selected quick access list
- `space d d`: delete the selected quick access list after confirmation

Opened quick access list:

- `/` and `s/`: search saved entries by note plus source page text
- `j`/`k` in right pane: move through saved entries in list order
- `n`/`N`: move through current quick access search results

## Notes

- Only PDFs that `rga` reports as having matches are indexed per-page.
- Cached assets are stored under `~/.cache/pdfgrepui/` and invalidated when the PDF changes.
- In-app search updates are executed against the same root path and regex mode selected at startup.
- Quick access lists are persisted in `~/.cache/pdfgrepui/quick_access_lists.json`.
