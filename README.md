# MiniOS Help

MiniOS Help is the native GTK 3 offline documentation viewer for MiniOS.
It uses `minios_gui.MarkdownTextView`; there is no WebKit, JavaScript runtime,
Node.js dependency, or runtime documentation download.

The editable documentation source remains the sibling `../docs` repository.
Files under `share/docs/` are generated release inputs and must not be edited by
hand. Refresh them with:

```sh
tools/sync-from-docs.sh
```

The synchronizer validates the sidebar contract, UTF-8 input, internal links,
anchors, path containment and generated manifest before atomically replacing the
bundle. A Debian source build consumes the already generated bundle and never
requires a sibling documentation checkout.

## Development

```sh
make check
PYTHONPATH=lib bin/minios-help
```

The application supports Python 3.6 and GTK 3.22. Optional native Mermaid
flowcharts are provided by the reusable `minios-gui` Markdown API.
