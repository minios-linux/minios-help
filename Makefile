PYTHON ?= python3
PREFIX ?= /usr
BINDIR = $(PREFIX)/bin
LIBDIR = $(PREFIX)/lib/minios-help
DATADIR = $(PREFIX)/share/minios-help
APPLICATIONSDIR = $(PREFIX)/share/applications
METAINFO_DIR = $(PREFIX)/share/metainfo
LOCALEDIR = $(PREFIX)/share/locale
ICONDIR = $(PREFIX)/share/icons/hicolor/scalable/apps
PO_FILES = $(wildcard po/*.po)

.PHONY: all test check install update-pot clean

all:

test:
	PYTHONPATH=lib$${PYTHONPATH:+:$$PYTHONPATH} xvfb-run -a $(PYTHON) -m unittest discover -s tests -v

check:
	$(PYTHON) -m py_compile bin/minios-help lib/minios_help/*.py tools/sync_from_docs.py tests/*.py
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
	install -d $(DESTDIR)$(ICONDIR)
	ln -sfn ../../../elementary-minios/categories/128/system-help.svg \
		$(DESTDIR)$(ICONDIR)/dev.minios.Help.svg
	@for po in $(PO_FILES); do \
		lang=$${po##*/}; lang=$${lang%.po}; \
		install -d $(DESTDIR)$(LOCALEDIR)/$$lang/LC_MESSAGES; \
		msgfmt -o $(DESTDIR)$(LOCALEDIR)/$$lang/LC_MESSAGES/minios-help.mo $$po; \
	done

clean:
	rm -rf build
