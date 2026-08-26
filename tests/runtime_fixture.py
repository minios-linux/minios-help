import hashlib
import json
import tempfile
from pathlib import Path


def _document(title, anchor, body="", extra_headings=None):
    nodes = [["heading", 1, anchor, [["text", title]]]]
    if body:
        nodes.append(["block", "paragraph", [["text", body]]])
    headings = [{"level": 1, "title": title, "anchor": anchor}]
    for level, heading_title, heading_anchor in extra_headings or ():
        nodes.append([
            "heading", level, heading_anchor,
            [["text", heading_title]],
        ])
        headings.append({
            "level": level, "title": heading_title,
            "anchor": heading_anchor,
        })
    plain = "\n".join(
        [title] + ([body] if body else []) +
        [item[1] for item in extra_headings or ()])
    return {
        "product_kind": "minios-markup-document",
        "schema_version": 1,
        "nodes": nodes,
        "headings": headings,
        "plain_text": plain,
    }


class RuntimeFixture(object):
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "docs"
        self.output.mkdir(parents=True)
        self.documents = {
            "index": {
                "en": _document("MiniOS Wiki", "minios-wiki", "Offline help Local docs"),
                "ru": _document("MiniOS Wiki", "minios-wiki", "Локальная справка"),
            },
            "about/Page": {
                "en": _document("Page", "page", "Other", [(2, "Anchor Here", "anchor-here")]),
                "ru": _document("Страница", "страница", "Другое", [(2, "Якорь здесь", "якорь-здесь")]),
            },
            "about/Other": {
                "en": _document("Other", "other", "Back to page"),
                "ru": _document("Other", "other", "Back to page"),
            },
        }
        self._write_bundle()

    def _write_document(self, locale, canonical, document):
        relative = "{}/{}.json".format(locale, canonical)
        path = self.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path.write_text(payload, encoding="utf-8")
        return relative, hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _write_bundle(self):
        records = []
        order = {"index": 0, "about/Page": 1, "about/Other": 2}
        for canonical in ("index", "about/Page", "about/Other"):
            translations = {}
            hashes = {}
            titles = {}
            for locale in ("en", "ru"):
                document = self.documents[canonical][locale]
                relative, digest = self._write_document(locale, canonical, document)
                translations[locale] = relative
                hashes[locale] = digest
                titles[locale] = document["headings"][0]["title"]
            fallback = ["ru"] if canonical == "about/Other" else []
            records.append({
                "canonical_id": canonical,
                "path": translations["en"],
                "section": "About" if canonical != "index" else "",
                "section_key": "sidebar.about" if canonical != "index" else "",
                "order": order[canonical],
                "title": titles["en"],
                "titles": titles,
                "available_translations": ["en"] if fallback else ["en", "ru"],
                "translations": translations,
                "sha256": hashes,
                "internal_links": [],
                "fallback_locales": fallback,
            })
        manifest = {
            "product_kind": "minios-help-documents",
            "schema_version": 2,
            "default_locale": "en",
            "locales": ["en", "ru"],
            "navigation": [{
                "key": "sidebar.about",
                "labels": {"en": "About", "ru": "О системе"},
                "canonical_id": "",
                "items": [
                    {
                        "key": "sidebar.page",
                        "labels": {"en": "Page", "ru": "Страница"},
                        "canonical_id": "about/Page",
                        "items": [],
                    },
                    {
                        "key": "sidebar.other",
                        "labels": {"en": "Other", "ru": "Другое"},
                        "canonical_id": "about/Other",
                        "items": [],
                    },
                ],
            }],
            "documents": records,
            "assets": {},
        }
        self.manifest = manifest
        (self.output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

    def close(self):
        self.temp.cleanup()

    def run(self):
        return self.manifest
