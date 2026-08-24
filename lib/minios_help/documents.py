"""Validated manifest access and locale selection for MiniOS Help."""

from __future__ import absolute_import

import hashlib
import json
import os
import tempfile
from collections import namedtuple
from pathlib import Path, PurePosixPath

PRODUCT_KIND = "minios-help-documents"
SCHEMA_VERSION = 1
DEFAULT_LOCALE = "en"
DocumentContent = namedtuple(
    "DocumentContent", "canonical_id requested_locale locale fallback text metadata")


class DocumentError(Exception):
    pass


def _has_control(value):
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def normalize_locale(value):
    if not value:
        return ""
    value = str(value).strip().split(".", 1)[0].split("@", 1)[0]
    if not value or _has_control(value):
        return ""
    value = value.replace("_", "-")
    parts = value.split("-")
    language = parts[0].lower()
    if not language.isalpha() or len(language) not in (2, 3):
        return ""
    if len(parts) == 1:
        return language
    region = parts[1]
    if not region.isalnum():
        return language
    region = region.upper() if len(region) <= 3 else region
    return language + "-" + region


def locale_candidates(explicit=None, environ=None):
    environ = os.environ if environ is None else environ
    values = []
    if explicit:
        values.append(explicit)
    else:
        values.extend(
            item for item in environ.get("LANGUAGE", "").split(":") if item)
        values.extend(environ.get(name, "") for name in (
            "LC_ALL", "LC_MESSAGES", "LANG"))
    values.append(DEFAULT_LOCALE)
    candidates = []
    for value in values:
        normalized = normalize_locale(value)
        if not normalized:
            continue
        for item in (normalized, normalized.split("-", 1)[0]):
            if item not in candidates:
                candidates.append(item)
    return candidates


def _validate_relative_path(value):
    if not isinstance(value, str) or not value or "\x00" in value or _has_control(value):
        raise DocumentError("invalid document path in manifest")
    if "\\" in value:
        raise DocumentError("backslashes are not allowed in document paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise DocumentError("document path escapes documentation root: {}".format(value))
    return path


def _validate_canonical_id(value):
    if not isinstance(value, str) or not value or "\x00" in value or _has_control(value):
        raise DocumentError("invalid canonical document ID")
    if "\\" in value or value.startswith("/"):
        raise DocumentError("invalid canonical document ID: {}".format(value))
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise DocumentError("invalid canonical document ID: {}".format(value))
    return value


class DocumentStore(object):
    def __init__(self, docs_root):
        self.docs_root = Path(docs_root)
        if self.docs_root.is_symlink():
            raise DocumentError("symbolic documentation roots are not allowed")
        self.manifest = self._load_manifest()
        self.locales = tuple(self.manifest["locales"])
        self.default_locale = self.manifest.get("default_locale", DEFAULT_LOCALE)
        self._locale_lookup = {item.casefold(): item for item in self.locales}
        self._documents = {}
        self._folded = {}
        self._validate_documents()
        self._validate_navigation()

    def _load_manifest(self):
        path = self.docs_root / "manifest.json"
        if path.is_symlink():
            raise DocumentError("symbolic documentation manifests are not allowed")
        if not path.is_file():
            raise DocumentError("MiniOS Help documentation is not installed")
        try:
            data = json.loads(path.read_bytes().decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise DocumentError("MiniOS Help manifest is invalid: {}".format(error))
        if data.get("product_kind") != PRODUCT_KIND:
            raise DocumentError("unexpected documentation manifest type")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise DocumentError("unsupported documentation manifest version")
        locales = data.get("locales")
        if (not isinstance(locales, list) or DEFAULT_LOCALE not in locales or
                len(locales) != len(set(locales))):
            raise DocumentError("documentation manifest has invalid locales")
        if data.get("default_locale") != DEFAULT_LOCALE:
            raise DocumentError("documentation manifest has invalid default locale")
        if not isinstance(data.get("documents"), list):
            raise DocumentError("documentation manifest has no document list")
        if not isinstance(data.get("navigation"), list):
            raise DocumentError("documentation manifest has no navigation tree")
        return data

    def _validate_documents(self):
        for item in self.manifest["documents"]:
            if not isinstance(item, dict):
                raise DocumentError("invalid document record")
            canon = _validate_canonical_id(item.get("canonical_id"))
            if canon in self._documents or canon.casefold() in self._folded:
                raise DocumentError("duplicate canonical document ID: {}".format(canon))
            translations = item.get("translations")
            if not isinstance(translations, dict) or DEFAULT_LOCALE not in translations:
                raise DocumentError("document has no English source: {}".format(canon))
            for locale_name, rel in translations.items():
                if locale_name not in self.locales:
                    raise DocumentError("unknown locale in document record: {}".format(locale_name))
                _validate_relative_path(rel)
            self._documents[canon] = item
            self._folded[canon.casefold()] = canon

    def _validate_navigation(self):
        seen_keys = set()
        seen_documents = set()

        def walk(nodes):
            if not isinstance(nodes, list):
                raise DocumentError("invalid navigation items in manifest")
            for node in nodes:
                if not isinstance(node, dict):
                    raise DocumentError("invalid navigation record in manifest")
                key = node.get("key")
                if (not isinstance(key, str) or not key or
                        "\x00" in key or _has_control(key)):
                    raise DocumentError("invalid navigation key in manifest")
                if key in seen_keys:
                    raise DocumentError("duplicate navigation key: {}".format(key))
                seen_keys.add(key)
                labels = node.get("labels")
                if (not isinstance(labels, dict) or
                        not isinstance(labels.get(DEFAULT_LOCALE), str) or
                        not labels.get(DEFAULT_LOCALE)):
                    raise DocumentError("navigation item has no English label: {}".format(key))
                for locale_name, label in labels.items():
                    if locale_name not in self.locales or not isinstance(label, str):
                        raise DocumentError("invalid navigation label: {}".format(key))
                canonical = node.get("canonical_id")
                if canonical:
                    resolved = self.resolve_canonical(canonical)
                    if resolved is None:
                        raise DocumentError("navigation references unknown document: {}".format(canonical))
                    if resolved in seen_documents:
                        raise DocumentError("duplicate navigation document: {}".format(resolved))
                    seen_documents.add(resolved)
                walk(node.get("items", []))

        walk(self.navigation())
        expected = set(self._documents) - {"index"}
        if seen_documents != expected:
            missing = sorted(expected - seen_documents)
            raise DocumentError(
                "navigation does not cover installed documents: {}".format(
                    ", ".join(missing) if missing else "duplicate entries"))

    def resolve_canonical(self, value):
        if not isinstance(value, str):
            return None
        return self._folded.get(value.casefold())

    def document(self, canonical):
        resolved = self.resolve_canonical(canonical)
        if resolved is None:
            raise DocumentError("unknown document: {}".format(canonical))
        return self._documents[resolved]

    def all_documents(self):
        return sorted(
            self._documents.values(),
            key=lambda item: (item.get("order", 0), item["canonical_id"].casefold()))

    def select_locale(self, explicit=None, environ=None):
        for candidate in locale_candidates(explicit, environ=environ):
            exact = self._locale_lookup.get(candidate.casefold())
            if exact:
                return exact
        return self.default_locale

    def _safe_file(self, relative):
        path = _validate_relative_path(relative)
        root = self.docs_root.resolve()
        candidate = self.docs_root.joinpath(*path.parts)
        current = self.docs_root
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise DocumentError("symbolic links are not allowed in installed documentation")
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            raise DocumentError("document path escapes installed documentation")
        if not resolved.is_file():
            raise DocumentError("installed document is missing: {}".format(relative))
        return resolved

    def open_document(self, canonical, locale_name=None):
        item = self.document(canonical)
        requested = self.select_locale(locale_name)
        fallback_locales = item.get("fallback_locales", [])
        fallback = requested in fallback_locales
        actual = self.default_locale if fallback else requested
        translations = item["translations"]
        relative = translations.get(actual) or translations[self.default_locale]
        path = self._safe_file(relative)
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DocumentError("installed document is not UTF-8: {}".format(error))
        expected = item.get("sha256", {}).get(actual)
        if not isinstance(expected, str) or len(expected) != 64:
            raise DocumentError("document checksum is missing: {}".format(canonical))
        if hashlib.sha256(raw).hexdigest() != expected.lower():
            raise DocumentError("installed document checksum does not match: {}".format(canonical))
        return DocumentContent(
            item["canonical_id"], requested, actual, fallback, text, item)

    def navigation(self):
        return self.manifest["navigation"]

    def label_for_node(self, node, locale_name):
        locale_name = self.select_locale(locale_name)
        labels = node.get("labels", {})
        return labels.get(locale_name) or labels.get(self.default_locale) or node.get("key", "")

    def section_label(self, item, locale_name):
        key = item.get("section_key")
        if not key:
            return item.get("section", "")
        found = self._find_navigation_key(self.navigation(), key)
        if found is None:
            return item.get("section", "")
        return self.label_for_node(found, locale_name)

    def _find_navigation_key(self, nodes, key):
        for node in nodes:
            if node.get("key") == key:
                return node
            found = self._find_navigation_key(node.get("items", []), key)
            if found is not None:
                return found
        return None


class LocalePreference(object):
    """Tiny XDG-backed preference store; never writes beside installed docs."""
    def __init__(self, path=None):
        if path is None:
            config = os.environ.get("XDG_CONFIG_HOME")
            if not config:
                config = os.path.join(os.path.expanduser("~"), ".config")
            path = os.path.join(config, "minios-help", "settings.json")
        self.path = Path(path)

    def load(self):
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        value = data.get("locale") if isinstance(data, dict) else None
        return normalize_locale(value) or None

    def save(self, locale_name):
        value = normalize_locale(locale_name)
        if not value:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".settings-", dir=str(self.path.parent), text=True)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({"locale": value}, handle, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, str(self.path))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True
