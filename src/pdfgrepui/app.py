"""Textual CLI application wiring for search, navigation, preview rendering."""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

import textual_image.renderable  # must be imported before Textual app starts
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static
from textual_image.widget import Image

from .cache import (
    load_quick_access_lists,
    load_semantic_settings,
    save_quick_access_lists,
    save_semantic_settings,
)
from .indexer import extract_page_text, index_pdf, search_quick_access_entries
from .models import (
    EmbeddingConfig,
    PdfDoc,
    QuickAccessEntry,
    QuickAccessList,
    SearchConfig,
    SearchMatch,
    SearchMode,
    ViewState,
)
from .renderer import render_page
from .search import find_all_pdfs, find_candidate_pdfs
from .semantic import build_embedding_provider, cosine_similarity


SubmitHandler = Callable[[str], Awaitable[None] | None]
PickHandler = Callable[[int], Awaitable[None] | None]


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

    #modal-overlay {
        layer: overlay;
        width: 100%;
        height: 100%;
        display: none;
        align: center top;
        padding: 3 0 0 0;
        background: $surface 40%;
    }

    #modal-panel {
        width: 70%;
        max-width: 92;
        height: auto;
        border: tall $accent;
        background: $surface;
        padding: 0 1 1 1;
    }

    #modal-title {
        padding: 1 0 0 0;
        text-style: bold;
    }

    #modal-help {
        padding: 0 0 1 0;
        color: $text-muted;
    }

    #modal-input {
        width: 100%;
        margin: 0 0 1 0;
    }

    #modal-list {
        max-height: 16;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "focus_left", "Focus Left"),
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
        if embedding_config is None:
            persisted = load_semantic_settings()
            embedding_config = EmbeddingConfig(
                provider=persisted.get("provider", "ollama"),
                model=persisted.get("model", ""),
            )
        self.embedding_config = embedding_config

        self.focus_pane = "left"
        self.view_state = ViewState.NORMAL

        self.documents: List[PdfDoc] = []
        self.matches: List[SearchMatch] = []
        self.current_match_index: Optional[int] = None
        self.current_pdf_index: Optional[int] = None
        self.current_page: Optional[int] = None

        self.quick_access_lists: List[QuickAccessList] = load_quick_access_lists()
        self.quick_access_browser_items: List[QuickAccessList] = []
        self.quick_access_browser_query = ""
        self.quick_access_browser_mode = self.default_search_mode
        self.active_quick_access_list_id: Optional[str] = None
        self.quick_access_visible_entries: List[QuickAccessEntry] = []
        self.quick_access_matches: List[SearchMatch] = []
        self.quick_access_current_entry_index: Optional[int] = None
        self.quick_access_current_match_index: Optional[int] = None
        self.quick_access_query = ""
        self.quick_access_search_mode = self.default_search_mode

        self.results_list = ListView(id="results")
        self.preview = PreviewPane(id="preview")
        self.status = Static(id="status")
        self.modal_overlay = Container(
            Container(
                Label("", id="modal-title"),
                Static("", id="modal-help"),
                Input(id="modal-input"),
                ListView(id="modal-list"),
                id="modal-panel",
            ),
            id="modal-overlay",
        )

        self.modal_open = False
        self.modal_submit_handler: Optional[SubmitHandler] = None
        self.modal_pick_handler: Optional[PickHandler] = None
        self.modal_restore_focus = "left"
        self.preview_fullscreen = False
        self._focus_before_fullscreen = "left"

        self._leader_pending = False
        self._delete_pending = False
        self._semantic_chord_pending = False
        self._mark_pending = ""
        self._quick_access_chord = ""

    def compose(self) -> ComposeResult:
        """Compose the two-pane layout plus status/footer widgets."""
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            yield self.results_list
            yield self.preview
        yield self.status
        yield self.modal_overlay
        yield Footer()

    async def on_mount(self) -> None:
        """Kick off indexing after the UI is mounted."""
        self._focus_left()
        self.status.update("Indexing...")
        await self._start_indexing()

    def _current_search_config(self, mode: Optional[SearchMode] = None, query: Optional[str] = None) -> SearchConfig:
        """Return normalized search configuration for the current app state."""
        return SearchConfig(
            query=query if query is not None else self.query,
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
        self._populate_normal_results()
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
            provider, query_embedding = self._semantic_backend(search_config.query)
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

    def _semantic_backend(self, query: str):
        """Build semantic provider and query embedding."""
        if not self.embedding_config.model:
            raise ValueError("Semantic search requires a saved embedding model or --set-embedding-model")
        provider = build_embedding_provider(self.embedding_config)
        query_embeddings = provider.embed([query])
        if not query_embeddings:
            raise ValueError("Embedding provider returned no query embedding")
        return provider, query_embeddings[0]

    def _persist_quick_access_lists(self) -> None:
        """Persist quick-access lists to disk."""
        save_quick_access_lists(self.quick_access_lists)

    def _populate_normal_results(self) -> None:
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

    def _populate_quick_access_browser(self) -> None:
        """Populate the quick-access list browser."""
        self.results_list.clear()
        if not self.quick_access_browser_items:
            empty_text = "No quick access lists"
            if self.quick_access_browser_query:
                empty_text = "No matching quick access lists"
            self.results_list.append(ListItem(Label(empty_text)))
            self.preview.show_message("Create a list with space a.")
            return
        for qa_list in self.quick_access_browser_items:
            count = len(qa_list.entries)
            summary = f"{qa_list.name} ({count} page{'s' if count != 1 else ''})"
            self.results_list.append(ListItem(Label(summary)))
        self.preview.show_message("Quick access lists. Press Enter to open a list.")

    def _populate_quick_access_entries(self) -> None:
        """Populate the currently opened quick-access list entries."""
        self.results_list.clear()
        if not self.quick_access_visible_entries:
            empty_text = "No saved pages"
            if self.quick_access_query:
                empty_text = "No matching saved pages"
            self.results_list.append(ListItem(Label(empty_text)))
            self.preview.show_message(empty_text)
            return
        for entry in self.quick_access_visible_entries:
            note = entry.note.strip().replace("\n", " ") or "(no note)"
            snippet = note[:64]
            label_text = f"{entry.pdf_path.name} p.{entry.page_number} | {snippet}"
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
        """Textual action handler for compatibility with existing flows."""
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

    def _cancel_pending_chords(self) -> None:
        """Reset all multi-key chord state."""
        self._leader_pending = False
        self._delete_pending = False
        self._semantic_chord_pending = False
        self._mark_pending = ""
        self._quick_access_chord = ""

    def _cancel_quick_access_chord(self) -> None:
        """Clear the `l s q a` chord state."""
        self._quick_access_chord = ""

    async def on_key(self, event) -> None:  # type: ignore[override]
        """Handle pane-specific key bindings, mode toggles, and navigation."""
        if await self._process_key(event.key):
            event.stop()

    async def _process_key(self, key: str) -> bool:
        """Process one keypress, including multi-key chord continuations."""
        if self.preview_fullscreen and key == "escape":
            self._set_preview_fullscreen(False)
            return True
        if self.modal_open:
            return await self._handle_modal_key(key)
        if key == "escape" and self.view_state is not ViewState.NORMAL:
            self._exit_quick_access_mode()
            return True
        pending = await self._handle_pending_chords(key)
        if pending == "consumed":
            return True
        if pending == "reprocess":
            return await self._process_key(key)

        if key == "space":
            self._leader_pending = True
            self.status.update("Leader pending")
            return True
        if key == "s":
            self._semantic_chord_pending = True
            self.status.update("Semantic chord pending: press /")
            return True
        if key == "m" and self.view_state is ViewState.NORMAL:
            self._mark_pending = "m"
            self.status.update("Mark chord pending: m p l")
            return True
        if key == "l":
            self._focus_right()
            return True
        if key == "f":
            self._set_preview_fullscreen(not self.preview_fullscreen)
            return True
        if key == "enter" and self.focus_pane == "left":
            await self._jump_to_selected()
            return True
        if self.focus_pane == "left":
            if key == "j":
                self.results_list.action_cursor_down()
                return True
            if key == "k":
                self.results_list.action_cursor_up()
                return True
        if self.focus_pane == "right":
            if key == "j":
                await self._next_right_pane_item()
                return True
            if key == "k":
                await self._previous_right_pane_item()
                return True
            if key == "n":
                await self._next_match_like_item()
                return True
            if key in ("N", "shift+n"):
                await self._previous_match_like_item()
                return True
        return False

    async def _handle_pending_chords(self, key: str) -> str | None:
        """Handle continuation keys for the app's sequential key chords."""
        if self._leader_pending:
            self._leader_pending = False
            if key == "l":
                self._quick_access_chord = "l"
                self.status.update("Quick access chord pending: space l s q a")
                return "consumed"
            if self.view_state is ViewState.QUICK_ACCESS_BROWSER:
                if key == "a":
                    self._prompt_create_quick_access_list()
                    return "consumed"
                if key == "r":
                    self._prompt_rename_quick_access_list()
                    return "consumed"
                if key == "d":
                    self._delete_pending = True
                    self.status.update("Delete chord pending: space d d")
                    return "consumed"
            return "reprocess"

        if self._delete_pending:
            self._delete_pending = False
            if self.view_state is ViewState.QUICK_ACCESS_BROWSER and key == "d":
                self._prompt_delete_quick_access_list()
                return "consumed"
            return "reprocess"

        if self._semantic_chord_pending:
            self._semantic_chord_pending = False
            if key in ("/", "slash"):
                self._open_search(SearchMode.SEMANTIC)
                return "consumed"
            return "reprocess"

        if self._mark_pending == "m":
            if key == "p":
                self._mark_pending = "mp"
                self.status.update("Mark chord pending: m p l")
                return "consumed"
            self._mark_pending = ""
            return "reprocess"

        if self._mark_pending == "mp":
            self._mark_pending = ""
            if key == "l":
                await self._prompt_save_current_page()
                return "consumed"
            return "reprocess"

        if self._quick_access_chord == "l":
            if key == "s":
                self._quick_access_chord = "ls"
                return "consumed"
            self._quick_access_chord = ""
            return "reprocess"

        if self._quick_access_chord == "ls":
            if key == "q":
                self._quick_access_chord = "lsq"
                return "consumed"
            self._quick_access_chord = ""
            return "reprocess"

        if self._quick_access_chord == "lsq":
            if key == "a":
                self._quick_access_chord = ""
                self._enter_quick_access_browser()
                return "consumed"
            self._quick_access_chord = ""
            return "reprocess"

        return None

    def _show_input_modal(
        self,
        title: str,
        placeholder: str,
        initial_value: str,
        help_text: str,
        handler: SubmitHandler,
    ) -> None:
        """Show the modal overlay with a single input field."""
        self._cancel_pending_chords()
        self.modal_open = True
        self.modal_submit_handler = handler
        self.modal_pick_handler = None
        self.modal_restore_focus = self.focus_pane
        self.modal_overlay.display = True
        title_widget = self.query_one("#modal-title", Label)
        help_widget = self.query_one("#modal-help", Static)
        input_widget = self.query_one("#modal-input", Input)
        list_widget = self.query_one("#modal-list", ListView)
        title_widget.update(title)
        help_widget.update(help_text)
        input_widget.display = True
        input_widget.placeholder = placeholder
        input_widget.value = initial_value
        input_widget.focus()
        input_widget.cursor_position = len(initial_value)
        list_widget.display = False
        list_widget.clear()

    def _show_picker_modal(
        self,
        title: str,
        options: Sequence[str],
        help_text: str,
        handler: PickHandler,
    ) -> None:
        """Show the modal overlay with a pick list."""
        self._cancel_pending_chords()
        self.modal_open = True
        self.modal_submit_handler = None
        self.modal_pick_handler = handler
        self.modal_restore_focus = self.focus_pane
        self.modal_overlay.display = True
        title_widget = self.query_one("#modal-title", Label)
        help_widget = self.query_one("#modal-help", Static)
        input_widget = self.query_one("#modal-input", Input)
        list_widget = self.query_one("#modal-list", ListView)
        title_widget.update(title)
        help_widget.update(help_text)
        input_widget.display = False
        input_widget.value = ""
        list_widget.display = True
        list_widget.clear()
        for option in options:
            list_widget.append(ListItem(Label(option)))
        if options:
            list_widget.index = 0
        list_widget.focus()

    def _hide_modal(self) -> None:
        """Hide the modal overlay and restore focus."""
        self.modal_open = False
        self.modal_overlay.display = False
        self.modal_submit_handler = None
        self.modal_pick_handler = None
        if self.modal_restore_focus == "right":
            self.preview.focus()
        else:
            self.results_list.focus()
        self._update_status()

    async def _handle_modal_key(self, key: str) -> bool:
        """Handle keys while a modal prompt or picker is open."""
        if key == "escape":
            self._hide_modal()
            return True
        if self.modal_pick_handler is None:
            return False
        list_widget = self.query_one("#modal-list", ListView)
        if key == "j":
            list_widget.action_cursor_down()
            return True
        if key == "k":
            list_widget.action_cursor_up()
            return True
        if key == "enter":
            index = list_widget.index
            if index is None:
                return True
            handler = self.modal_pick_handler
            self._hide_modal()
            await self._invoke_pick_handler(handler, index)
            return True
        return False

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Submit overlay input to its registered handler."""
        if event.input.id != "modal-input":
            return
        if self.modal_submit_handler is None:
            return
        value = event.value
        handler = self.modal_submit_handler
        self._hide_modal()
        await self._invoke_submit_handler(handler, value)

    async def _invoke_submit_handler(self, handler: Optional[SubmitHandler], value: str) -> None:
        """Run the current modal submit handler."""
        if handler is None:
            return
        result = handler(value)
        if asyncio.iscoroutine(result):
            await result

    async def _invoke_pick_handler(self, handler: Optional[PickHandler], index: int) -> None:
        """Run the current modal pick handler."""
        if handler is None:
            return
        result = handler(index)
        if asyncio.iscoroutine(result):
            await result

    def _open_search(self, mode: SearchMode) -> None:
        """Open search prompt scoped to the current view."""
        if self.view_state is ViewState.NORMAL:
            title = "Search PDFs"
            placeholder = "Enter new search and press Enter"
            initial = self.query
            help_text = "Esc cancels. Use s/ for semantic search."

            async def submit(value: str) -> None:
                new_query = value.strip()
                if not new_query:
                    self.status.update("Search unchanged: query is empty.")
                    return
                self.query = new_query
                self.active_search_mode = mode
                await self._start_search()

            self._show_input_modal(title, placeholder, initial, help_text, submit)
            return

        if self.view_state is ViewState.QUICK_ACCESS_BROWSER:
            title = "Search Quick Access Lists"
            placeholder = "Enter list-name search and press Enter"
            initial = self.quick_access_browser_query
            help_text = "Searches list names. Empty query clears the filter."

            def submit(value: str) -> None:
                self.quick_access_browser_query = value.strip()
                self.quick_access_browser_mode = mode
                try:
                    self._refresh_quick_access_browser()
                except Exception as exc:
                    self.status.update(f"Quick access search failed: {exc}")

            self._show_input_modal(title, placeholder, initial, help_text, submit)
            return

        qa_list = self._active_quick_access_list()
        title = f"Search Quick Access: {qa_list.name if qa_list else ''}".strip()
        placeholder = "Search saved pages and notes"
        initial = self.quick_access_query
        help_text = "Searches both the saved note and the source page text. Empty query clears the filter."

        async def submit(value: str) -> None:
            self.quick_access_query = value.strip()
            self.quick_access_search_mode = mode
            try:
                await self._apply_quick_access_document_search()
            except Exception as exc:
                self.status.update(f"Quick access search failed: {exc}")

        self._show_input_modal(title, placeholder, initial, help_text, submit)

    def action_open_search(self) -> None:
        """Show the appropriate search prompt for the current view."""
        if self._semantic_chord_pending:
            self._semantic_chord_pending = False
            self._open_search(SearchMode.SEMANTIC)
            return
        if self.view_state is ViewState.QUICK_ACCESS_DOCUMENT:
            self._open_search(self.quick_access_search_mode if self.quick_access_query else self.default_search_mode)
            return
        if self.view_state is ViewState.QUICK_ACCESS_BROWSER:
            self._open_search(self.quick_access_browser_mode if self.quick_access_browser_query else self.default_search_mode)
            return
        self._open_search(self.default_search_mode)

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
        """Jump preview context to the currently selected row."""
        index = self.results_list.index
        if index is None:
            return
        if self.view_state is ViewState.NORMAL:
            await self._jump_to_document(index)
            return
        if self.view_state is ViewState.QUICK_ACCESS_BROWSER:
            await self._open_quick_access_list(index)
            return
        await self._jump_to_quick_access_entry(index)

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
        await self._show_pdf_page(match.pdf_path, match.page_number)
        self._update_status()

    def _doc_index_for_match(self, match: SearchMatch) -> Optional[int]:
        """Find document index containing `match` in `self.documents`."""
        for idx, doc in enumerate(self.documents):
            if match.pdf_path == doc.path:
                return idx
        return None

    async def _show_pdf_page(self, pdf_path: Path, page_number: int) -> None:
        """Render a PDF page into the preview pane."""
        try:
            output = await asyncio.to_thread(render_page, pdf_path, page_number)
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
            await self._show_pdf_page(doc.path, self.current_page)
            self._update_status()
            return
        if self.current_pdf_index < len(self.documents) - 1:
            self.current_pdf_index += 1
            next_doc = self.documents[self.current_pdf_index]
            self._jump_to_doc_start(next_doc)
            await self._show_pdf_page(next_doc.path, self.current_page or 1)
            self._update_status()

    async def _previous_page(self) -> None:
        """Move backward one page, crossing into previous document when needed."""
        if self.current_pdf_index is None or self.current_page is None:
            return
        doc = self.documents[self.current_pdf_index]
        if self.current_page > 1:
            self.current_page -= 1
            await self._show_pdf_page(doc.path, self.current_page)
            self._update_status()
            return
        if self.current_pdf_index > 0:
            self.current_pdf_index -= 1
            prev_doc = self.documents[self.current_pdf_index]
            self._jump_to_doc_end(prev_doc)
            await self._show_pdf_page(prev_doc.path, self.current_page or prev_doc.page_count)
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

    def _refresh_quick_access_browser(self) -> None:
        """Rebuild the quick-access browser list using the current filter."""
        items = list(self.quick_access_lists)
        query = self.quick_access_browser_query
        mode = self.quick_access_browser_mode
        if query:
            if mode is SearchMode.SEMANTIC:
                provider, query_embedding = self._semantic_backend(query)
                scored: List[Tuple[float, QuickAccessList]] = []
                embeddings = provider.embed([item.name for item in items]) if items else []
                for qa_list, embedding in zip(items, embeddings):
                    score = cosine_similarity(query_embedding, embedding)
                    if score > 0.0:
                        scored.append((score, qa_list))
                scored.sort(key=lambda item: item[0], reverse=True)
                items = [qa_list for _, qa_list in scored]
            else:
                items = [qa_list for qa_list in items if self._text_matches(qa_list.name, query, mode)]
        self.quick_access_browser_items = items
        self._populate_quick_access_browser()
        if items:
            self.results_list.index = 0
        self._update_status()

    def _active_quick_access_list(self) -> Optional[QuickAccessList]:
        """Return the currently opened quick-access list, if any."""
        if self.active_quick_access_list_id is None:
            return None
        for qa_list in self.quick_access_lists:
            if qa_list.id == self.active_quick_access_list_id:
                return qa_list
        return None

    def _entry_by_id(self, entry_id: str) -> Optional[QuickAccessEntry]:
        """Return an entry from the active quick-access list by id."""
        qa_list = self._active_quick_access_list()
        if qa_list is None:
            return None
        for entry in qa_list.entries:
            if entry.id == entry_id:
                return entry
        return None

    async def _open_quick_access_list(self, browser_index: int) -> None:
        """Open a quick-access list from the browser view."""
        if browser_index < 0 or browser_index >= len(self.quick_access_browser_items):
            return
        qa_list = self.quick_access_browser_items[browser_index]
        self.view_state = ViewState.QUICK_ACCESS_DOCUMENT
        self.active_quick_access_list_id = qa_list.id
        self.quick_access_query = ""
        self.quick_access_matches = []
        self.quick_access_current_match_index = None
        self.quick_access_visible_entries = list(qa_list.entries)
        self.quick_access_current_entry_index = 0 if qa_list.entries else None
        self._populate_quick_access_entries()
        if self.quick_access_visible_entries:
            self.results_list.index = 0
            await self._show_quick_access_entry(0)
        else:
            self.preview.show_message(f"{qa_list.name} has no saved pages.")
        self._focus_left()
        self._update_status()

    async def _show_quick_access_entry(self, entry_index: int) -> None:
        """Render the selected quick-access entry in the preview pane."""
        if entry_index < 0 or entry_index >= len(self.quick_access_visible_entries):
            return
        entry = self.quick_access_visible_entries[entry_index]
        self.quick_access_current_entry_index = entry_index
        self.results_list.index = entry_index
        if self.quick_access_matches:
            self.quick_access_current_match_index = entry_index if entry_index < len(self.quick_access_matches) else None
        await self._show_pdf_page(entry.pdf_path, entry.page_number)
        self._update_status()

    async def _jump_to_quick_access_entry(self, entry_index: int) -> None:
        """Jump to one quick-access entry from the left pane."""
        await self._show_quick_access_entry(entry_index)

    async def _apply_quick_access_document_search(self) -> None:
        """Search the opened quick-access list or show all entries when cleared."""
        qa_list = self._active_quick_access_list()
        if qa_list is None:
            return
        if not self.quick_access_query:
            self.quick_access_matches = []
            self.quick_access_visible_entries = list(qa_list.entries)
            self.quick_access_current_match_index = None
            self.quick_access_current_entry_index = 0 if self.quick_access_visible_entries else None
            self._populate_quick_access_entries()
            if self.quick_access_visible_entries:
                self.results_list.index = 0
                await self._show_quick_access_entry(0)
            else:
                self.preview.show_message(f"{qa_list.name} has no saved pages.")
            return

        matches = await asyncio.to_thread(self._search_quick_access_entries, qa_list.entries)
        self.quick_access_matches = matches
        visible_entries: List[QuickAccessEntry] = []
        for match in matches:
            if match.source_id is None:
                continue
            entry = self._entry_by_id(match.source_id)
            if entry is not None:
                visible_entries.append(entry)
        self.quick_access_visible_entries = visible_entries
        self.quick_access_current_entry_index = 0 if visible_entries else None
        self.quick_access_current_match_index = 0 if matches else None
        self._populate_quick_access_entries()
        if visible_entries:
            self.results_list.index = 0
            await self._show_quick_access_entry(0)
        else:
            self.preview.show_message("No matching saved pages.")
        self._update_status()

    def _search_quick_access_entries(self, entries: Sequence[QuickAccessEntry]) -> List[SearchMatch]:
        """Search the active quick-access list entries."""
        search_config = SearchConfig(
            query=self.quick_access_query,
            mode=self.quick_access_search_mode,
            embedding=self.embedding_config,
        )
        if search_config.mode is SearchMode.SEMANTIC:
            provider, query_embedding = self._semantic_backend(search_config.query)
        else:
            provider = None
            query_embedding = None
        return search_quick_access_entries(
            entries,
            search_config,
            embedding_provider=provider,
            query_embedding=query_embedding,
        )

    def _enter_quick_access_browser(self) -> None:
        """Switch from normal search to quick-access browser mode."""
        self._cancel_pending_chords()
        self.view_state = ViewState.QUICK_ACCESS_BROWSER
        self.quick_access_browser_query = ""
        self.quick_access_browser_mode = self.default_search_mode
        self.active_quick_access_list_id = None
        self.quick_access_visible_entries = []
        self.quick_access_matches = []
        self.quick_access_current_entry_index = None
        self.quick_access_current_match_index = None
        self._refresh_quick_access_browser()
        self._focus_left()

    def _exit_quick_access_mode(self) -> None:
        """Return from any quick-access state to the normal search view."""
        self._cancel_pending_chords()
        self.view_state = ViewState.NORMAL
        self.active_quick_access_list_id = None
        self.quick_access_visible_entries = []
        self.quick_access_matches = []
        self.quick_access_current_entry_index = None
        self.quick_access_current_match_index = None
        self._populate_normal_results()
        if self.current_pdf_index is not None and self.documents:
            self.results_list.index = self.current_pdf_index
        self._focus_left()
        self._update_status()

    async def _next_quick_access_entry(self) -> None:
        """Move forward one saved quick-access entry."""
        if self.quick_access_current_entry_index is None:
            return
        if self.quick_access_current_entry_index >= len(self.quick_access_visible_entries) - 1:
            return
        await self._show_quick_access_entry(self.quick_access_current_entry_index + 1)

    async def _previous_quick_access_entry(self) -> None:
        """Move backward one saved quick-access entry."""
        if self.quick_access_current_entry_index is None or self.quick_access_current_entry_index <= 0:
            return
        await self._show_quick_access_entry(self.quick_access_current_entry_index - 1)

    async def _next_quick_access_match(self) -> None:
        """Advance within quick-access search results."""
        if not self.quick_access_matches:
            return
        if self.quick_access_current_match_index is None or self.quick_access_current_match_index >= len(self.quick_access_matches) - 1:
            return
        self.quick_access_current_match_index += 1
        await self._show_quick_access_entry(self.quick_access_current_match_index)

    async def _previous_quick_access_match(self) -> None:
        """Move backward within quick-access search results."""
        if not self.quick_access_matches:
            return
        if self.quick_access_current_match_index is None or self.quick_access_current_match_index <= 0:
            return
        self.quick_access_current_match_index -= 1
        await self._show_quick_access_entry(self.quick_access_current_match_index)

    async def _next_right_pane_item(self) -> None:
        """Move the right pane forward according to the current view."""
        if self.view_state is ViewState.NORMAL:
            await self._next_page()
            return
        if self.view_state is ViewState.QUICK_ACCESS_DOCUMENT:
            await self._next_quick_access_entry()

    async def _previous_right_pane_item(self) -> None:
        """Move the right pane backward according to the current view."""
        if self.view_state is ViewState.NORMAL:
            await self._previous_page()
            return
        if self.view_state is ViewState.QUICK_ACCESS_DOCUMENT:
            await self._previous_quick_access_entry()

    async def _next_match_like_item(self) -> None:
        """Advance to the next match-like item for the current view."""
        if self.view_state is ViewState.NORMAL:
            await self._next_match()
            return
        if self.view_state is ViewState.QUICK_ACCESS_DOCUMENT:
            await self._next_quick_access_match()

    async def _previous_match_like_item(self) -> None:
        """Move to the previous match-like item for the current view."""
        if self.view_state is ViewState.NORMAL:
            await self._previous_match()
            return
        if self.view_state is ViewState.QUICK_ACCESS_DOCUMENT:
            await self._previous_quick_access_match()

    async def _prompt_save_current_page(self) -> None:
        """Prompt for a quick-access list and note, then save the current page."""
        if self.view_state is not ViewState.NORMAL:
            self.status.update("Save-page quick access is only available in normal PDF browsing.")
            return
        if self.current_pdf_index is None or self.current_page is None:
            self.status.update("No current page to save.")
            return
        if not self.quick_access_lists:
            self.status.update("No quick access lists yet. Open quick access and create one with space a.")
            return
        options = [qa_list.name for qa_list in self.quick_access_lists]

        async def pick_list(index: int) -> None:
            if index < 0 or index >= len(self.quick_access_lists):
                return
            qa_list = self.quick_access_lists[index]

            async def save_note(value: str) -> None:
                note = value.strip()
                if not note:
                    self.status.update("Save cancelled: note is empty.")
                    return
                doc = self.documents[self.current_pdf_index]
                entry = QuickAccessEntry(
                    id=uuid4().hex,
                    pdf_path=doc.path,
                    page_number=self.current_page or 1,
                    note=note,
                    page_text=await asyncio.to_thread(extract_page_text, doc.path, self.current_page or 1),
                )
                qa_list.entries.append(entry)
                self._persist_quick_access_lists()
                if self.view_state is ViewState.QUICK_ACCESS_DOCUMENT and self.active_quick_access_list_id == qa_list.id:
                    await self._apply_quick_access_document_search()
                self.status.update(f"Saved page {entry.page_number} from {doc.path.name} to {qa_list.name}.")

            self._show_input_modal(
                f"Save Page To {qa_list.name}",
                "Enter a short note about this page",
                "",
                "Empty note cancels the save.",
                save_note,
            )

        self._show_picker_modal(
            "Choose Quick Access List",
            options,
            "Use j/k and Enter to choose a list. Esc cancels.",
            pick_list,
        )

    def _selected_quick_access_list(self) -> Optional[QuickAccessList]:
        """Return the currently highlighted quick-access list in browser view."""
        if self.view_state is not ViewState.QUICK_ACCESS_BROWSER:
            return None
        index = self.results_list.index
        if index is None or index < 0 or index >= len(self.quick_access_browser_items):
            return None
        return self.quick_access_browser_items[index]

    def _prompt_create_quick_access_list(self) -> None:
        """Prompt for a new quick-access list name."""

        def submit(value: str) -> None:
            name = value.strip()
            if not name:
                self.status.update("Create cancelled: name is empty.")
                return
            new_list = QuickAccessList(id=uuid4().hex, name=name)
            self.quick_access_lists.append(new_list)
            self._persist_quick_access_lists()
            self._refresh_quick_access_browser()
            if new_list in self.quick_access_browser_items:
                self.results_list.index = self.quick_access_browser_items.index(new_list)
            self.status.update(f"Created quick access list: {name}")

        self._show_input_modal(
            "Create Quick Access List",
            "Enter list name",
            "",
            "Empty name cancels creation.",
            submit,
        )

    def _prompt_rename_quick_access_list(self) -> None:
        """Prompt to rename the highlighted quick-access list."""
        qa_list = self._selected_quick_access_list()
        if qa_list is None:
            self.status.update("Select a quick access list to rename.")
            return

        def submit(value: str) -> None:
            name = value.strip()
            if not name:
                self.status.update("Rename cancelled: name is empty.")
                return
            qa_list.name = name
            self._persist_quick_access_lists()
            self._refresh_quick_access_browser()
            self.status.update(f"Renamed quick access list to: {name}")

        self._show_input_modal(
            f"Rename {qa_list.name}",
            "Enter new list name",
            qa_list.name,
            "Empty name cancels rename.",
            submit,
        )

    def _prompt_delete_quick_access_list(self) -> None:
        """Prompt to delete the highlighted quick-access list."""
        qa_list = self._selected_quick_access_list()
        if qa_list is None:
            self.status.update("Select a quick access list to delete.")
            return

        def pick(index: int) -> None:
            if index != 1:
                self.status.update("Delete cancelled.")
                return
            self.quick_access_lists = [item for item in self.quick_access_lists if item.id != qa_list.id]
            self._persist_quick_access_lists()
            self._refresh_quick_access_browser()
            self.status.update(f"Deleted quick access list: {qa_list.name}")

        self._show_picker_modal(
            f"Delete {qa_list.name}?",
            ["Cancel", "Delete list"],
            "Choose Delete list to confirm. Esc cancels.",
            pick,
        )

    def _text_matches(self, text: str, query: str, mode: SearchMode) -> bool:
        """Return whether text matches query for literal or regex searches."""
        if mode is SearchMode.REGEX:
            try:
                return re.search(query, text, re.IGNORECASE) is not None
            except re.error:
                return False
        return query.lower() in text.lower()

    def _update_status(self) -> None:
        """Render the bottom status line from current selection and focus state."""
        if self.view_state is ViewState.QUICK_ACCESS_BROWSER:
            query_text = self.quick_access_browser_query or "-"
            self.status.update(
                " | ".join(
                    [
                        "view: quick-access browser",
                        f"lists {len(self.quick_access_browser_items)}/{len(self.quick_access_lists)}",
                        f"search: {query_text}",
                        f"mode: {self.quick_access_browser_mode.value}",
                        f"focus: {self.focus_pane}",
                    ]
                )
            )
            return

        if self.view_state is ViewState.QUICK_ACCESS_DOCUMENT:
            qa_list = self._active_quick_access_list()
            total_entries = len(self.quick_access_visible_entries)
            entry_position = (self.quick_access_current_entry_index or 0) + 1 if total_entries else 0
            parts = [
                f"view: quick-access {qa_list.name if qa_list else ''}".strip(),
                f"item {entry_position}/{total_entries}",
                f"search: {self.quick_access_query or '-'}",
                f"mode: {self.quick_access_search_mode.value}",
            ]
            if self.quick_access_matches:
                match_position = (self.quick_access_current_match_index or 0) + 1
                parts.append(f"match {match_position}/{len(self.quick_access_matches)}")
            parts.append(f"focus: {self.focus_pane}")
            self.status.update(" | ".join(parts))
            return

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
        default=None,
        help="Embedding provider to use for semantic search",
    )
    parser.add_argument(
        "--set-embedding-model",
        default=None,
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
    persisted = load_semantic_settings()
    provider = args.set_embedding_provider or persisted.get("provider", "ollama")
    model = args.set_embedding_model or persisted.get("model", "")
    if args.set_embedding_provider is not None or args.set_embedding_model is not None:
        save_semantic_settings(provider, model)
    app = PdfGrepApp(
        args.query,
        Path(args.path),
        args.regex,
        initial_mode=initial_mode,
        embedding_config=EmbeddingConfig(
            provider=provider,
            model=model,
        ),
    )
    app.run()


if __name__ == "__main__":
    main()
