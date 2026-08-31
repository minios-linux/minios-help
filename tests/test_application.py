import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from minios_help.application import HelpWindow, MiniOSHelpApplication
from minios_help.documents import LocalePreference
from tests.runtime_fixture import RuntimeFixture


GTK_READY = Gtk.init_check(None)[0]
_TEST_APP = None


def test_application():
    global _TEST_APP
    if _TEST_APP is None:
        _TEST_APP = MiniOSHelpApplication()
        _TEST_APP.register(None)
    return _TEST_APP


@unittest.skipUnless(GTK_READY, "GTK display is required")
class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.fx = RuntimeFixture()
        self.fx.run()
        self.preference_path = Path(self.fx.temp.name) / "settings.json"
        self.preference = LocalePreference(self.preference_path)
        self.preference.save("en")
        self.app = test_application()
        self.window = HelpWindow(
            self.app, docs_root=self.fx.output, preference=self.preference)
        self.window.show_all()
        self._drain()

    def tearDown(self):
        self.window.destroy()
        self.fx.close()
        self._drain()

    @staticmethod
    def _drain():
        while Gtk.events_pending():
            Gtk.main_iteration()

    def test_opens_home_without_duplicate_h1(self):
        self.assertEqual(self.window.current.canonical_id, "index")
        h1 = [item for item in self.window.markdown.get_headings() if item[0] == 1]
        self.assertEqual(len(h1), 1)


    def test_help_uses_precompiled_document_renderer(self):
        self.assertEqual(type(self.window.markdown).__name__, "DocumentTextView")
        self.assertFalse(hasattr(self.window.markdown, "set_markdown"))

    def test_document_view_is_direct_scrolled_window_child(self):
        self.assertIs(self.window.document_scroll.get_child(), self.window.markdown)

    def test_internal_link_navigation_and_history(self):
        self.window._on_markdown_link("/about/Page.md#anchor-here")
        self._drain()
        self.assertEqual(self.window.current.canonical_id, "about/Page")
        self.assertTrue(self.window.history.can_back)
        self.window._on_back()
        self._drain()
        self.assertEqual(self.window.current.canonical_id, "index")
        self.window._on_forward()
        self._drain()
        self.assertEqual(self.window.current.canonical_id, "about/Page")

    def test_manual_language_switch_keeps_page(self):
        self.window._open_document("about/Page")
        self.window.locale_combo.set_active_id("ru")
        self._drain()
        self.assertEqual(self.window.locale, "ru")
        self.assertEqual(self.window.current.canonical_id, "about/Page")
        self.assertEqual(self.preference.load(), "ru")

    def test_missing_translation_shows_fallback(self):
        self.window.locale_combo.set_active_id("ru")
        self._drain()
        self.window._open_document("about/Other")
        self._drain()
        self.assertTrue(self.window.current.fallback)
        self.assertTrue(self.window.fallback_bar.get_visible())
        self.assertTrue(self.window.current.text.startswith("Other"))

    def test_external_https_uses_system_uri_launcher(self):
        with mock.patch.object(Gio.AppInfo, "launch_default_for_uri", return_value=True) as launcher:
            self.assertTrue(self.window._on_markdown_link("https://minios.dev"))
            launcher.assert_called_once_with("https://minios.dev", None)

    def test_dangerous_uri_never_uses_system_uri_launcher(self):
        with mock.patch.object(Gio.AppInfo, "launch_default_for_uri", return_value=True) as launcher:
            with mock.patch.object(self.window, "_show_transient_message"):
                self.assertTrue(self.window._on_markdown_link("file:///etc/passwd"))
            launcher.assert_not_called()

    def test_search_opens_matching_page(self):
        self.window.search_worker.index.build()
        self.window.search_index = self.window.search_worker.index
        self.window.search_entry.set_text("Page")
        self.window._update_search_results()
        self.assertTrue(self.window._search_rows)
        self.window._open_search_row(self.window._search_rows[0])
        self._drain()
        self.assertEqual(self.window.current.canonical_id, "about/Page")

    def test_compact_window_controls_do_not_force_a_wide_minimum(self):
        minimum, _natural = self.window.get_preferred_width()
        header_minimum, _header_natural = self.window.get_titlebar().get_preferred_width()
        controls_minimum, _controls_natural = self.window.controls_row.get_preferred_width()
        # GTK 3.22 themes may add a two-pixel frame to the requested width.
        self.assertLessEqual(minimum, 424)
        self.assertLessEqual(header_minimum, 424)
        self.assertLessEqual(controls_minimum, 424)
        self.assertIs(self.window.search_entry.get_parent(), self.window.controls_row)
        self.assertIs(self.window.locale_combo.get_parent(), self.window.controls_row)

    def test_language_combo_uses_list_popup(self):
        self.assertIsInstance(self.window.locale_combo, Gtk.ComboBoxText)
        self.assertTrue(
            self.window.locale_combo.style_get_property("appears-as-list"))
        self.assertEqual(
            type(self.window.locale_combo.get_popup_accessible()).__name__,
            "TreeViewAccessible")

    def test_document_rendering_is_owned_by_minios_gui(self):
        context = self.window.markdown.get_style_context()
        self.assertTrue(context.has_class("minios-document-view"))
        self.assertFalse(context.has_class("minios-help-document"))
        css = (Path(__file__).resolve().parents[1] / "share" / "styles" /
               "style.css").read_text(encoding="utf-8")
        self.assertNotIn("minios-document-", css)

    def test_navigation_and_document_panes_use_same_rounded_frame(self):
        self.assertIsInstance(self.window.sidebar_frame, Gtk.Frame)
        self.assertIsInstance(self.window.document_frame, Gtk.Frame)
        self.assertTrue(self.window.sidebar_frame.get_style_context().has_class(
            "minios-help-pane"))
        self.assertTrue(self.window.document_frame.get_style_context().has_class(
            "minios-help-pane"))
        self.assertEqual(
            self.window.sidebar_scroll.get_shadow_type(), Gtk.ShadowType.NONE)
        self.assertEqual(
            self.window.document_scroll.get_shadow_type(), Gtk.ShadowType.NONE)
        self.assertIs(self.window.sidebar_frame.get_child(), self.window.sidebar_scroll)
        self.assertIs(self.window.document_frame.get_child(), self.window.document_scroll)
        css = (Path(__file__).resolve().parents[1] / "share" / "styles" /
               "style.css").read_text(encoding="utf-8")
        self.assertNotIn("minios-document-", css)
        self.assertIn("frame.minios-help-pane", css)
        self.assertIn("border-radius: 4px;", css)
        self.assertIn("padding: 2px;", css)

    def test_page_outline_lists_document_headings_and_can_be_hidden(self):
        self.window._open_document("about/Page")
        self._drain()
        self.assertTrue(self.window._toc_available)
        self.assertTrue(self.window.toc_button.get_sensitive())
        self.assertTrue(self.window.toc_title.get_style_context().has_class(
            "minios-help-toc-title"))
        self.assertEqual(len(self.window._toc_rows), 1)
        self.assertEqual(self.window._toc_rows[0].anchor, "anchor-here")
        self.assertEqual(
            self.window._toc_rows[0].get_child().get_text(), "Anchor Here")
        self.window._toc_manual = True
        self.window._last_toc_narrow = False
        self.window._sync_toc_visibility()
        self.assertTrue(self.window.toc_revealer.get_reveal_child())
        self.window._on_toc_toggle()
        self.assertFalse(self.window.toc_revealer.get_reveal_child())
        self.window._on_toc_toggle()
        self.assertTrue(self.window.toc_revealer.get_reveal_child())

    def test_page_outline_heading_is_bold_and_translatable(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "share" / "styles" / "style.css").read_text(
            encoding="utf-8")
        source = (root / "lib" / "minios_help" / "application.py").read_text(
            encoding="utf-8")
        pot = (root / "po" / "messages.pot").read_text(encoding="utf-8")
        russian = (root / "po" / "ru.po").read_text(encoding="utf-8")
        self.assertIn(
            ".minios-help-toc-title {\n    font-weight: bold;", css)
        self.assertIn('label=_("On this page")', source)
        self.assertIn('msgid "On this page"', pot)
        self.assertIn('msgstr "На этой странице"', russian)

    def test_page_outline_row_scrolls_to_anchor(self):
        self.window._open_document("about/Page")
        self._drain()
        row = self.window._toc_rows[0]
        with mock.patch.object(
                self.window.markdown, "scroll_to_anchor", return_value=True) as scroll:
            self.window._on_toc_row_activated(self.window.toc_list, row)
        scroll.assert_called_once_with("anchor-here")
        self.assertEqual(self.window.history.current.anchor, "anchor-here")

    def test_page_outline_is_disabled_without_section_headings(self):
        self.window._open_document("index")
        self._drain()
        self.assertFalse(self.window._toc_available)
        self.assertFalse(self.window.toc_button.get_sensitive())
        self.assertFalse(self.window.toc_revealer.get_reveal_child())

    def test_search_results_do_not_use_modal_keyboard_grab(self):
        self.window.search_worker.index.build()
        self.window.search_index = self.window.search_worker.index
        self.window.search_entry.set_text("Page")
        self.window._update_search_results()
        self._drain()
        self.assertTrue(self.window.search_popover.get_visible())
        self.assertFalse(self.window.search_popover.get_modal())
        self.assertTrue(self.window._search_rows)

    def test_sidebar_button_points_toward_hidden_side(self):
        with mock.patch("minios_help.application.new_icon", return_value=Gtk.Image()) as factory:
            self.window.sidebar_revealer.set_reveal_child(True)
            self.window._update_sidebar_button_icon()
            self.assertEqual(
                factory.call_args[0][0], "sidebar-hide-symbolic")
            self.window.sidebar_revealer.set_reveal_child(False)
            self.window._update_sidebar_button_icon()
            self.assertEqual(
                factory.call_args[0][0], "sidebar-show-symbolic")

    def test_narrow_window_hides_sidebar(self):
        allocation = type("Allocation", (), {"width": 600})()
        self.window._on_size_allocate(self.window, allocation)
        self.assertFalse(self.window.sidebar_revealer.get_reveal_child())
        self.window._on_sidebar_toggle()
        self.assertTrue(self.window.sidebar_revealer.get_reveal_child())


@unittest.skipUnless(GTK_READY, "GTK display is required")
class BrokenManifestTests(unittest.TestCase):
    def test_corrupt_manifest_shows_readable_error_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text("{broken", encoding="utf-8")
            app = test_application()
            window = HelpWindow(app, docs_root=root)
            text = window.markdown.get_buffer().get_text(
                window.markdown.get_buffer().get_start_iter(),
                window.markdown.get_buffer().get_end_iter(), True)
            self.assertIn("could not be loaded", text.lower())
            window.destroy()


if __name__ == "__main__":
    unittest.main()
