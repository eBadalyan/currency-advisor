from __future__ import annotations

from collections.abc import Callable
from html.parser import HTMLParser

_Attrs = list[tuple[str, str | None]]


class HtmlRateRowParser(HTMLParser):
    """Extracts one row from a bank's exchange-rate HTML table, keyed by the
    currency code in its first cell.

    Generalizes a pattern shared by several bank sites (verified live):
    a plain server-rendered table where each row's first cell holds a
    currency code — however deeply nested in icons/spans/divs, since only
    the accumulated text within the cell is used — and later cells hold
    numeric rates. Only the first matching table is captured and parsing
    stops there: every site checked so far renders just one live table for
    its default/cash tab in the static HTML, with other tabs either absent
    or empty placeholders — continuing past it risks silently picking up a
    same-shaped non-cash/card table instead.
    """

    def __init__(
        self, table_matches: Callable[[dict[str, str | None]], bool], row_key: str
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._table_matches = table_matches
        self._row_key = row_key
        self._table_depth = 0
        self._in_target_table = False
        self._table_done = False
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self.row: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: _Attrs) -> None:
        if self._table_done:
            return
        if tag == "table":
            if self._in_target_table:
                self._table_depth += 1
            elif self._table_matches(dict(attrs)):
                self._in_target_table = True
                self._table_depth = 1
        elif self._in_target_table and tag == "tr":
            self._current_row = []
        elif self._in_target_table and tag == "td":
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if self._table_done:
            return
        if tag == "table" and self._in_target_table:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_target_table = False
                self._table_done = True
        elif tag == "td" and self._current_cell is not None:
            if self._current_row is not None:
                self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self.row is None and self._current_row[:1] == [self._row_key]:
                self.row = self._current_row
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)
