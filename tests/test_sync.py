import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote


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

    def compiled(self, locale, canonical):
        relative = "index.json" if canonical == "index" else canonical + ".json"
        return json.loads((self.output / locale / relative).read_text(encoding="utf-8"))

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
        home = self.fx.compiled("en", "index")
        self.assertEqual(home["headings"][0]["title"], "MiniOS Wiki")
        self.assertIn("Offline help", home["plain_text"])
        self.assertIn("About", home["plain_text"])
        raw = json.dumps(home, ensure_ascii=False)
        self.assertNotIn("layout: home", raw)
        self.assertNotIn("hero:", raw)

    def test_absolute_relative_and_anchor_links_are_normalized(self):
        self.fx.run()
        page = json.dumps(self.fx.compiled("en", "about/Page"), ensure_ascii=False)
        other = json.dumps(self.fx.compiled("en", "about/Other"), ensure_ascii=False)
        self.assertIn("/about/Other.md", page)
        self.assertIn("/about/Page.md#anchor-here", other)

    def test_locale_and_english_fallback_are_recorded(self):
        manifest = self.fx.run()
        other = next(item for item in manifest["documents"] if item["canonical_id"] == "about/Other")
        self.assertIn("ru", other["fallback_locales"])
        self.assertEqual(other["translations"]["ru"], "ru/about/Other.json")
        fallback = self.fx.compiled("ru", "about/Other")
        self.assertTrue(fallback["plain_text"].startswith("Other"))
        self.assertIn("Back to page", fallback["plain_text"])

    def test_localized_anchor_is_mapped_to_translated_heading(self):
        self.fx.write(
            "translations/ru/about/Other.md",
            "# Другое\n\n[Назад](/about/Page#anchor-here).\n")
        self.fx.run()
        other = json.dumps(
            self.fx.compiled("ru", "about/Other"), ensure_ascii=False)
        self.assertIn("#якорь-здесь", unquote(other))

    def test_unlisted_markdown_is_not_bundled(self):
        self.fx.write("notes/Internal.md", "# Internal note\n")
        manifest = self.fx.run()
        canonical = [item["canonical_id"] for item in manifest["documents"]]
        self.assertNotIn("notes/Internal", canonical)

    def test_sidebar_discovers_documents_in_arbitrary_directories(self):
        self.fx.write("future-layout/New-Page.md", "# New page\n\nFuture docs.\n")
        self.fx.sidebar.append({
            "key": "sidebar.future",
            "text": "Future",
            "items": [{
                "key": "sidebar.newPage",
                "text": "New page",
                "link": "/future-layout/New-Page",
            }],
        })
        self.fx._write_json(".vitepress/sidebar.json", self.fx.sidebar)
        manifest = self.fx.run()
        canonical = [item["canonical_id"] for item in manifest["documents"]]
        self.assertIn("future-layout/New-Page", canonical)
        compiled = self.fx.compiled("en", "future-layout/New-Page")
        self.assertTrue(compiled["plain_text"].startswith("New page"))

    def test_missing_sidebar_source_is_error(self):
        self.fx.sidebar.append({
            "key": "sidebar.future",
            "text": "Future",
            "link": "/some-new-layout/Missing",
        })
        self.fx._write_json(".vitepress/sidebar.json", self.fx.sidebar)
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    def test_nonexistent_internal_target_is_error(self):
        self.fx.write("about/Page.md", "# Page\n\n[Missing](./Missing.md)\n")
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    def test_conflicting_canonical_id_is_error(self):
        self.fx.sidebar[0]["items"].append({
            "key": "sidebar.pageDuplicate",
            "text": "Duplicate",
            "link": "/about/page",
        })
        self.fx._write_json(".vitepress/sidebar.json", self.fx.sidebar)
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    def test_parent_traversal_is_error(self):
        self.fx.write("about/Page.md", "# Page\n\n[Bad](../about/Other.md)\n")
        with self.assertRaises(sync.SyncError):
            self.fx.run()

    @unittest.skipIf(os.name == "nt", "symlink semantics differ on Windows")
    def test_symlink_is_error(self):
        target = self.fx.root / "about" / "Other.md"
        link = self.fx.root / "about" / "Page.md"
        link.unlink()
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
        page = self.fx.compiled("en", "about/Page")
        self.assertIn("A\nB Esc bold italic", page["plain_text"])
        raw = json.dumps(page["nodes"], ensure_ascii=False)
        self.assertIn('"code"', raw)
        self.assertIn('"strong"', raw)
        self.assertIn('"emphasis"', raw)

    def test_br_inside_table_stays_in_same_cell(self):
        self.fx.write(
            "about/Page.md",
            "# Page\n\n"
            "| Parameter | Use | Description | Example |\n"
            "| --- | --- | --- | --- |\n"
            "| `perchmode` | Every boot | Mode. | "
            "`perchmode=native`<br>`perchmode=raw` |\n\n"
            "## Anchor Here\n")
        self.fx.run()
        page = self.fx.compiled("en", "about/Page")
        table = next(node for node in page["nodes"] if node[0] == "table")
        self.assertEqual(len(table[1]), 2)
        row = table[1][1]
        self.assertEqual(len(row[2]), 4)
        first = json.dumps(row[2][0], ensure_ascii=False)
        example = json.dumps(row[2][3], ensure_ascii=False)
        self.assertIn("perchmode", first)
        self.assertIn("perchmode=native", example)
        self.assertIn("perchmode=raw", example)
        self.assertNotIn("perchmode=native", first)

    def test_fenced_code_is_byte_for_byte_preserved_in_body(self):
        fenced = "```html\n<strong>do not transform</strong>\n[k](../outside.md)\n```"
        self.fx.write("about/Page.md", "# Page\n\n{}\n\n## Anchor Here\n".format(fenced))
        self.fx.run()
        page = self.fx.compiled("en", "about/Page")
        code_blocks = [node for node in page["nodes"] if node[0] == "code_block"]
        self.assertEqual(len(code_blocks), 1)
        self.assertEqual(code_blocks[0][1], "<strong>do not transform</strong>\n[k](../outside.md)")
        self.assertEqual(code_blocks[0][2], "html")


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
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["default_locale"], "en")


if __name__ == "__main__":
    unittest.main()
