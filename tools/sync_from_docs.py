#!/usr/bin/env python3
"""Build the deterministic offline documentation bundle for MiniOS Help."""

from __future__ import absolute_import, print_function

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_NODE_COMPILER = (
    TOOLS_DIR.parent.parent / "minios-gui" / "tools" / "markdown-compiler.mjs")
NODE_COMPILER = Path(os.environ.get(
    "MINIOS_MARKDOWN_COMPILER", str(DEFAULT_NODE_COMPILER)))

DEFAULT_LOCALE = "en"
PRODUCT_KIND = "minios-help-documents"
SCHEMA_VERSION = 2
DOC_DIRS = ("about", "administration", "configuration", "development", "installation")
SAFE_EXTERNAL_SCHEMES = ("http", "https", "mailto")
LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\n]+)\)")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\n]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


class SyncError(Exception):
    pass


def _has_control(value):
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def validate_name(value, what="path"):
    if not value or "\x00" in value or _has_control(value):
        raise SyncError("invalid {}: {!r}".format(what, value))
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise SyncError("{} is not valid UTF-8: {!r}".format(what, value))


def read_utf8(path):
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise SyncError("invalid UTF-8 in {}: {}".format(path.name, error))


def read_json(path):
    try:
        return json.loads(read_utf8(path))
    except ValueError as error:
        raise SyncError("invalid JSON in {}: {}".format(path.name, error))


def canonical_id(relative_path):
    value = relative_path.replace("\\", "/")
    if value == "index.md":
        return "index"
    if not value.endswith(".md"):
        raise SyncError("document is not Markdown: {}".format(relative_path))
    return value[:-3]


def output_relpath(locale_name, canonical):
    source = "index.json" if canonical == "index" else canonical + ".json"
    return posixpath.join(locale_name, source)


def _reject_symlink(path, label):
    if path.is_symlink():
        raise SyncError("symbolic links are not allowed in {}: {}".format(label, path.name))


def collect_documents(root):
    """Return canonical-id -> source path without following symlinks."""
    root = Path(root)
    documents = {}
    folded = {}

    index_path = root / "index.md"
    if index_path.exists():
        _reject_symlink(index_path, "documentation")
        documents["index"] = index_path
        folded["index"] = "index"

    for dirname in DOC_DIRS:
        base = root / dirname
        if not base.exists():
            continue
        _reject_symlink(base, "documentation")
        for current, dirs, files in os.walk(str(base), followlinks=False):
            current_path = Path(current)
            for name in list(dirs):
                validate_name(name, "directory name")
                child = current_path / name
                if child.is_symlink():
                    raise SyncError("symbolic links are not allowed in documentation: {}".format(name))
            for name in files:
                validate_name(name, "file name")
                if not name.endswith(".md"):
                    continue
                path = current_path / name
                _reject_symlink(path, "documentation")
                rel = path.relative_to(root).as_posix()
                validate_name(rel, "relative path")
                canon = canonical_id(rel)
                key = canon.casefold()
                if key in folded and folded[key] != canon:
                    raise SyncError("conflicting canonical IDs: {} and {}".format(folded[key], canon))
                if canon in documents:
                    raise SyncError("duplicate canonical ID: {}".format(canon))
                folded[key] = canon
                documents[canon] = path
    return documents


def discover_locales(docs_root):
    translations = Path(docs_root) / "translations"
    locales = []
    if translations.exists():
        _reject_symlink(translations, "translations")
        for path in translations.iterdir():
            if not path.is_dir():
                continue
            _reject_symlink(path, "translations")
            validate_name(path.name, "locale")
            if not LOCALE_RE.match(path.name):
                raise SyncError("invalid locale directory: {}".format(path.name))
            locales.append(path.name)
    return sorted(set(locales + [DEFAULT_LOCALE]), key=lambda item: item.casefold())


def strip_frontmatter(text):
    if not text.startswith("---"):
        return text, ""
    lines = text.splitlines(True)
    if not lines or lines[0].strip() != "---":
        return text, ""
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[index + 1:]), "".join(lines[1:index])
    raise SyncError("unterminated YAML frontmatter")


def _unquote_yaml(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def parse_home_frontmatter(frontmatter):
    hero = {}
    features = []
    section = None
    current_feature = None
    for raw in frontmatter.splitlines():
        if raw.strip() == "hero:":
            section = "hero"
            current_feature = None
            continue
        if raw.strip() == "features:":
            section = "features"
            current_feature = None
            continue
        if section == "hero":
            match = re.match(r"^  (name|text|tagline):\s*(.*?)\s*$", raw)
            if match:
                hero[match.group(1)] = _unquote_yaml(match.group(2))
        elif section == "features":
            title = re.match(r"^  - title:\s*(.*?)\s*$", raw)
            details = re.match(r"^    details:\s*(.*?)\s*$", raw)
            if title:
                current_feature = {"title": _unquote_yaml(title.group(1)), "details": ""}
                features.append(current_feature)
            elif details and current_feature is not None:
                current_feature["details"] = _unquote_yaml(details.group(1))
    return hero, features


def safe_html_line(line):
    # Keep <br> inline: the shared compiler converts it to a line break.
    # Replacing it with a physical Markdown newline breaks table rows.
    line = re.sub(
        r"(?is)<kbd>(.*?)</kbd>",
        lambda match: "`{}`".format(match.group(1).replace("`", "")),
        line)
    line = re.sub(r"(?i)<(/?)strong\s*>", "**", line)
    line = re.sub(r"(?i)<(/?)em\s*>", "*", line)
    return line


def plain_heading_text(value):
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[*_~]", "", value)
    return value.strip()


def heading_slug(value):
    value = unicodedata.normalize("NFKC", plain_heading_text(value)).lower()
    chars = []
    for char in value:
        category = unicodedata.category(char)
        if category[0] in ("L", "N", "M") or char in ("-", "_"):
            chars.append(char)
        elif char.isspace():
            chars.append("-")
    slug = re.sub(r"-+", "-", "".join(chars)).strip("-")
    return slug or "section"


def anchor_match_key(value):
    normalized = unicodedata.normalize("NFKD", value or "").lower()
    return "".join(
        char for char in normalized
        if unicodedata.category(char)[0] != "M")


def headings_for_text(text):
    used = {}
    headings = []
    in_fence = False
    fence = None
    for line in text.splitlines():
        fence_match = FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence = token
            elif token == fence:
                in_fence = False
                fence = None
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", line)
        if not match:
            continue
        title = plain_heading_text(match.group(2))
        base = heading_slug(title)
        count = used.get(base, 0)
        used[base] = count + 1
        anchor = base if count == 0 else "{}-{}".format(base, count)
        headings.append((len(match.group(1)), title, anchor))
    return headings


def first_h1(text):
    for level, title, _anchor in headings_for_text(text):
        if level == 1:
            return title
    return ""


def _split_link_contents(contents):
    stripped = contents.strip()
    if stripped.startswith("<"):
        end = stripped.find(">")
        if end < 0:
            return stripped, ""
        return stripped[1:end], stripped[end + 1:]
    match = re.match(r"([^\s]+)(.*)$", stripped, re.DOTALL)
    if not match:
        return stripped, ""
    return match.group(1), match.group(2)


def _canonical_lookup(known):
    return {item.casefold(): item for item in known}


def resolve_internal_target(target, current, known):
    """Resolve a Markdown target to (kind, value, anchor)."""
    if not target or "\x00" in target or _has_control(target):
        raise SyncError("invalid link target in {}: {!r}".format(current, target))
    parsed = urlsplit(target)
    scheme = parsed.scheme.lower()
    if scheme:
        if scheme in SAFE_EXTERNAL_SCHEMES:
            return "external", target, ""
        raise SyncError("unsafe URI scheme in {}: {}".format(current, scheme))
    if target.startswith("//"):
        raise SyncError("scheme-relative URI is not allowed in {}".format(current))
    if parsed.query:
        raise SyncError("query strings are not supported in internal link {}".format(target))

    anchor = unquote(parsed.fragment or "")
    path = unquote(parsed.path or "")
    if "\\" in path:
        raise SyncError("backslashes are not allowed in internal link {}".format(target))
    if not path:
        return "internal", current, anchor
    if ".." in path.split("/"):
        raise SyncError("parent traversal is not allowed in internal link {}".format(target))
    if path.startswith("/docs/"):
        path = path[len("/docs/"):]
    elif path.startswith("/"):
        path = path[1:]
    elif path.startswith("translations/"):
        parts = path.split("/")
        if len(parts) >= 3:
            path = "/".join(parts[2:])
    else:
        base = "" if current == "index" else posixpath.dirname(current)
        path = posixpath.join(base, path)

    if path.endswith(".md"):
        path = path[:-3]
    path = posixpath.normpath(path)
    if path in ("", "."):
        path = "index"
    if path == ".." or path.startswith("../") or path.startswith("/"):
        raise SyncError("internal link escapes documentation root: {}".format(target))
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise SyncError("invalid internal path: {}".format(target))

    lookup = _canonical_lookup(known)
    resolved = lookup.get(path.casefold())
    if resolved is None:
        raise SyncError("broken internal link from {} to {}".format(current, target))
    return "internal", resolved, anchor


def normalize_links(text, current, known, link_records):
    def replace(match):
        label, contents = match.groups()
        target, suffix = _split_link_contents(contents)
        kind, value, anchor = resolve_internal_target(target, current, known)
        if kind == "external":
            return match.group(0)
        link_records.append({"canonical_id": value, "anchor": anchor})
        normalized = "/{}.md".format(value)
        if anchor:
            normalized += "#" + anchor
        return "[{}]({}{})".format(label, normalized, suffix)
    return LINK_RE.sub(replace, text)


def transform_document(text, current, known):
    body, _frontmatter = strip_frontmatter(text)
    output = []
    links = []
    image_found = False
    in_fence = False
    fence = None
    for line in body.splitlines(True):
        fence_match = FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence = token
            elif token == fence:
                in_fence = False
                fence = None
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue
        if IMAGE_RE.search(line):
            image_found = True
        line = safe_html_line(line)
        line = normalize_links(line, current, known, links)
        output.append(line)
    return "".join(output), links, image_found


def localized_messages(docs_root, locale_name):
    path = Path(docs_root) / ".vitepress" / "i18n" / (locale_name + ".json")
    if not path.exists() and locale_name != DEFAULT_LOCALE:
        path = Path(docs_root) / ".vitepress" / "i18n" / "en.json"
    data = read_json(path)
    messages = data.get("translations", {})
    if not isinstance(messages, dict):
        raise SyncError("i18n translations must be an object: {}".format(path.name))
    return messages


def localized_sidebar_text(node, locale_messages, english_messages):
    key = node.get("key")
    fallback = node.get("text", "")
    value = locale_messages.get(key)
    if not isinstance(value, str) or not value:
        value = english_messages.get(key, fallback)
    return value


def validate_sidebar(sidebar):
    keys = set()
    def walk(nodes):
        if not isinstance(nodes, list):
            raise SyncError("sidebar items must be an array")
        for node in nodes:
            if not isinstance(node, dict):
                raise SyncError("sidebar node must be an object")
            key = node.get("key")
            text = node.get("text")
            if not isinstance(key, str) or not key.startswith("sidebar."):
                raise SyncError("sidebar node is missing stable key: {!r}".format(text))
            if key in keys:
                raise SyncError("duplicate sidebar key: {}".format(key))
            keys.add(key)
            if not isinstance(text, str) or not text:
                raise SyncError("sidebar node has invalid text: {}".format(key))
            if "items" in node:
                walk(node["items"])
    walk(sidebar)


def sidebar_metadata(sidebar, known):
    meta = {"index": {"section": "Home", "section_key": "nav.home", "order": 0}}
    seen = set()
    order = [0]
    def walk(nodes, section, section_key):
        for node in nodes:
            current_section = section or node.get("text", "")
            current_key = section_key or node.get("key", "")
            if node.get("link"):
                kind, canon, anchor = resolve_internal_target(node["link"], "index", known)
                if kind != "internal" or anchor:
                    raise SyncError("sidebar link must point to a document: {}".format(node["link"]))
                if canon not in seen:
                    order[0] += 1
                    meta[canon] = {
                        "section": current_section,
                        "section_key": current_key,
                        "order": order[0],
                    }
                    seen.add(canon)
            walk(node.get("items", []), current_section, current_key)
    walk(sidebar, "", "")
    return meta


def navigation_manifest(sidebar, locales, messages_by_locale, known):
    english = messages_by_locale[DEFAULT_LOCALE]
    def build(nodes):
        result = []
        for node in nodes:
            item = {
                "key": node["key"],
                "labels": {
                    locale_name: localized_sidebar_text(
                        node, messages_by_locale[locale_name], english)
                    for locale_name in locales
                },
            }
            if node.get("link"):
                kind, canon, anchor = resolve_internal_target(node["link"], "index", known)
                if kind != "internal" or anchor:
                    raise SyncError("invalid sidebar document link: {}".format(node["link"]))
                item["canonical_id"] = canon
            children = build(node.get("items", []))
            if children:
                item["items"] = children
            result.append(item)
        return result
    return build(sidebar)


def home_navigation_lines(sidebar, locale_name, messages_by_locale, known, indent=""):
    lines = []
    english = messages_by_locale[DEFAULT_LOCALE]
    for node in sidebar:
        label = localized_sidebar_text(node, messages_by_locale[locale_name], english)
        link = ""
        if node.get("link"):
            _kind, canon, _anchor = resolve_internal_target(node["link"], "index", known)
            link = "(/{}.md)".format(canon)
            lines.append("{}- [{}]{}".format(indent, label, link))
        elif indent:
            lines.append("{}- **{}**".format(indent, label))
        else:
            lines.extend(["## {}".format(label), ""])
        children = node.get("items", [])
        if children:
            lines.extend(home_navigation_lines(
                children, locale_name, messages_by_locale, known,
                indent + ("  " if indent else "")))
        if not indent and lines and lines[-1] != "":
            lines.append("")
    return lines


def build_home(source_text, sidebar, locale_name, messages_by_locale, known):
    _body, frontmatter = strip_frontmatter(source_text)
    hero, features = parse_home_frontmatter(frontmatter)
    name = hero.get("name") or "MiniOS"
    text = hero.get("text") or "MiniOS documentation"
    tagline = hero.get("tagline") or ""
    lines = ["# {}".format(name), "", "**{}**".format(text)]
    if tagline:
        lines.extend(["", tagline])
    if features:
        lines.append("")
        for feature in features:
            if feature.get("title"):
                details = feature.get("details", "")
                suffix = ": " + details if details else ""
                lines.append("- **{}**{}".format(feature["title"], suffix))
    lines.append("")
    lines.extend(home_navigation_lines(
        sidebar, locale_name, messages_by_locale, known))
    return "\n".join(lines).rstrip() + "\n"


def remap_localized_anchors(generated, link_records, locales, known):
    english_headings = {
        canon: headings_for_text(generated[DEFAULT_LOCALE][canon])
        for canon in known
    }
    for locale_name in locales:
        if locale_name == DEFAULT_LOCALE:
            continue
        alias_maps = {}
        local_sets = {}
        local_match = {}
        for canon in known:
            local = headings_for_text(generated[locale_name][canon])
            local_sets[canon] = {item[2] for item in local}
            local_match[canon] = {
                anchor_match_key(item[2]): item[2] for item in local
            }
            aliases = {}
            for source_heading, localized_heading in zip(english_headings[canon], local):
                aliases[anchor_match_key(source_heading[2])] = localized_heading[2]
            alias_maps[canon] = aliases
        for source in known:
            text = generated[locale_name][source]
            for record in link_records[locale_name][source]:
                anchor = record.get("anchor") or ""
                if not anchor:
                    continue
                target = record["canonical_id"]
                lowered = anchor.lower()
                if lowered in local_sets[target]:
                    continue
                match_key = anchor_match_key(anchor)
                replacement = local_match[target].get(match_key)
                if not replacement:
                    replacement = alias_maps[target].get(match_key)
                if not replacement:
                    raise SyncError(
                        "broken anchor in {} locale: {}#{}".format(
                            locale_name, target, anchor))
                old_target = "/{}.md#{}".format(target, anchor)
                new_target = "/{}.md#{}".format(target, replacement)
                text = text.replace(old_target, new_target)
                record["anchor"] = replacement
            generated[locale_name][source] = text


def _validate_anchor_records(records, rendered_by_canonical, locale_name):
    for record in records:
        anchor = record.get("anchor") or ""
        if not anchor:
            continue
        target = record["canonical_id"]
        target_text = rendered_by_canonical.get(target)
        if target_text is None:
            continue
        anchors = {item[2] for item in headings_for_text(target_text)}
        if anchor.lower() not in anchors:
            raise SyncError(
                "broken anchor in {} locale: {}#{}".format(locale_name, target, anchor))


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_generated_tree(root, manifest):
    root = Path(root).resolve()
    for document in manifest["documents"]:
        hashes = document.get("sha256", {})
        for locale_name, rel in document.get("translations", {}).items():
            validate_name(rel, "manifest path")
            candidate = root / Path(rel)
            if candidate.is_symlink():
                raise SyncError("generated document is a symlink: {}".format(rel))
            resolved = candidate.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                raise SyncError("manifest path escapes output root: {}".format(rel))
            if not resolved.is_file():
                raise SyncError("manifest points to missing document: {}".format(rel))
            actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
            if hashes.get(locale_name) != actual:
                raise SyncError("manifest hash mismatch for {}".format(rel))
    for rel, expected in manifest.get("assets", {}).items():
        validate_name(rel, "asset path")
        candidate = root / Path(rel)
        if candidate.is_symlink():
            raise SyncError("generated asset is a symlink: {}".format(rel))
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise SyncError("asset path escapes output root: {}".format(rel))
        if not resolved.is_file():
            raise SyncError("manifest points to missing asset: {}".format(rel))
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual != expected:
            raise SyncError("manifest hash mismatch for {}".format(rel))


def compile_documents(docs_root, temp_root, generated, source_paths,
                      locales, known, node_command=None, mermaid_command=None):
    items = []
    lookup = {}
    for locale_name in locales:
        for canon in known:
            item_id = str(len(items))
            lookup[item_id] = (locale_name, canon)
            items.append({
                "id": item_id,
                "text": generated[locale_name][canon],
                "source_path": str(source_paths[locale_name][canon]),
                "output_path": output_relpath(locale_name, canon),
            })

    request = {
        "schema_version": 1,
        "docs_root": str(docs_root),
        "output_root": str(temp_root),
        "mermaid_command": mermaid_command,
        "items": items,
    }
    batch_path = temp_root / ".compile-batch.json"
    batch_path.write_text(
        json.dumps(request, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")
    command = [
        node_command or os.environ.get("MINIOS_NODE", "node"),
        str(NODE_COMPILER), str(batch_path),
    ]
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SyncError("Node Markdown compiler failed: {}".format(error))
    finally:
        try:
            batch_path.unlink()
        except OSError:
            pass
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown error").strip()
        if "ERR_MODULE_NOT_FOUND" in message:
            message += " (run ../minios-gui/tools/npm-ci.sh first)"
        raise SyncError("Node Markdown compiler failed: {}".format(message))
    try:
        response = json.loads(result.stdout)
    except ValueError as error:
        raise SyncError("Node Markdown compiler returned invalid JSON: {}".format(error))
    if response.get("schema_version") != 1:
        raise SyncError("unsupported Node compiler response")

    compiled = {locale_name: {} for locale_name in locales}
    seen = set()
    for item in response.get("items", []):
        item_id = item.get("id") if isinstance(item, dict) else None
        if item_id not in lookup or item_id in seen:
            raise SyncError("invalid item in Node compiler response")
        seen.add(item_id)
        locale_name, canon = lookup[item_id]
        digest = item.get("sha256")
        headings = item.get("headings")
        if not isinstance(digest, str) or len(digest) != 64 or not isinstance(headings, list):
            raise SyncError("invalid metadata in Node compiler response")
        compiled[locale_name][canon] = {"sha256": digest, "headings": headings}
    if seen != set(lookup):
        raise SyncError("Node compiler response is incomplete")
    assets = response.get("assets")
    if not isinstance(assets, dict):
        raise SyncError("Node compiler returned invalid asset metadata")
    return compiled, assets


def sync_docs(docs_root, output_root, stderr=None, node_command=None,
              mermaid_command=None):
    docs_root = Path(docs_root).resolve()
    output_root = Path(output_root)
    stderr = stderr or sys.stderr
    if not docs_root.is_dir():
        raise SyncError("documentation root does not exist: {}".format(docs_root))

    sidebar_path = docs_root / ".vitepress" / "sidebar.json"
    if not sidebar_path.is_file():
        raise SyncError("missing .vitepress/sidebar.json")
    sidebar = read_json(sidebar_path)
    validate_sidebar(sidebar)

    locales = discover_locales(docs_root)
    english_sources = collect_documents(docs_root)
    if "index" not in english_sources:
        raise SyncError("missing English index.md")
    known = sorted(english_sources)
    known_folded = {}
    for canon in known:
        folded = canon.casefold()
        if folded in known_folded and known_folded[folded] != canon:
            raise SyncError("conflicting canonical IDs: {} and {}".format(known_folded[folded], canon))
        known_folded[folded] = canon

    sources_by_locale = {DEFAULT_LOCALE: english_sources}
    for locale_name in locales:
        if locale_name == DEFAULT_LOCALE:
            continue
        locale_root = docs_root / "translations" / locale_name
        localized = collect_documents(locale_root)
        for canon in localized:
            if canon.casefold() not in known_folded:
                raise SyncError("translation has no English source: {} ({})".format(canon, locale_name))
        sources_by_locale[locale_name] = localized

    messages_by_locale = {}
    for locale_name in locales:
        messages_by_locale[locale_name] = localized_messages(docs_root, locale_name)
    metadata = sidebar_metadata(sidebar, known)
    for canon in known:
        if canon != "index" and canon not in metadata:
            raise SyncError("document is missing from sidebar: {}".format(canon))

    parent = output_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=".minios-help-docs-", dir=str(parent)))
    generated = {locale_name: {} for locale_name in locales}
    link_records = {locale_name: {} for locale_name in locales}
    source_paths = {locale_name: {} for locale_name in locales}
    source_present = {
        locale_name: set(sources_by_locale.get(locale_name, {}))
        for locale_name in locales
    }

    try:
        for locale_name in locales:
            localized_sources = sources_by_locale.get(locale_name, {})
            for canon in known:
                if canon == "index":
                    source = localized_sources.get("index") or english_sources["index"]
                    text = build_home(
                        read_utf8(source), sidebar, locale_name,
                        messages_by_locale, known)
                    records = []
                    text = normalize_links(text, canon, known, records)
                else:
                    source = localized_sources.get(canon) or english_sources[canon]
                    text, records, _has_image = transform_document(
                        read_utf8(source), canon, known)
                generated[locale_name][canon] = text
                link_records[locale_name][canon] = records
                source_paths[locale_name][canon] = source

        remap_localized_anchors(generated, link_records, locales, known)
        for locale_name in locales:
            for canon in known:
                _validate_anchor_records(
                    link_records[locale_name][canon], generated[locale_name], locale_name)
        compiled, assets = compile_documents(
            docs_root, temp_root, generated, source_paths, locales, known,
            node_command=node_command,
            mermaid_command=(mermaid_command or
                             os.environ.get("MINIOS_MERMAID_COMMAND")))

        documents = []
        for canon in sorted(known, key=lambda value: (metadata[value]["order"], value.casefold())):
            translations = {}
            hashes = {}
            titles = {}
            fallback_locales = []
            for locale_name in locales:
                rel = output_relpath(locale_name, canon)
                translations[locale_name] = rel
                hashes[locale_name] = compiled[locale_name][canon]["sha256"]
                headings = compiled[locale_name][canon]["headings"]
                titles[locale_name] = next(
                    (item.get("title", "") for item in headings
                     if item.get("level") == 1),
                    headings[0].get("title", "") if headings else "")
                if locale_name != DEFAULT_LOCALE and canon not in source_present[locale_name]:
                    fallback_locales.append(locale_name)
            documents.append({
                "canonical_id": canon,
                "path": translations[DEFAULT_LOCALE],
                "section": metadata[canon]["section"],
                "section_key": metadata[canon]["section_key"],
                "order": metadata[canon]["order"],
                "title": titles[DEFAULT_LOCALE],
                "titles": titles,
                "available_translations": [
                    locale_name for locale_name in locales
                    if locale_name == DEFAULT_LOCALE or
                    canon in source_present[locale_name]],
                "translations": translations,
                "sha256": hashes,
                "internal_links": link_records[DEFAULT_LOCALE][canon],
                "fallback_locales": fallback_locales,
            })

        manifest = {
            "product_kind": PRODUCT_KIND,
            "schema_version": SCHEMA_VERSION,
            "default_locale": DEFAULT_LOCALE,
            "locales": locales,
            "navigation": navigation_manifest(
                sidebar, locales, messages_by_locale, known),
            "documents": documents,
            "assets": dict(sorted(assets.items())),
        }
        manifest_path = temp_root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        verify_generated_tree(temp_root, manifest)

        backup = None
        if output_root.exists():
            backup = Path(tempfile.mkdtemp(prefix=".minios-help-old-", dir=str(parent)))
            backup.rmdir()
            os.rename(str(output_root), str(backup))
        try:
            os.rename(str(temp_root), str(output_root))
        except Exception:
            if backup is not None and backup.exists() and not output_root.exists():
                os.rename(str(backup), str(output_root))
            raise
        if backup is not None and backup.exists():
            shutil.rmtree(str(backup))
        return manifest
    except Exception:
        if temp_root.exists():
            shutil.rmtree(str(temp_root), ignore_errors=True)
        raise


def default_paths():
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root.parent / "docs", repo_root / "share" / "docs"


def main(argv=None):
    default_docs, default_output = default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-root", default=str(default_docs))
    parser.add_argument("--output-root", default=str(default_output))
    parser.add_argument(
        "--node-command", default=os.environ.get("MINIOS_NODE", "node"))
    parser.add_argument(
        "--mermaid-command", default=os.environ.get("MINIOS_MERMAID_COMMAND"))
    args = parser.parse_args(argv)
    try:
        manifest = sync_docs(
            args.docs_root, args.output_root,
            node_command=args.node_command,
            mermaid_command=args.mermaid_command)
    except SyncError as error:
        print("sync_from_docs.py: error: {}".format(error), file=sys.stderr)
        return 1
    print(
        "Synchronized {} documents in {} locales".format(
            len(manifest["documents"]), len(manifest["locales"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
