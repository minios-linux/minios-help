"""GTK 3 application for browsing the offline MiniOS documentation bundle."""

from __future__ import absolute_import

import gettext
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from minios_gui import DocumentTextView, apply_minios_css, new_header_bar, new_icon

from .documents import DocumentError, DocumentStore, LocalePreference
from .navigation import NavigationHistory, resolve_link
from .search import SearchWorker

APPLICATION_ID = "dev.minios.Help"
DOMAIN = "minios-help"
APP_NAME = "MiniOS Help"
SIDEBAR_THRESHOLD = 760
TOC_THRESHOLD = 900


def _repo_root():
    return Path(__file__).resolve().parent.parent.parent


def data_root():
    override = os.environ.get("MINIOS_HELP_DATA_DIR")
    if override:
        return Path(override)
    installed = Path("/usr/share/minios-help")
    if (installed / "docs" / "manifest.json").is_file():
        return installed
    return _repo_root() / "share"


def locale_root():
    override = os.environ.get("MINIOS_HELP_LOCALE_DIR")
    if override:
        return override
    installed = "/usr/share/locale"
    if os.path.isdir(installed):
        return installed
    return str(_repo_root() / "locale")


gettext.bindtextdomain(DOMAIN, locale_root())
gettext.textdomain(DOMAIN)
_ = gettext.gettext


_LOCALE_LABELS = {
    "de": "Deutsch",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "id": "Bahasa Indonesia",
    "it": "Italiano",
    "pt-BR": "Português (Brasil)",
    "ru": "Русский",
}


def _accessible(widget, name):
    accessible = widget.get_accessible()
    if accessible is not None:
        accessible.set_name(name)


def _header_button(icon_name, tooltip, callback):
    button = Gtk.Button()
    button.set_image(new_icon(icon_name, accessible_name=tooltip))
    button.set_tooltip_text(tooltip)
    button.set_focus_on_click(False)
    _accessible(button, tooltip)
    button.connect("clicked", callback)
    return button


class HelpWindow(Gtk.ApplicationWindow):
    def __init__(self, application, docs_root=None, preference=None):
        Gtk.ApplicationWindow.__init__(
            self, application=application, title=_(APP_NAME))
        self.set_default_size(980, 650)
        self.set_size_request(420, 300)
        self.docs_root = Path(docs_root) if docs_root else data_root() / "docs"
        self.preference = preference or LocalePreference()
        self.store = None
        self.current = None
        self.history = NavigationHistory()
        self.search_worker = None
        self.search_index = None
        self._tree_paths = {}
        self._syncing_tree = False
        self._last_narrow = None
        self._last_toc_narrow = None
        self._sidebar_manual = None
        self._toc_manual = None
        self._toc_rows = []
        self._toc_available = False
        self._search_rows = []
        self._load_store()
        self._build_header()
        self._build_content()
        self.connect("key-press-event", self._on_key_press)
        self.connect("size-allocate", self._on_size_allocate)
        if self.store is not None:
            explicit = self.preference.load()
            self.locale = self.store.select_locale(explicit)
            self._populate_locales()
            self._rebuild_navigation()
            self._open_document("index", add_history=True)
            self._start_search_index()
        else:
            self.locale = "en"
            self._show_fatal_error()

    def _load_store(self):
        try:
            self.store = DocumentStore(self.docs_root)
            self.store_error = None
        except DocumentError as error:
            self.store_error = error

    def _build_header(self):
        header = new_header_bar(_(APP_NAME))
        self.set_titlebar(header)

        self.sidebar_button = _header_button(
            "sidebar-hide-symbolic", _("Show or hide contents"),
            self._on_sidebar_toggle)
        header.pack_start(self.sidebar_button)

        self.back_button = _header_button(
            "go-previous-symbolic", _("Back"), self._on_back)
        self.forward_button = _header_button(
            "go-next-symbolic", _("Forward"), self._on_forward)
        self.home_button = _header_button(
            "go-home-symbolic", _("Home"), self._on_home)
        header.pack_start(self.back_button)
        header.pack_start(self.forward_button)
        header.pack_start(self.home_button)

        self.toc_button = _header_button(
            "view-list-symbolic", _("Show or hide page outline"),
            self._on_toc_toggle)
        self.toc_button.set_sensitive(False)
        header.pack_end(self.toc_button)

        self._update_nav_buttons()

    def _build_search_popover(self):
        self.search_popover = Gtk.Popover.new(self.search_entry)
        self.search_popover.set_position(Gtk.PositionType.BOTTOM)
        # Search results must not take the keyboard grab from SearchEntry.
        self.search_popover.set_modal(False)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_size_request(380, 300)
        self.search_message = Gtk.Label(
            label=_("Type to search the local documentation."), xalign=0)
        self.search_message.set_margin_top(10)
        self.search_message.set_margin_bottom(10)
        self.search_message.set_margin_start(12)
        self.search_message.set_margin_end(12)
        outer.pack_start(self.search_message, False, False, 0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.search_list = Gtk.ListBox()
        self.search_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.search_list.connect("row-activated", self._on_search_row_activated)
        scrolled.add(self.search_list)
        outer.pack_start(scrolled, True, True, 0)
        self.search_popover.add(outer)

    def _build_content(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.set_margin_top(6)
        controls.set_margin_bottom(6)
        controls.set_margin_start(8)
        controls.set_margin_end(8)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(_("Search documentation"))
        self.search_entry.set_tooltip_text(_("Search documentation"))
        self.search_entry.set_hexpand(True)
        _accessible(self.search_entry, _("Search documentation"))
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_activate)
        controls.pack_start(self.search_entry, True, True, 0)

        self.locale_combo = Gtk.ComboBoxText()
        # Keep the native combo-box appearance, but force the popup to use
        # GTK's list implementation. The menu implementation aligns the active
        # row with the combo box and can leave a large blank area for items near
        # the end of the list when the popup is constrained by the screen edge.
        # This style property must be present before the combo box is realized.
        self._locale_combo_css = Gtk.CssProvider()
        self._locale_combo_css.load_from_data(
            b"* { -GtkComboBox-appears-as-list: true; }")
        self.locale_combo.get_style_context().add_provider(
            self._locale_combo_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.locale_combo.set_tooltip_text(_("Documentation language"))
        _accessible(self.locale_combo, _("Documentation language"))
        self.locale_combo.connect("changed", self._on_locale_changed)
        controls.pack_end(self.locale_combo, False, False, 0)
        root.pack_start(controls, False, False, 0)
        self.controls_row = controls
        self._build_search_popover()

        self.fallback_bar = Gtk.InfoBar()
        self.fallback_bar.set_message_type(Gtk.MessageType.INFO)
        self.fallback_bar.set_no_show_all(True)
        self.fallback_label = Gtk.Label(xalign=0)
        self.fallback_label.set_line_wrap(True)
        self.fallback_bar.get_content_area().pack_start(
            self.fallback_label, True, True, 0)
        root.pack_start(self.fallback_bar, False, False, 0)

        pane = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pane.set_margin_start(8)
        pane.set_margin_end(8)
        pane.set_margin_bottom(8)
        root.pack_start(pane, True, True, 0)

        self.sidebar_revealer = Gtk.Revealer()
        self.sidebar_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.sidebar_revealer.set_reveal_child(True)
        self.sidebar_frame = Gtk.Frame()
        self.sidebar_frame.set_shadow_type(Gtk.ShadowType.NONE)
        self.sidebar_frame.set_size_request(250, -1)
        self.sidebar_frame.get_style_context().add_class("minios-help-pane")
        self.sidebar_scroll = Gtk.ScrolledWindow()
        self.sidebar_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sidebar_scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.tree_store = Gtk.TreeStore(str, str)
        self.tree = Gtk.TreeView(model=self.tree_store)
        self.tree.set_headers_visible(False)
        self.tree.set_enable_search(True)
        renderer = Gtk.CellRendererText()
        renderer.set_property("ellipsize", 3)
        column = Gtk.TreeViewColumn(_("Contents"), renderer, text=0)
        self.tree.append_column(column)
        self.tree.get_selection().connect("changed", self._on_tree_selection)
        _accessible(self.tree, _("Documentation contents"))
        self.sidebar_scroll.add(self.tree)
        self.sidebar_frame.add(self.sidebar_scroll)
        self.sidebar_revealer.add(self.sidebar_frame)
        pane.pack_start(self.sidebar_revealer, False, False, 0)

        self.document_frame = Gtk.Frame()
        self.document_frame.set_shadow_type(Gtk.ShadowType.NONE)
        self.document_frame.get_style_context().add_class("minios-help-pane")
        self.document_scroll = Gtk.ScrolledWindow()
        self.document_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.document_scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.document_scroll.set_hexpand(True)
        self.document_scroll.set_vexpand(True)
        self.markdown = DocumentTextView(
            link_handler=self._on_markdown_link,
            asset_resolver=self._resolve_document_asset)
        self.markdown.set_left_margin(24)
        self.markdown.set_right_margin(24)
        self.markdown.set_top_margin(18)
        self.markdown.set_bottom_margin(24)
        self.document_scroll.add(self.markdown)
        self.document_scroll.get_vadjustment().connect(
            "value-changed", self._on_document_scroll)
        self.document_frame.add(self.document_scroll)
        pane.pack_start(self.document_frame, True, True, 0)

        self.toc_revealer = Gtk.Revealer()
        self.toc_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.toc_revealer.set_reveal_child(False)
        toc_outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toc_outer.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
            False, False, 0)
        toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        toc_box.set_size_request(205, -1)
        self.toc_title = Gtk.Label(label=_("On this page"), xalign=0)
        self.toc_title.get_style_context().add_class("minios-help-toc-title")
        self.toc_title.set_margin_top(4)
        self.toc_title.set_margin_start(4)
        toc_box.pack_start(self.toc_title, False, False, 0)
        self.toc_list = Gtk.ListBox()
        self.toc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list.get_style_context().add_class("minios-help-toc")
        self.toc_list.connect("row-activated", self._on_toc_row_activated)
        self.toc_scroll = Gtk.ScrolledWindow()
        self.toc_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.toc_scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.toc_scroll.add(self.toc_list)
        toc_box.pack_start(self.toc_scroll, True, True, 0)
        toc_outer.pack_start(toc_box, True, True, 0)
        self.toc_revealer.add(toc_outer)
        pane.pack_end(self.toc_revealer, False, False, 0)

    def _show_fatal_error(self):
        self.back_button.set_sensitive(False)
        self.forward_button.set_sensitive(False)
        self.home_button.set_sensitive(False)
        self.sidebar_button.set_sensitive(False)
        self.locale_combo.set_sensitive(False)
        self.search_entry.set_sensitive(False)
        message = _("The local MiniOS documentation could not be loaded.")
        if self.store_error:
            message = "{}\n\n{}".format(message, str(self.store_error))
        self.markdown.set_document({
            "product_kind": "minios-markup-document",
            "schema_version": 1,
            "nodes": [
                ["heading", 1, "minios-help", [["text", _("MiniOS Help")]]],
                ["block", "paragraph", [["text", message]]],
            ],
        })
        self._rebuild_toc()

    def _resolve_document_asset(self, relative):
        if self.store is None:
            raise DocumentError("documentation store is unavailable")
        return str(self.store.asset_path(relative))

    def _populate_locales(self):
        self.locale_combo.handler_block_by_func(self._on_locale_changed)
        try:
            self.locale_combo.remove_all()
            active = 0
            for index, locale_name in enumerate(self.store.locales):
                label = _LOCALE_LABELS.get(locale_name, locale_name)
                self.locale_combo.append(locale_name, label)
                if locale_name == self.locale:
                    active = index
            self.locale_combo.set_active(active)
        finally:
            self.locale_combo.handler_unblock_by_func(self._on_locale_changed)

    def _rebuild_navigation(self):
        self._syncing_tree = True
        try:
            self.tree_store.clear()
            self._tree_paths = {}
            home = self.store.document("index")
            home_title = home.get("titles", {}).get(self.locale) or home.get("title") or _("Home")
            tree_iter = self.tree_store.append(None, [home_title, "index"])
            self._tree_paths["index"] = self.tree_store.get_path(tree_iter).to_string()

            def append_nodes(nodes, parent=None):
                for node in nodes:
                    label = self.store.label_for_node(node, self.locale)
                    canonical = node.get("canonical_id", "")
                    item_iter = self.tree_store.append(parent, [label, canonical])
                    if canonical:
                        self._tree_paths[canonical] = self.tree_store.get_path(item_iter).to_string()
                    append_nodes(node.get("items", []), item_iter)
            append_nodes(self.store.navigation())
            self.tree.expand_all()
            if self.current is not None:
                self._select_tree_document(self.current.canonical_id)
        finally:
            self._syncing_tree = False

    def _select_tree_document(self, canonical):
        path = self._tree_paths.get(canonical)
        if not path:
            return
        tree_path = Gtk.TreePath.new_from_string(path)
        self.tree.get_selection().select_path(tree_path)
        self.tree.scroll_to_cell(tree_path, None, False, 0.0, 0.0)

    def _rebuild_toc(self):
        for row in list(self.toc_list.get_children()):
            self.toc_list.remove(row)
        self._toc_rows = []
        headings = [
            heading for heading in self.markdown.get_headings()
            if 2 <= heading[0] <= 3]
        for level, title, anchor in headings:
            row = Gtk.ListBoxRow()
            row.anchor = anchor
            row.level = level
            label = Gtk.Label(label=title, xalign=0)
            label.set_line_wrap(True)
            label.set_margin_start(4 + (level - 2) * 12)
            label.set_margin_end(6)
            label.set_margin_top(4)
            label.set_margin_bottom(4)
            row.add(label)
            self.toc_list.add(row)
            self._toc_rows.append(row)
        self.toc_list.show_all()
        self._toc_available = bool(self._toc_rows)
        self.toc_button.set_sensitive(self._toc_available)
        self._sync_toc_visibility()
        self._on_document_scroll()

    def _sync_toc_visibility(self):
        if not hasattr(self, "toc_revealer"):
            return
        narrow = self.get_allocated_width() < TOC_THRESHOLD
        if not self._toc_available:
            visible = False
        elif self._toc_manual is None:
            visible = not narrow
        else:
            visible = self._toc_manual
        self.toc_revealer.set_reveal_child(visible)

    def _on_toc_row_activated(self, _listbox, row):
        anchor = getattr(row, "anchor", "")
        if not anchor:
            return
        if self.markdown.scroll_to_anchor(anchor):
            if self.history.current is not None:
                self.history.replace_current(anchor=anchor)
            self.toc_list.select_row(row)

    def _on_document_scroll(self, _adjustment=None):
        if not self._toc_rows:
            return
        adjustment = self.document_scroll.get_vadjustment()
        maximum = max(
            adjustment.get_lower(),
            adjustment.get_upper() - adjustment.get_page_size())
        if adjustment.get_value() >= maximum - 1.0:
            anchor = self._toc_rows[-1].anchor
        else:
            current = self.markdown.heading_at_y(
                adjustment.get_value() + self.markdown.get_top_margin() + 4,
                min_level=2, max_level=3)
            anchor = (
                current[2] if current is not None
                else self._toc_rows[0].anchor)
        for row in self._toc_rows:
            if row.anchor == anchor:
                if self.toc_list.get_selected_row() is not row:
                    self.toc_list.select_row(row)
                break

    def _current_scroll(self):
        return self.document_scroll.get_vadjustment().get_value()

    def _restore_position(self, anchor, scroll):
        def restore():
            if scroll is not None and float(scroll) > 0.0:
                adjustment = self.document_scroll.get_vadjustment()
                upper = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
                adjustment.set_value(min(float(scroll), upper))
            elif anchor:
                self.markdown.scroll_to_anchor(anchor)
            else:
                adjustment = self.document_scroll.get_vadjustment()
                adjustment.set_value(adjustment.get_lower())
            return False
        GLib.idle_add(restore)

    def _open_document(self, canonical, anchor="", add_history=True,
                       restore_scroll=None):
        if self.store is None:
            return False
        if self.current is not None and add_history:
            self.history.capture_scroll(self._current_scroll())
        try:
            content = self.store.open_document(canonical, self.locale)
        except DocumentError as error:
            self._show_transient_message(str(error), Gtk.MessageType.ERROR)
            return False
        self.current = content
        self.markdown.set_document(content.document)
        self._rebuild_toc()
        self._syncing_tree = True
        try:
            self._select_tree_document(content.canonical_id)
        finally:
            self._syncing_tree = False
        if content.fallback:
            self.fallback_label.set_text(_(
                "This page is not available in the selected language. The English version is shown."))
            self.fallback_label.show()
            self.fallback_bar.show()
        else:
            self.fallback_bar.hide()
        if add_history:
            self.history.visit(content.canonical_id, self.locale, anchor, 0.0)
        self._restore_position(anchor, restore_scroll)
        self._update_nav_buttons()
        return True

    def _show_transient_message(self, text, message_type=Gtk.MessageType.INFO):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=message_type,
            buttons=Gtk.ButtonsType.CLOSE, text=text)
        dialog.run()
        dialog.destroy()

    def _on_markdown_link(self, uri):
        if self.current is None:
            return True
        resolution = resolve_link(self.store, self.current.canonical_id, uri)
        if resolution.kind == "internal":
            self._open_document(
                resolution.canonical_id, anchor=resolution.anchor,
                add_history=True)
            return True
        if resolution.kind == "external":
            try:
                Gio.AppInfo.launch_default_for_uri(resolution.uri, None)
            except GLib.Error as error:
                self._show_transient_message(str(error), Gtk.MessageType.ERROR)
            return True
        self._show_transient_message(
            _("This link is blocked for safety."), Gtk.MessageType.WARNING)
        return True

    def _on_tree_selection(self, selection):
        if self._syncing_tree or self.store is None:
            return
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            return
        canonical = model.get_value(tree_iter, 1)
        if canonical and (self.current is None or canonical != self.current.canonical_id):
            self._open_document(canonical, add_history=True)

    def _on_back(self, _button=None):
        entry = self.history.back(self._current_scroll())
        if entry is None:
            return
        self.locale = self.store.select_locale(entry.locale)
        self._set_locale_combo(self.locale)
        self._open_document(
            entry.canonical_id, anchor=entry.anchor,
            add_history=False, restore_scroll=entry.scroll)

    def _on_forward(self, _button=None):
        entry = self.history.forward(self._current_scroll())
        if entry is None:
            return
        self.locale = self.store.select_locale(entry.locale)
        self._set_locale_combo(self.locale)
        self._open_document(
            entry.canonical_id, anchor=entry.anchor,
            add_history=False, restore_scroll=entry.scroll)

    def _on_home(self, _button=None):
        if self.store is not None:
            self._open_document("index", add_history=True)

    def _update_nav_buttons(self):
        if not hasattr(self, "back_button"):
            return
        self.back_button.set_sensitive(self.history.can_back)
        self.forward_button.set_sensitive(self.history.can_forward)

    def _set_locale_combo(self, locale_name):
        self.locale_combo.handler_block_by_func(self._on_locale_changed)
        try:
            self.locale_combo.set_active_id(locale_name)
        finally:
            self.locale_combo.handler_unblock_by_func(self._on_locale_changed)

    def _on_locale_changed(self, combo):
        locale_name = combo.get_active_id()
        if not locale_name or self.store is None or locale_name == getattr(self, "locale", None):
            return
        scroll = self._current_scroll()
        self.locale = self.store.select_locale(locale_name)
        self.preference.save(self.locale)
        canonical = self.current.canonical_id if self.current is not None else "index"
        if self.history.current is not None:
            self.history.replace_current(locale=self.locale, scroll=scroll)
        self._rebuild_navigation()
        self._open_document(canonical, add_history=False, restore_scroll=scroll)
        self._start_search_index()

    def _start_search_index(self):
        self.search_index = None
        self.search_worker = SearchWorker(self.store, self.locale)
        self.search_worker.start(self._on_search_ready)
        if self.search_entry.get_text().strip():
            self.search_message.set_text(_("Indexing local documentation…"))
            self.search_popover.show_all()

    def _on_search_ready(self, index, error):
        if error is not None:
            self.search_message.set_text(_("Search index could not be built."))
            return False
        if index is not self.search_worker.index:
            return False
        self.search_index = index
        self._update_search_results()
        return False

    def _clear_search_rows(self):
        for row in list(self.search_list.get_children()):
            self.search_list.remove(row)
        self._search_rows = []

    def _update_search_results(self):
        query = self.search_entry.get_text().strip()
        self._clear_search_rows()
        if not query:
            self.search_popover.popdown()
            return
        self.search_popover.show_all()
        if self.search_index is None:
            self.search_message.set_text(_("Indexing local documentation…"))
            self.search_message.show()
            return
        results = self.search_index.search(query)
        if not results:
            self.search_message.set_text(_("No results found."))
            self.search_message.show()
            return
        self.search_message.hide()
        for result in results:
            row = Gtk.ListBoxRow()
            row.result = result
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(7)
            box.set_margin_bottom(7)
            box.set_margin_start(10)
            box.set_margin_end(10)
            title = Gtk.Label(xalign=0)
            title.set_markup("<b>{}</b>".format(GLib.markup_escape_text(result.title)))
            section = Gtk.Label(label=result.section, xalign=0)
            section.get_style_context().add_class("row-meta")
            snippet = Gtk.Label(label=result.snippet, xalign=0)
            snippet.set_line_wrap(True)
            snippet.set_max_width_chars(48)
            box.pack_start(title, False, False, 0)
            box.pack_start(section, False, False, 0)
            box.pack_start(snippet, False, False, 0)
            row.add(box)
            self.search_list.add(row)
            self._search_rows.append(row)
        self.search_list.show_all()
        if self._search_rows:
            self.search_list.select_row(self._search_rows[0])

    def _on_search_changed(self, _entry):
        self._update_search_results()

    def _on_search_activate(self, _entry):
        row = self.search_list.get_selected_row()
        if row is None and self._search_rows:
            row = self._search_rows[0]
        if row is not None:
            self._open_search_row(row)

    def _on_search_row_activated(self, _listbox, row):
        self._open_search_row(row)

    def _open_search_row(self, row):
        result = getattr(row, "result", None)
        if result is None:
            return
        self.search_popover.popdown()
        self._open_document(
            result.canonical_id, anchor=result.anchor, add_history=True)

    def _update_sidebar_button_icon(self):
        icon_name = (
            "sidebar-hide-symbolic"
            if self.sidebar_revealer.get_reveal_child()
            else "sidebar-show-symbolic")
        self.sidebar_button.set_image(new_icon(
            icon_name, accessible_name=_("Show or hide contents")))

    def _on_sidebar_toggle(self, _button=None):
        visible = not self.sidebar_revealer.get_reveal_child()
        self.sidebar_revealer.set_reveal_child(visible)
        self._sidebar_manual = visible
        self._update_sidebar_button_icon()

    def _on_toc_toggle(self, _button=None):
        if not self._toc_available:
            return
        visible = not self.toc_revealer.get_reveal_child()
        self._toc_manual = visible
        self._sync_toc_visibility()

    def _on_size_allocate(self, _widget, allocation):
        narrow = allocation.width < SIDEBAR_THRESHOLD
        if narrow != self._last_narrow:
            self._last_narrow = narrow
            if narrow:
                self.sidebar_revealer.set_reveal_child(False)
            else:
                self.sidebar_revealer.set_reveal_child(
                    True if self._sidebar_manual is None else self._sidebar_manual)
            self._update_sidebar_button_icon()

        toc_narrow = allocation.width < TOC_THRESHOLD
        if toc_narrow != self._last_toc_narrow:
            self._last_toc_narrow = toc_narrow
            self._sync_toc_visibility()

    def _on_key_press(self, _window, event):
        state = event.state
        key = event.keyval
        if state & Gdk.ModifierType.MOD1_MASK:
            if key == Gdk.KEY_Left:
                self._on_back()
                return True
            if key == Gdk.KEY_Right:
                self._on_forward()
                return True
        if state & Gdk.ModifierType.CONTROL_MASK:
            if key in (Gdk.KEY_f, Gdk.KEY_F):
                self.search_entry.grab_focus()
                self.search_popover.show_all()
                return True
            if key == Gdk.KEY_Home:
                self.document_scroll.get_vadjustment().set_value(0.0)
                return True
        if key == Gdk.KEY_F1:
            self._on_home()
            return True
        if key == Gdk.KEY_Escape:
            if self.search_popover.get_visible():
                self.search_popover.popdown()
                self.search_entry.set_text("")
                return True
            if self._last_narrow and self.sidebar_revealer.get_reveal_child():
                self.sidebar_revealer.set_reveal_child(False)
                self._update_sidebar_button_icon()
                return True
        return False


class MiniOSHelpApplication(Gtk.Application):
    def __init__(self, docs_root=None, preference=None):
        Gtk.Application.__init__(self, application_id=APPLICATION_ID)
        self.docs_root = docs_root
        self.preference = preference
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        css = data_root() / "styles" / "style.css"
        apply_minios_css(str(css))

    def do_activate(self):
        if self.window is None:
            self.window = HelpWindow(
                self, docs_root=self.docs_root, preference=self.preference)
        self.window.show_all()
        if self.window.current is not None and not self.window.current.fallback:
            self.window.fallback_bar.hide()
        self.window.present()


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    application = MiniOSHelpApplication()
    return application.run(argv)
