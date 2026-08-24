import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "minios_help_sync", str(HERE.parent / "tools" / "sync_from_docs.py"))
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class SyncFixture(object):
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "docs"
        self.output = Path(self.temp.name) / "output"
        (self.root / ".vitepress" / "i18n").mkdir(parents=True)
        (self.root / "about").mkdir()
        (self.root / "translations" / "ru" / "about").mkdir(parents=True)
        self.sidebar = [
            {
                "key": "sidebar.about",
                "text": "About",
                "items": [
                    {"key": "sidebar.page", "text": "Page", "link": "/about/Page"},
                    {"key": "sidebar.other", "text": "Other", "link": "/about/Other"},
                ],
            }
        ]
        self._write_json(".vitepress/sidebar.json", self.sidebar)
        self._write_json(".vitepress/i18n/en.json", {"translations": {
            "sidebar.about": "About", "sidebar.page": "Page", "sidebar.other": "Other"
        }})
        self._write_json(".vitepress/i18n/ru.json", {"translations": {
            "sidebar.about": "О системе", "sidebar.page": "Страница", "sidebar.other": "Другое"
        }})
        index = """---
layout: home
hero:
  name: "MiniOS Wiki"
  text: "Offline help"
  tagline: "Local docs"
features:
  - title: Portable
    details: Works offline.
---
"""
        ru_index = index.replace("Offline help", "Локальная справка")
        self.write("index.md", index)
        self.write("translations/ru/index.md", ru_index)
        self.write("about/Page.md", "# Page\n\n[Other](./Other.md)\n\n## Anchor Here\n")
        self.write("about/Other.md", "# Other\n\nBack to [page](/about/Page#anchor-here).\n")
        self.write("translations/ru/about/Page.md", "# Страница\n\n[Другое](./Other.md)\n\n## Якорь здесь\n")

    def _write_json(self, relative, data):
        self.write(relative, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run(self):
        return sync.sync_docs(self.root, self.output, stderr=io.StringIO())

    def close(self):
        self.temp.cleanup()


class SynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.fx = SyncFixture()

    def tearDown(self):
        self.fx.close()

    def tree_digest(self):
        digest = hashlib.sha256()
        for path in sorted(self.fx.output.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(self.fx.output).as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_repeat_is_deterministic_and_removes_stale_files(self):
        self.fx.run()
        first = self.tree_digest()
        (self.fx.output / "stale.md").write_text("old", encoding="utf-8")
        self.fx.run()
        self.assertFalse((self.fx.output / "stale.md").exists())
        self.assertEqual(first, self.tree_digest())

    def test_frontmatter_removed_and_native_home_generated(self):
        self.fx.run()
        home = (self.fx.output / "en" / "index.md").read_text(encoding="utf-8")
        self.assertTrue(home.startswith("# MiniOS Wiki\n"))
        self.assertIn("**Offline help**", home)
        self.assertIn("## About", home)
        self.assertNotIn("layout: home", home)
        self.assertNotIn("hero:", home)

    def test_absolute_relative_and_anchor_links_are_normalized(self):
        self.fx.run()
        page = (self.fx.output / "en" / "about" / "Page.md").read_text(encoding="utf-8")
        other = (self.fx.output / "en" / "about" / "Other.md").read_text(encoding="utf-8")
        self.assertIn("[Other](/about/Other.md)", page)
        self.assertIn("[page](/about/Page.md#anchor-here)", other)

    def test_locale_and_english_fallback_are_recorded(self):
        manifest = self.fx.run()
        other = next(item for item in manifest["documents"] if item["canonical_id"] == "about/Other")
        self.assertIn("ru", other["fallback_locales"])
        self.assertEqual(other["translations"]["ru"], "ru/about/Other.md")
        fallback_text = (self.fx.output / "ru" / "about" / "Other.md").read_text(encoding="utf-8")
        self.assertTrue(fallback_text.startswith("# Other\n"))
        self.assertIn("Back to [page]", fallback_text)

    def test_localized_anchor_is_mapped_to_translated_heading(self):
        self.fx.write(
            "translations/ru/about/Other.md",
            "# Другое\n\n[Назад](/about/Page#anchor-here).\n")
        self.fx.run()
        other = (self.fx.output / "ru" / "about" / "Other.md").read_text(encoding="utf-8")
        self.assertIn("#якорь-здесь", other)

    def test_sidebar_missing_document_is_error(self):
        self.fx.sidebar[0]["items"] = self.fx.sidebar[0]["items"][:1]
        self.fx._write_json(".vitepress/sidebar.json", self.fx.sidebar)
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    def test_nonexistent_internal_target_is_error(self):
        self.fx.write("about/Page.md", "# Page\n\n[Missing](./Missing.md)\n")
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    def test_conflicting_canonical_id_is_error(self):
        self.fx.write("about/page.md", "# duplicate\n")
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    def test_parent_traversal_is_error(self):
        self.fx.write("about/Page.md", "# Page\n\n[Bad](../about/Other.md)\n")
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    @unittest.skipIf(os.name == "nt", "symlink semantics differ on Windows")
    def test_symlink_is_error(self):
        target = self.fx.root / "about" / "Other.md"
        link = self.fx.root / "about" / "Linked.md"
        link.symlink_to(target)
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    def test_invalid_utf8_is_error(self):
        (self.fx.root / "about" / "Page.md").write_bytes(b"# Page\n\xff")
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    def test_safe_html_is_converted(self):
        self.fx.write(
            "about/Page.md",
            "# Page\n\nA<br>B <kbd>Esc</kbd> <strong>bold</strong> <em>italic</em>\n\n## Anchor Here\n")
        self.fx.run()
        page = (self.fx.output / "en" / "about" / "Page.md").read_text(encoding="utf-8")
        self.assertIn("A  \nB", page)
        self.assertIn("`Esc`", page)
        self.assertIn("**bold**", page)
        self.assertIn("*italic*", page)

    def test_fenced_code_is_byte_for_byte_preserved_in_body(self):
        fenced = "```html\n<strong>do not transform</strong>\n[k](../outside.md)\n```"
        self.fx.write("about/Page.md", "# Page\n\n{}\n\n## Anchor Here\n".format(fenced))
        self.fx.run()
        page = (self.fx.output / "en" / "about" / "Page.md").read_text(encoding="utf-8")
        self.assertIn(fenced, page)


    def test_manifest_lists_available_translations(self):
        manifest = self.fx.run()
        page = next(item for item in manifest["documents"] if item["canonical_id"] == "about/Page")
        other = next(item for item in manifest["documents"] if item["canonical_id"] == "about/Other")
        self.assertEqual(page["available_translations"], ["en", "ru"])
        self.assertEqual(other["available_translations"], ["en"])

    def test_manifest_has_no_absolute_paths_or_nondeterministic_metadata(self):
        self.fx.run()
        raw = (self.fx.output / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.fx.root), raw)
        self.assertNotIn(str(self.fx.output), raw)
        self.assertNotIn("generated_at", raw)
        manifest = json.loads(raw)
        self.assertEqual(manifest["product_kind"], "minios-help-documents")
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["default_locale"], "en")


if __name__ == "__main__":
    unittest.main()
