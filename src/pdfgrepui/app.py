"""Textual CLI application wiring for search, navigation, and preview rendering."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import List, Optional, Tuple

import textual_image.renderable  # must be imported before Textual app starts
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static
from textual_image.widget import Image

from .indexer import index_pdf
from .models import EmbeddingConfig, PdfDoc, SearchConfig, SearchMatch, SearchMode
from .renderer import render_page
from .search import find_all_pdfs, find_candidate_pdfs
from .semantic import build_embedding_provider


class PreviewPane(Container):
    """Right-side preview container that toggles between status text and image output."""

    def compose(self) -> ComposeResult:
        """Build preview widgets once and reuse them across updates."""
        yield Label("", id="preview-message")
        yield Image(id="preview-image")

    def show_message(self, message: str) -> None:
        """Show a plain-text status/error message in the preview pane."""
        label = self.query_one("#preview-message", Label)
        img = self.query_one("#preview-image", Image)
        label.update(message)
        label.display = True
        img.display = False

    def show_image(self, image_path: Path) -> None:
        """Display the rendered image for a selected PDF page."""
        label = self.query_one("#preview-message", Label)
        img = self.query_one("#preview-image", Image)
        label.update("")
        label.display = False
        img.image = str(image_path)
        img.display = True


class PdfGrepApp(App):
    """Textual app that coordinates indexing, result listing, and page preview."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #main {
        height: 1fr;
    }

    #results {
        width: 40%;
        border: tall $primary;
    }

    #preview {
        width: 60%;
        border: tall $primary;
    }

    #preview-image {
        width: 100%;
        height: 100%;
    }

    #preview-message {
        width: 100%;
        height: 100%;
        content-align: center middle;
    }

    Screen.fullscreen-preview #results {
        display: none;
    }

    Screen.fullscreen-preview Header {
        display: none;
    }

    Screen.fullscreen-preview Footer {
        display: none;
    }

    Screen.fullscreen-preview #status {
        display: none;
    }

    Screen.fullscreen-preview #preview {
        width: 100%;
        height: 100%;
    }

    #status {
        height: auto;
        border: tall $secondary;
    }

    #search-overlay {
        layer: overlay;
        width: 100%;
        height: 100%;
        display: none;
        align: center top;
        padding: 3 0 0 0;
        background: $surface 40%;
    }

    #search-modal {
        width: 70%;
        max-width: 84;
        height: auto;
        border: tall $accent;
        background: $surface;
        padding: 0 1;
    }

    #search-input {
        width: 100%;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "focus_left", "Focus Left"),
        ("l", "focus_right", "Focus Right"),
        ("/", "open_search", "Search"),
    ]

    def __init__(
        self,
        query: str,
        root: Path,
        regex: bool,
        initial_mode: SearchMode = SearchMode.LITERAL,
        embedding_config: Optional[EmbeddingConfig] = None,
    ) -> None:
        """Initialize app state for one query session."""
        super().__init__()
        self.query = query
        self.root = root
        self.regex = regex
        self.default_search_mode = SearchMode.REGEX if regex else SearchMode.LITERAL
        self.active_search_mode = initial_mode
        self.overlay_search_mode = initial_mode
        self.embedding_config = embedding_config or EmbeddingConfig()
        self.focus_pane = "left"
        self.documents: List[PdfDoc] = []
        self.matches: List[SearchMatch] = []
        self.current_match_index: Optional[int] = None
        self.current_pdf_index: Optional[int] = None
        self.current_page: Optional[int] = None
        self.results_list = ListView(id="results")
        self.preview = PreviewPane(id="preview")
        self.status = Static(id="status")
        self.search_overlay = Container(
            Container(Input(placeholder="Enter new search and press Enter", id="search-input"), id="search-modal"),
            id="search-overlay",
        )
        self.search_open = False
        self.preview_fullscreen = False
        self._focus_before_fullscreen = "left"
        self._semantic_chord_pending = False

    def compose(self) -> ComposeResult:
        """Compose the two-pane layout plus status/footer widgets."""
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            yield self.results_list
            yield self.preview
        yield self.status
        yield self.search_overlay
        yield Footer()

    async def on_mount(self) -> None:
        """Kick off indexing after the UI is mounted."""
        self._focus_left()
        self.status.update("Indexing...")
        await self._start_indexing()

    def _current_search_config(self, mode: Optional[SearchMode] = None) -> SearchConfig:
        """Return normalized search configuration for the current app state."""
        return SearchConfig(
            query=self.query,
            mode=mode or self.active_search_mode,
            embedding=self.embedding_config,
        )

    async def _start_indexing(self) -> None:
        """Run indexing in a worker thread and initialize initial selection."""
        try:
            documents, matches = await asyncio.to_thread(self._index_documents)
        except Exception as exc:
            self.status.update(f"Indexing failed: {exc}")
            return
        self.documents = documents
        self.matches = matches
        self._populate_results()
        if self.matches:
            self.results_list.index = 0
            await self._jump_to_match(0)
        else:
            self.preview.show_message("No matches found.")
        self._update_status()

    def _index_documents(self) -> Tuple[List[PdfDoc], List[SearchMatch]]:
        """Run candidate discovery and index matching PDFs for the active mode."""
        search_config = self._current_search_config()
        if search_config.mode is SearchMode.SEMANTIC:
            if not self.embedding_config.model:
                raise ValueError("Semantic search requires --set-embedding-model")
            provider = build_embedding_provider(search_config.embedding)
            query_embeddings = provider.embed([search_config.query])
            if not query_embeddings:
                raise ValueError("Embedding provider returned no query embedding")
            query_embedding = query_embeddings[0]
            candidates = find_all_pdfs(self.root)
        else:
            provider = None
            query_embedding = None
            candidates = find_candidate_pdfs(search_config.query, self.root)
        documents: List[PdfDoc] = []
        matches: List[SearchMatch] = []
        for pdf_path in candidates:
            doc = index_pdf(
                pdf_path,
                search_config,
                embedding_provider=provider,
                query_embedding=query_embedding,
            )
            if doc.matches:
                documents.append(doc)
        if search_config.mode is SearchMode.SEMANTIC:
            documents.sort(key=lambda doc: doc.matches[0].score or 0.0, reverse=True)
        for doc in documents:
            matches.extend(doc.matches)
        return documents, matches

    def _populate_results(self) -> None:
        """Populate the left results list from documents with matches."""
        self.results_list.clear()
        if not self.documents:
            self.results_list.append(ListItem(Label("No matches")))
            return
        for doc in self.documents:
            label_text = doc.path.name
            if self.active_search_mode is SearchMode.SEMANTIC and doc.matches and doc.matches[0].score is not None:
                label_text = f"{label_text} ({doc.matches[0].score:.3f})"
            self.results_list.append(ListItem(Label(label_text)))

    def _focus_left(self) -> None:
        """Move keyboard focus to results list pane."""
        self.focus_pane = "left"
        self.results_list.focus()
        self._update_status()

    def _focus_right(self) -> None:
        """Move keyboard focus to preview pane."""
        self.focus_pane = "right"
        self.preview.focus()
        self._update_status()

    def action_focus_left(self) -> None:
        """Textual action handler for binding: focus left pane."""
        self._focus_left()

    def action_focus_right(self) -> None:
        """Textual action handler for binding: focus right pane."""
        self._focus_right()

    def _set_preview_fullscreen(self, enabled: bool) -> None:
        """Enter or exit right-pane fullscreen layout mode."""
        if enabled == self.preview_fullscreen:
            return
        self.preview_fullscreen = enabled
        if enabled:
            self._focus_before_fullscreen = self.focus_pane
            self.screen.add_class("fullscreen-preview")
            self._focus_right()
            return
        self.screen.remove_class("fullscreen-preview")
        if self._focus_before_fullscreen == "left":
            self._focus_left()
        else:
            self._focus_right()

    async def on_key(self, event) -> None:  # type: ignore[override]
        """Handle pane-specific key bindings, mode toggles, and navigation."""
        if self.preview_fullscreen and event.key == "escape":
            self._set_preview_fullscreen(False)
            event.stop()
            return
        if self.search_open:
            if event.key == "escape":
                self._hide_search_overlay()
                event.stop()
            return
        if self._semantic_chord_pending:
            if event.key in ("/", "slash"):
                self._semantic_chord_pending = False
                self._open_search(SearchMode.SEMANTIC)
                event.stop()
                return
            self._semantic_chord_pending = False
        if event.key == "s":
            self._semantic_chord_pending = True
            self.status.update("Semantic chord pending: press /")
            event.stop()
            return
        if event.key == "f":
            self._set_preview_fullscreen(not self.preview_fullscreen)
            event.stop()
            return
        if event.key in ("/", "slash"):
            self.action_open_search()
            event.stop()
            return
        if event.key == "enter" and self.focus_pane == "left":
            await self._jump_to_selected()
            event.stop()
            return
        if self.focus_pane == "left":
            if event.key == "j":
                self.results_list.action_cursor_down()
                event.stop()
                return
            if event.key == "k":
                self.results_list.action_cursor_up()
                event.stop()
                return
        if self.focus_pane == "right":
            if event.key == "j":
                await self._next_page()
                event.stop()
                return
            if event.key == "k":
                await self._previous_page()
                event.stop()
                return
            if event.key == "n":
                await self._next_match()
                event.stop()
                return
            if event.key in ("N", "shift+n"):
                await self._previous_match()
                event.stop()
                return

    def _open_search(self, mode: SearchMode) -> None:
        """Show centered search overlay and focus input for the requested mode."""
        self.search_open = True
        self.overlay_search_mode = mode
        self.search_overlay.display = True
        search_input = self.query_one("#search-input", Input)
        if mode is SearchMode.SEMANTIC:
            search_input.placeholder = "Enter semantic prompt and press Enter"
        elif mode is SearchMode.REGEX:
            search_input.placeholder = "Enter regex search and press Enter"
        else:
            search_input.placeholder = "Enter new search and press Enter"
        search_input.value = self.query
        search_input.focus()
        search_input.cursor_position = len(search_input.value)

    def action_open_search(self) -> None:
        """Show the default non-semantic search overlay."""
        self._open_search(self.default_search_mode)

    def _hide_search_overlay(self) -> None:
        """Hide search overlay and return focus to the active pane."""
        self.search_open = False
        self.search_overlay.display = False
        if self.focus_pane == "right":
            self.preview.focus()
        else:
            self.results_list.focus()
        self._update_status()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Submit in overlay input reruns the search and closes overlay."""
        if event.input.id != "search-input":
            return
        new_query = event.value.strip()
        selected_mode = self.overlay_search_mode
        self._hide_search_overlay()
        if not new_query:
            self.status.update("Search unchanged: query is empty.")
            return
        self.query = new_query
        self.active_search_mode = selected_mode
        await self._start_search()

    async def _start_search(self) -> None:
        """Clear current state and perform a new search with current query."""
        mode_name = self.active_search_mode.value
        self.status.update(f"Searching ({mode_name}) for: {self.query}")
        self.results_list.clear()
        self.preview.show_message("Searching...")
        self.documents = []
        self.matches = []
        self.current_match_index = None
        self.current_pdf_index = None
        self.current_page = None
        await self._start_indexing()

    async def _jump_to_selected(self) -> None:
        """Jump preview context to the currently selected row in results."""
        if not self.documents:
            return
        index = self.results_list.index
        if index is None:
            return
        await self._jump_to_document(index)

    async def _jump_to_document(self, doc_index: int) -> None:
        """Select a document row and jump to its first match."""
        if doc_index < 0 or doc_index >= len(self.documents):
            return
        doc = self.documents[doc_index]
        if not doc.matches:
            return
        first_match = doc.matches[0]
        await self._jump_to_match(self.matches.index(first_match))

    async def _jump_to_match(self, match_index: int) -> None:
        """Select match by flat index and render its corresponding page."""
        if match_index < 0 or match_index >= len(self.matches):
            return
        self.current_match_index = match_index
        match = self.matches[match_index]
        self.current_page = match.page_number
        self.current_pdf_index = self._doc_index_for_match(match)
        if self.current_pdf_index is not None:
            self.results_list.index = self.current_pdf_index
        await self._render_current_page()
        self._update_status()

    def _doc_index_for_match(self, match: SearchMatch) -> Optional[int]:
        """Find document index containing `match` in `self.documents`."""
        for idx, doc in enumerate(self.documents):
            if match.pdf_path == doc.path:
                return idx
        return None

    async def _render_current_page(self) -> None:
        """Render the currently selected PDF page into the preview pane."""
        if self.current_pdf_index is None or self.current_page is None:
            return
        doc = self.documents[self.current_pdf_index]
        try:
            output = await asyncio.to_thread(render_page, doc.path, self.current_page)
        except Exception as exc:
            self.preview.show_message(f"Render failed: {exc}")
            return
        self.preview.show_image(output)

    async def _next_match(self) -> None:
        """Advance to the next search match, if one exists."""
        if self.current_match_index is None or self.current_match_index >= len(self.matches) - 1:
            return
        await self._jump_to_match(self.current_match_index + 1)

    async def _previous_match(self) -> None:
        """Move to the previous search match, if one exists."""
        if self.current_match_index is None or self.current_match_index <= 0:
            return
        await self._jump_to_match(self.current_match_index - 1)

    async def _next_page(self) -> None:
        """Move forward one page, crossing into next document when needed."""
        if self.current_pdf_index is None or self.current_page is None:
            return
        doc = self.documents[self.current_pdf_index]
        if self.current_page < doc.page_count:
            self.current_page += 1
            await self._render_current_page()
            self._update_status()
            return
        if self.current_pdf_index < len(self.documents) - 1:
            self.current_pdf_index += 1
            next_doc = self.documents[self.current_pdf_index]
            self._jump_to_doc_start(next_doc)
            await self._render_current_page()
            self._update_status()

    async def _previous_page(self) -> None:
        """Move backward one page, crossing into previous document when needed."""
        if self.current_pdf_index is None or self.current_page is None:
            return
        doc = self.documents[self.current_pdf_index]
        if self.current_page > 1:
            self.current_page -= 1
            await self._render_current_page()
            self._update_status()
            return
        if self.current_pdf_index > 0:
            self.current_pdf_index -= 1
            prev_doc = self.documents[self.current_pdf_index]
            self._jump_to_doc_end(prev_doc)
            await self._render_current_page()
            self._update_status()

    def _jump_to_doc_start(self, doc: PdfDoc) -> None:
        """Set selection to first match/page for a document."""
        if doc.matches:
            first_match = doc.matches[0]
            self.current_match_index = self.matches.index(first_match)
            self.current_page = first_match.page_number
            self.results_list.index = self.current_pdf_index
        else:
            self.current_page = 1

    def _jump_to_doc_end(self, doc: PdfDoc) -> None:
        """Set selection to last match/page for a document."""
        if doc.matches:
            last_match = doc.matches[-1]
            self.current_match_index = self.matches.index(last_match)
            self.current_page = last_match.page_number
            self.results_list.index = self.current_pdf_index
        else:
            self.current_page = doc.page_count

    def _update_status(self) -> None:
        """Render the bottom status line from current selection and focus state."""
        mode_name = self.active_search_mode.value
        if not self.documents or self.current_pdf_index is None:
            self.status.update(f"No results | mode: {mode_name} | focus: {self.focus_pane}")
            return
        doc = self.documents[self.current_pdf_index]
        page = self.current_page or 0
        match_position = self._match_position_in_doc(doc)
        match_count = len(doc.matches)
        match_text = f"match {match_position}/{match_count}" if match_count else "match 0/0"
        parts = [doc.path.name, f"page {page}/{doc.page_count}", match_text, f"mode: {mode_name}"]
        if self.current_match_index is not None:
            match = self.matches[self.current_match_index]
            if match.score is not None:
                parts.append(f"score {match.score:.3f}")
        parts.append(f"focus: {self.focus_pane}")
        self.status.update(" | ".join(parts))

    def _match_position_in_doc(self, doc: PdfDoc) -> int:
        """Return 1-based position of current match within `doc`, else 0."""
        if self.current_match_index is None:
            return 0
        match = self.matches[self.current_match_index]
        if match.pdf_path != doc.path:
            return 0
        for idx, item in enumerate(doc.matches, start=1):
            if item == match:
                return idx
        return 0


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for query string, root path, and search mode."""
    parser = argparse.ArgumentParser(description="Search PDFs with preview UI")
    parser.add_argument("query", help="Search query")
    parser.add_argument("path", nargs="?", default=".", help="Root path (default: .)")
    parser.add_argument("--regex", action="store_true", help="Treat query as regex")
    parser.add_argument("-s", "--semantic", action="store_true", help="Use semantic embedding search")
    parser.add_argument(
        "--set-embedding-provider",
        default="ollama",
        help="Embedding provider to use for semantic search (default: ollama)",
    )
    parser.add_argument(
        "--set-embedding-model",
        default="",
        help="Embedding model name for semantic search",
    )
    args = parser.parse_args()
    if args.semantic and args.regex:
        parser.error("--semantic cannot be combined with --regex")
    return args


def main() -> None:
    """CLI entrypoint: parse args, build app, and run Textual event loop."""
    args = parse_args()
    initial_mode = SearchMode.SEMANTIC if args.semantic else SearchMode.REGEX if args.regex else SearchMode.LITERAL
    app = PdfGrepApp(
        args.query,
        Path(args.path),
        args.regex,
        initial_mode=initial_mode,
        embedding_config=EmbeddingConfig(
            provider=args.set_embedding_provider,
            model=args.set_embedding_model,
        ),
    )
    app.run()


if __name__ == "__main__":
    main()
