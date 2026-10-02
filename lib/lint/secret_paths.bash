#!/usr/bin/env bash
[[ "${CI_SECRET_PATHS_LOADED:-0}" == 1 ]] && return 0
readonly CI_SECRET_PATHS_LOADED=1

# shellcheck source=lib/core/exit_codes.bash
source "$(dirname -- "${BASH_SOURCE[0]}")/../core/exit_codes.bash"

ci_block_secret_commit_message() {
	(($# == 1)) || return "${CI_EXIT_USAGE}"
	[[ -r "$1" ]] || return "${CI_EXIT_MISSING_FILE}"
	local pattern
	pattern='(^|[^[:alnum:]_.])(\.internal|\.codex|\.claude|\.coordination|'
	pattern+='agents\.md|claude\.md|codex[_-](handout|handoff)\.md|'
	pattern+='team_guide\.md)($|[^[:alnum:]_.])'
	if LC_ALL=C grep -Eiq "${pattern}" "$1"; then
		printf '%s\n' 'BLOCKER: commit message names a local agent artifact.' >&2
		return "${CI_EXIT_BLOCKED}"
	fi
	printf '%s\n' 'Secret commit message: clean'
}

ci_block_staged_secret_paths() {
	(($# == 1)) || return "${CI_EXIT_USAGE}"
	[[ -r "$1" ]] || return "${CI_EXIT_MISSING_FILE}"
	local path
	while IFS= read -r -d '' path; do
		case "/${path}/:${path##*/}" in
		*/.internal/*:* | */.codex/*:* | */.claude/*:* | \
			*/.coordination/*:* | */.agents/*:* | */.gemini/*:* | \
			*:AGENTS.md | *:CLAUDE*.md | *:CODEX*.md | \
			*:TEAM_GUIDE.md | *:GEMINI.md | *:.mcp.json)
			printf '%s\n' 'BLOCKER: a local agent artifact is staged.' >&2
			return "${CI_EXIT_BLOCKED}"
			;;
		esac
	done <"$1"
	printf '%s\n' 'Secret paths: clean'
}
