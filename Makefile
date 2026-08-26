PYTHON ?= python3
NODE ?= node
MARKDOWN_COMPILER ?= ../minios-gui/tools/markdown-compiler.mjs
MARKDOWN_NODE_MODULES ?= ../minios-gui/tools/node_modules
PREFIX ?= /usr
BINDIR = $(PREFIX)/bin
LIBDIR = $(PREFIX)/lib/minios-help
DATADIR = $(PREFIX)/share/minios-help
APPLICATIONSDIR = $(PREFIX)/share/applications
METAINFO_DIR = $(PREFIX)/share/metainfo
LOCALEDIR = $(PREFIX)/share/locale
PO_FILES = $(wildcard po/*.po)

.PHONY: all test test-build-tools check install update-pot clean

all:

test:
	PYTHONPATH=lib$${PYTHONPATH:+:$$PYTHONPATH} xvfb-run -a $(PYTHON) -m unittest \
		tests.test_core tests.test_application -v

test-build-tools:
	@test -d $(MARKDOWN_NODE_MODULES) || { \
		echo "Node build tools are missing; run ../minios-gui/tools/npm-ci.sh" >&2; exit 1; \
	}
	$(NODE) --check $(MARKDOWN_COMPILER)
	MINIOS_MARKDOWN_COMPILER=$(MARKDOWN_COMPILER) \
		$(PYTHON) -m unittest tests.test_sync tests.test_compiler -v

check:
	$(PYTHON) -m py_compile bin/minios-help lib/minios_help/*.py \
		tools/sync_from_docs.py tests/*.py
	@for po in $(PO_FILES); do msgfmt --check --check-format -o /dev/null $$po; done
	@if command -v desktop-file-validate >/dev/null 2>&1; then \
		desktop-file-validate share/applications/dev.minios.Help.desktop; \
	fi
	$(MAKE) test

update-pot:
	xgettext --language=Python --from-code=UTF-8 --keyword=_ --sort-output \
		-o po/messages.pot lib/minios_help/application.py

install:
	install -Dm755 bin/minios-help $(DESTDIR)$(BINDIR)/minios-help
	install -d $(DESTDIR)$(LIBDIR)/minios_help
	install -m644 lib/minios_help/*.py $(DESTDIR)$(LIBDIR)/minios_help/
	install -Dm644 share/applications/dev.minios.Help.desktop \
		$(DESTDIR)$(APPLICATIONSDIR)/dev.minios.Help.desktop
	install -Dm644 share/metainfo/dev.minios.Help.metainfo.xml \
		$(DESTDIR)$(METAINFO_DIR)/dev.minios.Help.metainfo.xml
	install -d $(DESTDIR)$(DATADIR)/docs $(DESTDIR)$(DATADIR)/styles
	cp -a share/docs/. $(DESTDIR)$(DATADIR)/docs/
	install -m644 share/styles/style.css $(DESTDIR)$(DATADIR)/styles/style.css
	@for po in $(PO_FILES); do \
		lang=$${po##*/}; lang=$${lang%.po}; \
		install -d $(DESTDIR)$(LOCALEDIR)/$$lang/LC_MESSAGES; \
		msgfmt -o $(DESTDIR)$(LOCALEDIR)/$$lang/LC_MESSAGES/minios-help.mo $$po; \
	done

clean:
	rm -rf build
