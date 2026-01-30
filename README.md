# pdfgrepui — `rga`-powered PDF search with a two-pane TUI (WezTerm-friendly)

A terminal UI that lets you:
- search PDFs with `ripgrep-all (rga)`
- browse matches in a **left pane**
- preview the **actual PDF page (images + figures)** in a **right pane**
- navigate pages + matches with **vim keys**
- wrap seamlessly across PDFs when you hit the end of matches/pages

This README is the build plan + project spec for **Approach A (proper TUI app)**, targeting **WezTerm** as the primary terminal.

---

## Goals (your ideal workflow)

### Layout
- **Left pane:** list of search matches (grouped by PDF)
- **Right pane:** **rendered PDF page preview** (real page image)

### Navigation & focus
- `h` / `l` switch focus between panes
  - `h` = focus left (match list)
  - `l` = focus right (PDF preview)

### Within the right pane (PDF preview)
- `j` = next page
- `k` = previous page
- `n` = next match
- `N` = previous match

### Auto-advance behavior
- `n` on the **last match** in current PDF → jump to **first match of next PDF**
- `N` on the **first match** in current PDF → jump to **last match of previous PDF**
- `j` on the **last page** in current PDF → move to next PDF (sensible default: page 1 or nearest match page)
- `k` on the **first page** in current PDF → move to previous PDF (sensible default: last page or nearest match page)

---

## Tech stack

### Search
- `ripgrep-all (rga)` for finding matches across PDFs

### PDF text extraction (page-aware)
- `pdftotext` (Poppler) to extract text per page for accurate page mapping

### PDF rendering for preview
- `pdftoppm` (Poppler) to render a specific page → PNG

### TUI framework
- **Python + Textual** for split panes, focus management, and keybindings

### Terminal image display (WezTerm)
- Use **WezTerm inline image protocol** via:
  - `wezterm imgcat path/to/page.png`
- Fallback: `chafa` (ANSI/Unicode image rendering) if needed

---

## Dependencies

### System packages
```bash
sudo apt update
sudo apt install -y ripgrep-all poppler-utils
