# Build the C client, install the client family, and run offline regressions.
PREFIX ?= /usr/local
PYTHON ?= python3

.PHONY: all check check-scripts install install-scripts clean

all:
	$(MAKE) -C c

check:
	$(MAKE) -C c check
	$(MAKE) check-scripts

check-scripts:
	bash -n nonc/ttychatter.bash
	bash -n nonc/ttychatter.python
	$(PYTHON) -B -m unittest discover -s tests -v
	$(PYTHON) -B -m unittest discover -s mailbot/tests -v

install:
	$(MAKE) -C c install
	$(MAKE) install-scripts

install-scripts:
	install -d "$(DESTDIR)$(PREFIX)/bin" "$(DESTDIR)$(PREFIX)/share/man/man1"
	install -m 0755 nonc/ttychatter.python nonc/ttychatter.bash nonc/ttychatter.ncurses mailbot/ttychatter.mailbot "$(DESTDIR)$(PREFIX)/bin/"
	install -m 0644 nonc/ttychatter.python.1 nonc/ttychatter.bash.1 nonc/ttychatter.ncurses.1 mailbot/ttychatter.mailbot.1 "$(DESTDIR)$(PREFIX)/share/man/man1/"

clean:
	$(MAKE) -C c clean
