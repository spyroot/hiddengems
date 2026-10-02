#!/usr/bin/env bash
[[ "${CI_EXIT_CODES_LOADED:-0}" == 1 ]] && return 0
readonly CI_EXIT_CODES_LOADED=1

# shellcheck disable=SC2034 # Consumed by sourcing entrypoints.
readonly CI_EXIT_OK=0 CI_EXIT_BLOCKED=2 CI_EXIT_USAGE=64 \
	CI_EXIT_MISSING_FILE=66 CI_EXIT_SIGNAL_INT=130 CI_EXIT_SIGNAL_TERM=143
