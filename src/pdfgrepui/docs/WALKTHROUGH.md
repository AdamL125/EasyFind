# WALKTHROUGH

## What This Tool Does
`pdfgrepui` is a terminal UI for searching PDF files under a root directory and previewing matching pages.

It uses:
- `rga` to prefilter candidate PDFs containing the query.
- `pdfinfo` and `pdftotext` to index per-page text and find matches.
- `pdftoppm` to render page previews as PNG files.
- Textual + textual-image for interactive UI (results pane + preview pane).

## Architecture (ASCII)
```text
CLI / module entry
  |  (__main__.py -> app.main)
  v
app.parse_args()
  |
  v
PdfGrepApp.run()  [Textual event loop]
  |
  v
PdfGrepApp._start_indexing()
  |
  v
PdfGrepApp._index_documents()
  |
  +--> search.find_candidate_pdfs(query, root)
  |       \-> subprocess: rga --files-with-matches
  |
  +--> indexer.index_pdf(pdf, query, regex) [for each candidate]
          |
          +--> cache.get_cache_paths/load_meta/is_cache_valid/save_meta
          +--> subprocess: pdfinfo (page count)
          +--> subprocess: pdftotext (per-page text)
          +--> text matching + snippets
          +--> renderer.ensure_render_cache(pdf, page_count)
                  \-> subprocess: pdftoppm (all pages)

UI navigation
  |
  +--> `/` opens search overlay input (top-center)
  |       \-> Enter submits new query -> reindex -> overlay hides
  |       \-> Esc cancels -> overlay hides
  |
  +--> app._jump_to_match / _next_page / _previous_page
          |
          +--> renderer.render_page(pdf, page)
                  \-> pdftoppm for single-page fallback if needed
          |
          +--> PreviewPane.show_image(...) / show_message(...)
```

## Entry Points
- `python -m pdfgrepui`
  - [`__main__.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/__main__.py): calls `app.main()`.
- Direct module run
  - [`app.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py): guarded `if __name__ == "__main__": main()`.

## Execution Flow (CLI -> Search -> Output)
1. `main()` in [`app.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py) calls `parse_args()`.
2. `PdfGrepApp(query, root, regex)` is created and `run()` starts Textual.
3. `on_mount()` sets focus/status and awaits `_start_indexing()`.
4. `_start_indexing()` runs `_index_documents()` in a background thread via `asyncio.to_thread(...)`.
5. `_index_documents()`:
   - Calls `find_candidate_pdfs(query, root)` from [`search.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/search.py).
   - Iterates candidates, calling `index_pdf(pdf_path, query, regex)` from [`indexer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/indexer.py).
   - Collects `PdfDoc` objects and flattens all `SearchMatch` values.
6. `_populate_results()` builds the left-pane list labels (`filename`, page, snippet).
7. If matches exist, `_jump_to_match(0)` selects first match and `_render_current_page()` renders preview.
8. `_render_current_page()` calls `render_page(path, page)` from [`renderer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/renderer.py) and then updates preview image.
9. Key handlers in `on_key()` drive navigation:
   - `/` opens a centered search overlay input.
   - Left pane: `j/k` move result cursor, `enter` jumps to selection.
   - Right pane: `j/k` page navigation, `n/N` next/previous match.
10. Submitting the overlay input (`on_input_submitted`) updates `self.query`, clears current result state, reruns indexing, then hides overlay.
11. `_update_status()` writes current document/page/match/focus info to status line.

## Core Data Structures
- [`models.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/models.py)
  - `SearchMatch`: one hit (`pdf_path`, `page_number`, `match_index`, `context`).
  - `PdfDoc`: one indexed PDF (`path`, `page_count`, `matches`).
- [`cache.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/cache.py)
  - `CachePaths`: cache locations (`texts`, `renders`, metadata JSON).

## Dependency / Import Map
```text
__main__ -> app

app -> indexer, models, renderer, search, textual*, textual_image*
indexer -> cache, models, renderer, re, subprocess
renderer -> cache, re, subprocess
search -> subprocess
cache -> hashlib, json, pathlib
models -> dataclasses, pathlib
```

## Where State/Config Lives
- Runtime UI/session state: [`PdfGrepApp` in app.py](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py)
  - Query, root path, regex flag.
  - Current focus, selected match/page/doc indices.
  - Loaded documents and match list.
- Persistent cache config: [`CACHE_ROOT` in cache.py](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/cache.py)
  - Default path: `~/.cache/pdfgrepui`.

## Where I/O Happens
- Filesystem read/write:
  - Cache directory creation and metadata/text/png writes in [`cache.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/cache.py), [`indexer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/indexer.py), [`renderer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/renderer.py).
- Subprocess calls to external binaries:
  - `rga` in [`search.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/search.py).
  - `pdfinfo`, `pdftotext` in [`indexer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/indexer.py).
  - `pdftoppm` in [`renderer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/renderer.py).

## Where Searching Happens
- Candidate-level filtering: `find_candidate_pdfs(...)` in [`search.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/search.py).
- Page-level matching and snippet generation: `_find_matches(...)` and `_context_snippet(...)` in [`indexer.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/indexer.py).

## Where Output Formatting Happens
- Result row text in `_populate_results()` in [`app.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py).
- Status line formatting in `_update_status()` in [`app.py`](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py).
- Preview pane mode selection (`show_message` vs `show_image`) in [`PreviewPane` in app.py](/home/adam/Documents/Projects/EasyFind/src/pdfgrepui/app.py).

## Error Handling Strategy
- External tool failures are raised as `RuntimeError` in `search/indexer/renderer` helpers.
- UI layer catches exceptions during indexing and rendering:
  - `_start_indexing()` shows `Indexing failed: ...`.
  - `_render_current_page()` shows `Render failed: ...`.
- Invalid/missing cache metadata is handled defensively:
  - `load_meta()` returns `{}` when JSON is missing/corrupt.

## Ambiguities / Notes
- `rga --files-with-matches` determines candidate PDFs quickly, but matching semantics may differ from in-app matching when `--regex` is enabled because candidate discovery always uses the raw query passed to `rga`.
  - Most likely interpretation: this is an intentional fast prefilter; exact match extraction is authoritative in `indexer.py`.
