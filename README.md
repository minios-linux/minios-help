# MiniOS Help

MiniOS Help is the native GTK 3 offline documentation viewer for MiniOS. The
installed application does not parse Markdown and does not use WebKit, HTML,
JavaScript, Node.js, or a Mermaid runtime. It renders a precompiled MiniOS
markup document tree through `minios_gui.DocumentTextView`.

The editable documentation source remains the sibling `../docs` repository.
Files under `share/docs/` are generated release inputs and must not be edited by
hand. Markdown is parsed during documentation synchronization with the original
Node.js `markdown-it` and pinned plugins; Mermaid fenced blocks are rendered to
SVG by the official Mermaid CLI. The resulting JSON documents and SVG/image
assets are the only documentation formats shipped in the binary package.

A Debian source build consumes the already generated `share/docs/` bundle. It
therefore does not need Node.js, npm, Mermaid, Chromium, or a sibling
documentation checkout.

## Refreshing the documentation

Install the shared pinned Node.js build tools from the sibling `minios-gui` repository:

```sh
../minios-gui/tools/npm-ci.sh
```

This keeps `node_modules`, the npm cache and the Puppeteer browser cache inside
the repository's ignored paths; nothing is installed globally. Then refresh the
compiled documentation bundle with:

```sh
tools/sync-from-docs.sh
```

The shared `minios-gui` build toolchain uses Node.js 20 or newer and includes
`@mermaid-js/mermaid-cli@11.16.0`. `--mermaid-command /path/to/mmdc` remains
available when an alternate Mermaid CLI executable is required.

The synchronizer validates the sidebar contract, UTF-8 input, internal links,
anchors, path containment, compiled document checksums and asset checksums
before atomically replacing the bundle. Browser-only Mermaid CSS animations,
drop shadows and custom properties are stripped from the static SVG; output
containing scripts or `foreignObject` is rejected. This keeps the assets
renderable by the native GTK SVG loader, including librsvg 2.40 in Ubuntu 18.04.

## Development

Runtime/package tests do not need Node.js build dependencies:

```sh
make check
```

After `../minios-gui/tools/npm-ci.sh`, compiler and synchronizer tests run with:

```sh
make test-build-tools
```

When developing `minios-help` and `minios-gui` side by side without installing
the latter, run runtime tests with the sibling source on `PYTHONPATH`.

The runtime application is kept compatible with Python 3.6 and GTK 3.22. The
build-time Markdown parser and Mermaid renderer require Node.js 20 or newer;
the Python synchronizer itself uses only the standard library.
