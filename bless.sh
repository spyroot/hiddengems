#!/usr/bin/env bash
set -Eeuo pipefail

CI_ROOT="$({
	cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
	pwd -P
})"
readonly CI_ROOT
CI_INVENTORY=

# shellcheck source=lib/core/exit_codes.bash
source "${CI_ROOT}/lib/core/exit_codes.bash"
# shellcheck source=lib/lint/secret_paths.bash
source "${CI_ROOT}/lib/lint/secret_paths.bash"

usage() {
	printf '%s\n' \
		'Usage: ./bless.sh [--all | --staged] [--dry-run]' \
		'       ./bless.sh --help' \
		'' \
		'Run Python, Markdown, YAML, and shell static checks.' \
		'--all      Check tracked and untracked package files (default).' \
		'--staged   Check staged files for the optional pre-commit hook.' \
		'--dry-run  Print the selected file counts without running checks.'
}

ci_require_tool() {
	if ! command -v "$1" >/dev/null 2>&1; then
		printf 'BLOCKER: %s is unavailable.\n' "$1" >&2
		printf 'SAFE_NEXT_STEP: install %s, then rerun bless.\n' "$1" >&2
		return "${CI_EXIT_BLOCKED}"
	fi
}

main() {
	local mode=all dry_run=false selected_mode=false path status=0
	local -a python_files=() markdown_files=() yaml_files=() shell_files=()
	while (($# > 0)); do
		case "$1" in
		--all | --staged)
			if [[ "${selected_mode}" == true ]]; then
				usage >&2
				return "${CI_EXIT_USAGE}"
			fi
			mode="${1#--}"
			selected_mode=true
			;;
		--dry-run) dry_run=true ;;
		--help)
			usage
			return "${CI_EXIT_OK}"
			;;
		*)
			usage >&2
			return "${CI_EXIT_USAGE}"
			;;
		esac
		shift
	done

	ci_require_tool git || return $?
	cd -- "${CI_ROOT}"
	CI_INVENTORY="$(mktemp "${TMPDIR:-/tmp}/ci-bless.XXXXXX")" ||
		return "${CI_EXIT_BLOCKED}"
	trap 'rm -f -- "${CI_INVENTORY}"' EXIT
	if [[ "${mode}" == staged ]]; then
		git diff --cached --name-only -z --diff-filter=ACMRT >"${CI_INVENTORY}" ||
			return "${CI_EXIT_BLOCKED}"
	else
		git ls-files --cached --others --exclude-standard -z >"${CI_INVENTORY}" ||
			return "${CI_EXIT_BLOCKED}"
	fi
	while IFS= read -r -d '' path; do
		[[ -f "${path}" ]] || continue
		case "${path}" in
		.internal/* | .coordination/* | .codex/* | .claude/* | .idea/* | docs/vendor/*)
			continue
			;;
		esac
		case "${path}" in
		*.py) python_files+=("${path}") ;;
		*.md) markdown_files+=("${path}") ;;
		*.yaml | *.yml) yaml_files+=("${path}") ;;
		*.sh | *.bash | .githooks/*) shell_files+=("${path}") ;;
		esac
	done <"${CI_INVENTORY}"

	if [[ "${dry_run}" == true ]]; then
		printf '{"status":"PLAN","mode":"%s","python":%s,"markdown":%s,"yaml":%s,"shell":%s}\n' \
			"${mode}" "${#python_files[@]}" "${#markdown_files[@]}" \
			"${#yaml_files[@]}" "${#shell_files[@]}"
		return "${CI_EXIT_OK}"
	fi

	if [[ "${mode}" == staged ]]; then
		ci_block_staged_secret_paths "${CI_INVENTORY}" || return $?
		git diff --cached --check >&2 || status=1
	fi
	if ((${#python_files[@]} > 0)); then
		ci_require_tool ruff || return $?
		ruff check --no-cache -- "${python_files[@]}" >&2 || status=1
		ruff format --check --no-cache -- "${python_files[@]}" >&2 || status=1
	fi
	if ((${#markdown_files[@]} > 0)); then
		ci_require_tool markdownlint-cli2 || return $?
		markdownlint-cli2 --config .markdownlint-cli2.yaml \
			"${markdown_files[@]}" >&2 || status=1
	fi
	if ((${#yaml_files[@]} > 0)); then
		ci_require_tool yamllint || return $?
		yamllint "${yaml_files[@]}" >&2 || status=1
	fi
	if ((${#shell_files[@]} > 0)); then
		ci_require_tool shellcheck || return $?
		shellcheck "${shell_files[@]}" >&2 || status=1
	fi
	if ((status == 0)); then
		printf '{"status":"PASS","mode":"%s"}\n' "${mode}"
	else
		printf '{"status":"FAIL","mode":"%s"}\n' "${mode}"
	fi
	return "${status}"
}

main "$@"
