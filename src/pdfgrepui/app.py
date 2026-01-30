from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import List, Optional, Tuple

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static
from textual_image.widget import Image

from .indexer import index_pdf
from .models import PdfDoc, SearchMatch
from .renderer import render_page
from .search import find_candidate_pdfs


class PreviewPane(Container):
    def compose(self) -> ComposeResult:
        yield Label("", id="preview-message")

    def show_message(self, message: str) -> None:
        label = self.query_one("#preview-message", Label)
        for image in self.query(Image):
            image.display = False
        label.display = True
        label.update(message)

    def show_image(self, image_path: Path) -> None:
        label = self.query_one("#preview-message", Label)
        label.display = False
        images = list(self.query(Image))
        if images:
            image = images[0]
            image.display = True
            if hasattr(image, "set_image"):
                image.set_image(str(image_path))
            elif hasattr(image, "path"):
                image.path = str(image_path)
                image.refresh()
            else:
                image.update(str(image_path))
            return
        self.mount(Image(str(image_path), id="preview-image"))


class PdfGrepApp(App):
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

    #status {
        height: auto;
        border: tall $secondary;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "focus_left", "Focus Left"),
        ("l", "focus_right", "Focus Right"),
    ]

    def __init__(self, query: str, root: Path, regex: bool) -> None:
        super().__init__()
        self.query = query
        self.root = root
        self.regex = regex
        self.focus_pane = "left"
        self.documents: List[PdfDoc] = []
        self.matches: List[SearchMatch] = []
        self.current_match_index: Optional[int] = None
        self.current_pdf_index: Optional[int] = None
        self.current_page: Optional[int] = None
        self.results_list = ListView(id="results")
        self.preview = PreviewPane(id="preview")
        self.status = Static(id="status")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            yield self.results_list
            yield self.preview
        yield self.status
        yield Footer()

    async def on_mount(self) -> None:
        self._focus_left()
        self.status.update("Indexing...")
        await self._start_indexing()

    async def _start_indexing(self) -> None:
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
        candidates = find_candidate_pdfs(self.query, self.root)
        documents: List[PdfDoc] = []
        matches: List[SearchMatch] = []
        for pdf_path in candidates:
            doc = index_pdf(pdf_path, self.query, self.regex)
            if doc.matches:
                documents.append(doc)
                matches.extend(doc.matches)
        return documents, matches

    def _populate_results(self) -> None:
        self.results_list.clear()
        if not self.matches:
            self.results_list.append(ListItem(Label("No matches")))
            return
        for match in self.matches:
            label = f"{match.pdf_path.name} p{match.page_number}: {match.context}"
            self.results_list.append(ListItem(Label(label)))

    def _focus_left(self) -> None:
        self.focus_pane = "left"
        self.results_list.focus()
        self._update_status()

    def _focus_right(self) -> None:
        self.focus_pane = "right"
        self.preview.focus()
        self._update_status()

    def action_focus_left(self) -> None:
        self._focus_left()

    def action_focus_right(self) -> None:
        self._focus_right()

    async def on_key(self, event) -> None:  # type: ignore[override]
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

    async def _jump_to_selected(self) -> None:
        if not self.matches:
            return
        index = self.results_list.index
        if index is None:
            return
        await self._jump_to_match(index)

    async def _jump_to_match(self, match_index: int) -> None:
        if match_index < 0 or match_index >= len(self.matches):
            return
        self.current_match_index = match_index
        match = self.matches[match_index]
        self.current_page = match.page_number
        self.current_pdf_index = self._doc_index_for_match(match)
        await self._render_current_page()
        self._update_status()

    def _doc_index_for_match(self, match: SearchMatch) -> Optional[int]:
        for idx, doc in enumerate(self.documents):
            if doc.path == match.pdf_path:
                return idx
        return None

    async def _render_current_page(self) -> None:
        if self.current_pdf_index is None or self.current_page is None:
            return
        doc = self.documents[self.current_pdf_index]
        self.preview.show_message("Rendering...")
        try:
            output = await asyncio.to_thread(render_page, doc.path, self.current_page)
        except Exception as exc:
            self.preview.show_message(f"Render failed: {exc}")
            return
        self.preview.show_image(output)

    async def _next_match(self) -> None:
        if self.current_match_index is None:
            return
        if self.current_match_index >= len(self.matches) - 1:
            return
        await self._jump_to_match(self.current_match_index + 1)

    async def _previous_match(self) -> None:
        if self.current_match_index is None:
            return
        if self.current_match_index <= 0:
            return
        await self._jump_to_match(self.current_match_index - 1)

    async def _next_page(self) -> None:
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
        if doc.matches:
            first_match = doc.matches[0]
            self.current_match_index = self.matches.index(first_match)
            self.current_page = first_match.page_number
        else:
            self.current_page = 1

    def _jump_to_doc_end(self, doc: PdfDoc) -> None:
        if doc.matches:
            last_match = doc.matches[-1]
            self.current_match_index = self.matches.index(last_match)
            self.current_page = last_match.page_number
        else:
            self.current_page = doc.page_count

    def _update_status(self) -> None:
        if not self.documents or self.current_pdf_index is None:
            self.status.update(f"No results | focus: {self.focus_pane}")
            return
        doc = self.documents[self.current_pdf_index]
        page = self.current_page or 0
        match_position = self._match_position_in_doc(doc)
        match_count = len(doc.matches)
        match_text = f"match {match_position}/{match_count}" if match_count else "match 0/0"
        status = f"{doc.path.name} | page {page}/{doc.page_count} | {match_text} | focus: {self.focus_pane}"
        self.status.update(status)

    def _match_position_in_doc(self, doc: PdfDoc) -> int:
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
    parser = argparse.ArgumentParser(description="Search PDFs with preview UI")
    parser.add_argument("query", help="Search query")
    parser.add_argument("path", nargs="?", default=".", help="Root path (default: .)")
    parser.add_argument("--regex", action="store_true", help="Treat query as regex")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = PdfGrepApp(args.query, Path(args.path), args.regex)
    app.run()


if __name__ == "__main__":
    main()
