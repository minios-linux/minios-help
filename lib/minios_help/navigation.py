"""Safe internal-link resolution and navigation history."""

from __future__ import absolute_import

import posixpath
from collections import namedtuple
from urllib.parse import unquote, urlsplit

SAFE_EXTERNAL_SCHEMES = ("http", "https", "mailto")
BLOCKED_SCHEMES = ("file", "data", "javascript")
LinkResolution = namedtuple("LinkResolution", "kind canonical_id anchor uri")


def _has_control(value):
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def resolve_link(store, current_id, uri):
    """Classify a link without ever launching it."""
    if not isinstance(uri, str) or not uri or "\x00" in uri or _has_control(uri):
        return LinkResolution("blocked", None, "", None)
    parsed = urlsplit(uri)
    scheme = parsed.scheme.lower()
    if scheme:
        if scheme in SAFE_EXTERNAL_SCHEMES:
            return LinkResolution("external", None, "", uri)
        return LinkResolution("blocked", None, "", None)
    if uri.startswith("//") or parsed.query:
        return LinkResolution("blocked", None, "", None)

    anchor = unquote(parsed.fragment or "")
    path = unquote(parsed.path or "")
    if "\\" in path or _has_control(path):
        return LinkResolution("blocked", None, "", None)
    if not path:
        canonical = store.resolve_canonical(current_id)
        if canonical is None:
            return LinkResolution("blocked", None, "", None)
        return LinkResolution("internal", canonical, anchor, None)
    if ".." in path.split("/"):
        return LinkResolution("blocked", None, "", None)

    if path.startswith("/docs/"):
        path = path[len("/docs/"):]
    elif path.startswith("/"):
        path = path[1:]
    else:
        base = "" if current_id == "index" else posixpath.dirname(current_id)
        path = posixpath.join(base, path)
    if path.endswith(".md"):
        path = path[:-3]
    path = posixpath.normpath(path)
    if path in ("", "."):
        path = "index"
    if path == ".." or path.startswith("../") or path.startswith("/"):
        return LinkResolution("blocked", None, "", None)
    if any(part in ("", ".", "..") for part in path.split("/")):
        return LinkResolution("blocked", None, "", None)
    canonical = store.resolve_canonical(path)
    if canonical is None:
        return LinkResolution("blocked", None, "", None)
    return LinkResolution("internal", canonical, anchor, None)


class HistoryEntry(object):
    __slots__ = ("canonical_id", "locale", "anchor", "scroll")

    def __init__(self, canonical_id, locale, anchor="", scroll=0.0):
        self.canonical_id = canonical_id
        self.locale = locale
        self.anchor = anchor or ""
        self.scroll = float(scroll or 0.0)

    def copy(self):
        return HistoryEntry(self.canonical_id, self.locale, self.anchor, self.scroll)


class NavigationHistory(object):
    def __init__(self):
        self._entries = []
        self._index = -1

    @property
    def can_back(self):
        return self._index > 0

    @property
    def can_forward(self):
        return 0 <= self._index < len(self._entries) - 1

    @property
    def current(self):
        if self._index < 0:
            return None
        return self._entries[self._index]

    def visit(self, canonical_id, locale, anchor="", scroll=0.0):
        entry = HistoryEntry(canonical_id, locale, anchor, scroll)
        if self._index >= 0:
            current = self._entries[self._index]
            if (current.canonical_id == canonical_id and
                    current.locale == locale and current.anchor == (anchor or "")):
                current.scroll = float(scroll or 0.0)
                return current
        del self._entries[self._index + 1:]
        self._entries.append(entry)
        self._index = len(self._entries) - 1
        return entry

    def replace_current(self, canonical_id=None, locale=None, anchor=None, scroll=None):
        current = self.current
        if current is None:
            return None
        if canonical_id is not None:
            current.canonical_id = canonical_id
        if locale is not None:
            current.locale = locale
        if anchor is not None:
            current.anchor = anchor
        if scroll is not None:
            current.scroll = float(scroll)
        return current

    def capture_scroll(self, scroll):
        if self.current is not None:
            self.current.scroll = float(scroll or 0.0)

    def back(self, current_scroll=None):
        if current_scroll is not None:
            self.capture_scroll(current_scroll)
        if not self.can_back:
            return None
        self._index -= 1
        return self.current.copy()

    def forward(self, current_scroll=None):
        if current_scroll is not None:
            self.capture_scroll(current_scroll)
        if not self.can_forward:
            return None
        self._index += 1
        return self.current.copy()
