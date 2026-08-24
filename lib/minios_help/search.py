"""Offline Unicode full-text search for the generated MiniOS Help bundle."""

from __future__ import absolute_import

import re
import threading
import unicodedata
from collections import namedtuple

SearchResult = namedtuple(
    "SearchResult", "canonical_id title section snippet anchor score")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def _plain_inline(value):
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[`*_~>#|]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_markdown(text):
    lines = []
    in_fence = False
    fence = None
    for raw in text.splitlines():
        match = re.match(r"^[ \t]*(```|~~~)", raw)
        if match:
            token = match.group(1)
            if not in_fence:
                in_fence = True
                fence = token
            elif token == fence:
                in_fence = False
                fence = None
            continue
        line = raw
        if not in_fence:
            line = re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+", "", line)
            line = re.sub(r"^#{1,6}\s+", "", line)
        lines.append(_plain_inline(line))
    return "\n".join(item for item in lines if item)


def _slug(value):
    value = unicodedata.normalize("NFKC", _plain_inline(value)).casefold()
    chars = []
    for char in value:
        category = unicodedata.category(char)
        if category[0] in ("L", "N", "M") or char in ("-", "_"):
            chars.append(char)
        elif char.isspace():
            chars.append("-")
    return re.sub(r"-+", "-", "".join(chars)).strip("-") or "section"


def headings(text):
    used = {}
    result = []
    for match in HEADING_RE.finditer(text):
        title = _plain_inline(match.group(2))
        base = _slug(title)
        count = used.get(base, 0)
        used[base] = count + 1
        anchor = base if count == 0 else "{}-{}".format(base, count)
        result.append((len(match.group(1)), title, anchor))
    return result


def _snippet(text, query, width=150):
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    index = compact.casefold().find(query.casefold())
    if index < 0:
        return compact[:width] + ("…" if len(compact) > width else "")
    start = max(0, index - width // 3)
    end = min(len(compact), start + width)
    prefix = "…" if start else ""
    suffix = "…" if end < len(compact) else ""
    return prefix + compact[start:end].strip() + suffix


class SearchIndex(object):
    def __init__(self, store, locale_name):
        self.store = store
        self.locale = store.select_locale(locale_name)
        self._records = []
        self.ready = False

    def build(self):
        records = []
        for item in self.store.all_documents():
            content = self.store.open_document(item["canonical_id"], self.locale)
            page_headings = headings(content.text)
            title = content.metadata.get("titles", {}).get(content.requested_locale)
            if not title:
                title = page_headings[0][1] if page_headings else content.metadata.get("title", "")
            body = strip_markdown(content.text)
            records.append({
                "canonical_id": content.canonical_id,
                "title": title,
                "title_fold": title.casefold(),
                "section": self.store.section_label(content.metadata, self.locale),
                "body": body,
                "body_fold": body.casefold(),
                "headings": page_headings,
                "order": content.metadata.get("order", 0),
            })
        self._records = records
        self.ready = True
        return self

    def search(self, query, limit=30):
        query = (query or "").strip()
        if not query or not self.ready:
            return []
        folded = query.casefold()
        tokens = [item for item in re.findall(r"\w+", folded, re.UNICODE) if item]
        results = []
        for record in self._records:
            title_hits = record["title_fold"].count(folded)
            body_hits = record["body_fold"].count(folded)
            if not title_hits and not body_hits and tokens:
                if not all(token in record["body_fold"] or token in record["title_fold"] for token in tokens):
                    continue
            score = title_hits * 100 + body_hits * 5
            if tokens:
                score += sum(25 for token in tokens if token in record["title_fold"])
                score += sum(2 for token in tokens if token in record["body_fold"])
            anchor = ""
            for _level, heading_title, heading_anchor in record["headings"]:
                heading_fold = heading_title.casefold()
                if folded in heading_fold or (tokens and all(token in heading_fold for token in tokens)):
                    anchor = heading_anchor
                    score += 30
                    break
            if score <= 0:
                continue
            results.append(SearchResult(
                record["canonical_id"], record["title"], record["section"],
                _snippet(record["body"], query), anchor, score))
        results.sort(key=lambda item: (-item.score, item.title.casefold(), item.canonical_id))
        return results[:limit]


class SearchWorker(object):
    """Build search data off the GTK main loop and report through GLib."""
    def __init__(self, store, locale_name):
        self.index = SearchIndex(store, locale_name)
        self._thread = None

    def start(self, callback):
        def run():
            error = None
            try:
                self.index.build()
            except Exception as caught:
                error = caught
            try:
                from gi.repository import GLib
                GLib.idle_add(callback, self.index if error is None else None, error)
            except ImportError:
                callback(self.index if error is None else None, error)
        self._thread = threading.Thread(target=run, name="minios-help-search")
        self._thread.daemon = True
        self._thread.start()
        return self._thread
