# DEV_NOTES

## Run Locally
From the parent directory of this package (`src/`):

```bash
./pdfgrepui/EasyFindVenv/bin/python -m pdfgrepui "search term" .
```

Regex mode:

```bash
./pdfgrepui/EasyFindVenv/bin/python -m pdfgrepui --regex "invoice\\s+total" .
```

Direct file execution from inside `src/pdfgrepui`:

```bash
./EasyFindVenv/bin/python app.py "search term" .
```

## Dependencies and External Tools
This project expects these binaries on PATH:
- `rga` (ripgrep-all)
- `pdfinfo` (poppler)
- `pdftotext` (poppler)
- `pdftoppm` (poppler)

Python/runtime libraries used by UI:
- `textual`
- `textual-image`

## Tests
No automated test suite is present in this directory right now.

Recommended lightweight checks after edits:
1. Import sanity:
```bash
./pdfgrepui/EasyFindVenv/bin/python -c "import pdfgrepui.app, pdfgrepui.indexer, pdfgrepui.search, pdfgrepui.renderer, pdfgrepui.cache, pdfgrepui.models"
```
2. CLI help:
```bash
./pdfgrepui/EasyFindVenv/bin/python -m pdfgrepui -h
```
3. Manual smoke run on a small PDF directory.
   - Verify `/` opens the in-app search popup, `Enter` reruns search, and `Esc` cancels.
   - Verify `space l s q a` opens quick access browser and `Esc` returns to normal search.
   - Verify `space a`, `space r`, and `space d d` work inside quick access browser.
   - Verify `mpl` saves the current page to a chosen quick access list with a note.

## Debugging Tips
- Indexing path:
  - Start at `PdfGrepApp._index_documents()` in [`app.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py).
  - Then follow `search.find_candidate_pdfs()` and `indexer.index_pdf()`.
- Rendering path:
  - Start at `PdfGrepApp._render_current_page()` and follow `renderer.render_page()`.
- Cache behavior:
  - Inspect `~/.cache/pdfgrepui` for `texts/`, `renders/`, and `meta/*.json`.
- Good temporary print points:
  - `find_candidate_pdfs(...)` (candidate set)
  - `index_pdf(...)` page loop and match counts
  - `_update_status(...)` for navigation state

## Where To Add Features
- New CLI option:
  - Add flag in `parse_args()` and thread it through `PdfGrepApp.__init__`.
- New search mode:
  - Extend `_find_matches(...)` in [`indexer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/indexer.py).
  - Keep candidate prefilter in sync if needed (`search.py`).
- New output/result formatting:
  - Edit `_populate_results()` and/or `_update_status()` in [`app.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py).
- New quick access list behavior:
  - Extend quick-access persistence in [`cache.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/cache.py).
  - Update quick-access search helpers in [`indexer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/indexer.py).
  - Update view state, prompts, and chord handling in [`app.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py).
- New preview/render backend:
  - Extend or replace `render_page(...)` in [`renderer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/renderer.py).

## Maintenance Notes
- Cache validity currently keys off source PDF `mtime` only.
  - If cache bugs appear with copied/restored files, consider strengthening cache invalidation logic.
- `ensure_render_cache(...)` can render all pages up-front for each matched document.
  - Useful for smooth paging, but a potential performance hotspot on large PDFs.
