import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from minios_help.documents import (
    DocumentError, DocumentStore, LocalePreference, locale_candidates,
    normalize_locale)
from minios_help.navigation import NavigationHistory, resolve_link
from minios_help.search import SearchIndex, strip_markdown
from tests.test_sync import SyncFixture


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.fx = SyncFixture()
        self.fx.run()
        self.store = DocumentStore(self.fx.output)

    def tearDown(self):
        self.fx.close()

    def test_manifest_load_and_document_open(self):
        self.assertEqual(self.store.default_locale, "en")
        self.assertIn("ru", self.store.locales)
        page = self.store.open_document("about/Page", "en")
        self.assertEqual(page.canonical_id, "about/Page")
        self.assertIn("# Page", page.text)

    def test_locale_normalization_and_environment_order(self):
        self.assertEqual(normalize_locale("pt_BR.UTF-8"), "pt-BR")
        self.assertEqual(normalize_locale("ru_RU.UTF-8"), "ru-RU")
        env = {
            "LANGUAGE": "missing:ru_RU.UTF-8",
            "LC_ALL": "de_DE.UTF-8",
            "LC_MESSAGES": "fr_FR.UTF-8",
            "LANG": "en_US.UTF-8",
        }
        self.assertEqual(self.store.select_locale(environ=env), "ru")
        self.assertEqual(self.store.select_locale("pt_BR.UTF-8"), "en")
        self.assertEqual(locale_candidates(None, env)[:3], ["ru-RU", "ru", "de-DE"])

    def test_explicit_locale_wins(self):
        env = {"LANGUAGE": "en", "LANG": "en_US.UTF-8"}
        self.assertEqual(self.store.select_locale("ru", environ=env), "ru")

    def test_manual_locale_preference_is_saved_outside_docs(self):
        path = Path(self.fx.temp.name) / "config" / "settings.json"
        pref = LocalePreference(path)
        self.assertTrue(pref.save("pt_BR"))
        self.assertEqual(pref.load(), "pt-BR")
        self.assertFalse(str(path).startswith(str(self.fx.output)))


    def test_modified_document_checksum_is_rejected(self):
        path = self.fx.output / "en" / "about" / "Page.md"
        path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        with self.assertRaises(DocumentError):
            self.store.open_document("about/Page", "en")

    def test_missing_translation_falls_back_to_english(self):
        other = self.store.open_document("about/Other", "ru")
        self.assertTrue(other.fallback)
        self.assertEqual(other.locale, "en")
        self.assertTrue(other.text.startswith("# Other"))

    def test_corrupt_manifest_is_rejected(self):
        (self.fx.output / "manifest.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(DocumentError):
            DocumentStore(self.fx.output)

    def test_missing_manifest_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DocumentError, "not installed"):
                DocumentStore(directory)

    def test_symbolic_document_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "docs-link"
            link.symlink_to(self.fx.output, target_is_directory=True)
            with self.assertRaisesRegex(DocumentError, "symbolic documentation roots"):
                DocumentStore(link)

    def test_symbolic_manifest_is_rejected(self):
        manifest = self.fx.output / "manifest.json"
        real_manifest = self.fx.output / "manifest.real.json"
        manifest.rename(real_manifest)
        manifest.symlink_to(real_manifest.name)
        with self.assertRaisesRegex(DocumentError, "symbolic documentation manifests"):
            DocumentStore(self.fx.output)

    def test_internal_navigation_forms(self):
        cases = {
            "/about/Other.md": ("about/Other", ""),
            "/about/Other": ("about/Other", ""),
            "./Other.md": ("about/Other", ""),
            "#anchor-here": ("about/Page", "anchor-here"),
        }
        for uri, expected in cases.items():
            resolved = resolve_link(self.store, "about/Page", uri)
            self.assertEqual(resolved.kind, "internal")
            self.assertEqual((resolved.canonical_id, resolved.anchor), expected)

    def test_external_and_dangerous_uri_classification(self):
        for uri in ("https://minios.dev", "http://example.test", "mailto:test@example.test"):
            self.assertEqual(resolve_link(self.store, "about/Page", uri).kind, "external")
        for uri in ("file:///etc/passwd", "data:text/plain,x", "javascript:alert(1)",
                    "ftp://example.test", "//example.test", "../about/Other.md",
                    "/about/../about/Other.md",
                    "/docs/about/%2e%2e/about/Other.md"):
            self.assertEqual(resolve_link(self.store, "about/Page", uri).kind, "blocked")

    def test_history_back_forward_and_scroll_restore(self):
        history = NavigationHistory()
        history.visit("index", "en", scroll=5)
        history.visit("about/Page", "en", scroll=20)
        previous = history.back(current_scroll=42)
        self.assertEqual(previous.canonical_id, "index")
        self.assertEqual(previous.scroll, 5.0)
        following = history.forward(current_scroll=9)
        self.assertEqual(following.canonical_id, "about/Page")
        self.assertEqual(following.scroll, 42.0)

    def test_search_prefers_title_and_returns_anchor(self):
        index = SearchIndex(self.store, "ru").build()
        results = index.search("страница")
        self.assertTrue(results)
        self.assertEqual(results[0].canonical_id, "about/Page")
        self.assertTrue(results[0].anchor)

    def test_search_uses_english_only_for_missing_translation(self):
        index = SearchIndex(self.store, "ru").build()
        results = index.search("Other")
        self.assertTrue(any(item.canonical_id == "about/Other" for item in results))

    def test_markdown_search_text_has_no_markup(self):
        text = strip_markdown("# Title\n\nUse **bold**, `code` and [link](https://example.test).")
        self.assertIn("Title", text)
        self.assertIn("bold", text)
        self.assertNotIn("**", text)
        self.assertNotIn("](", text)


if __name__ == "__main__":
    unittest.main()
