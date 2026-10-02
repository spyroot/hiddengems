SHELL := /bin/bash
.DEFAULT_GOAL := bless

.PHONY: bless install-hooks help

bless:
	@./bless.sh --all

install-hooks:
	@git config --local core.hooksPath .githooks

help:
	@printf '%s\n' \
		'Targets:' \
		'  bless          Run package static checks (default).' \
		'  install-hooks  Configure the optional local Git hooks.' \
		'  help           Show this help.' \
		'' \
		'Run make bless inside the project environment. The same command can run in CI.'
