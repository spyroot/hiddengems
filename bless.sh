#!/usr/bin/env bash
set -Eeuo pipefail

GALILEO_ROOT="$({
	cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
	pwd -P
})"
readonly GALILEO_ROOT

# shellcheck source=lib/bash/toolchain/jobs.bash
source "${GALILEO_ROOT}/lib/bash/toolchain/jobs.bash"
# shellcheck source=automation/lib/core/exit_codes.bash
source "${GALILEO_ROOT}/automation/lib/core/exit_codes.bash"
# shellcheck source=automation/lib/core/subject.bash
source "${GALILEO_ROOT}/automation/lib/core/subject.bash"
# shellcheck source=automation/lib/lint/secret_paths.bash
source "${GALILEO_ROOT}/automation/lib/lint/secret_paths.bash"
# shellcheck source=automation/lib/lint/git_diff.bash
source "${GALILEO_ROOT}/automation/lib/lint/git_diff.bash"
# shellcheck source=automation/lib/lint/gitleaks.bash
source "${GALILEO_ROOT}/automation/lib/lint/gitleaks.bash"
# shellcheck source=automation/lib/lint/markdown.bash
source "${GALILEO_ROOT}/automation/lib/lint/markdown.bash"
# shellcheck source=automation/lib/lint/shellcheck.bash
source "${GALILEO_ROOT}/automation/lib/lint/shellcheck.bash"
# shellcheck source=automation/lib/lint/source_graph.bash
source "${GALILEO_ROOT}/automation/lib/lint/source_graph.bash"
# shellcheck source=automation/lib/lint/script_interface.bash
source "${GALILEO_ROOT}/automation/lib/lint/script_interface.bash"
# shellcheck source=automation/lib/lint/json.bash
source "${GALILEO_ROOT}/automation/lib/lint/json.bash"
# shellcheck source=automation/lib/lint/yaml.bash
source "${GALILEO_ROOT}/automation/lib/lint/yaml.bash"
# shellcheck source=automation/lib/lint/value_secret_stamps.bash
source "${GALILEO_ROOT}/automation/lib/lint/value_secret_stamps.bash"
# shellcheck source=automation/lib/lint/domain_lock.bash
source "${GALILEO_ROOT}/automation/lib/lint/domain_lock.bash"
# shellcheck source=automation/lib/lint/helm_lint.bash
source "${GALILEO_ROOT}/automation/lib/lint/helm_lint.bash"
# shellcheck source=automation/lib/lint/kubernetes_schema.bash
source "${GALILEO_ROOT}/automation/lib/lint/kubernetes_schema.bash"
# shellcheck source=automation/lib/lint/architecture_schema.bash
source "${GALILEO_ROOT}/automation/lib/lint/architecture_schema.bash"

usage() {
	printf '%s\n' \
		'Usage: ./bless.sh [--staged] [--dry-run]' \
		'       ./bless.sh --help'
}

main() {
	local dry_run=false
	while (($# > 0)); do
		case "$1" in
		--staged) ;;
		--dry-run) dry_run=true ;;
		--help)
			usage
			return "${GALILEO_EXIT_OK}"
			;;
		*)
			usage >&2
			return "${GALILEO_EXIT_USAGE}"
			;;
		esac
		shift
	done
	local maximum="${MAX_JOBS:-4}" available jobs
	available="$(galileo_toolchain_cpu_count)" || return
	jobs="$(galileo_toolchain_job_count "${maximum}" "${available}")" || return
	printf 'Bless jobs: MAX_JOBS=%s available_cpus=%s effective_jobs=%s\n' \
		"${maximum}" "${available}" "${jobs}"
	export JOBS="${jobs}"
	galileo_require_staged_subject "${GALILEO_ROOT}" || return $?
	galileo_block_staged_secret_paths "${GALILEO_ROOT}" || return $?
	galileo_lint_staged_git_diff "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_secrets "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_markdown "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_shell "${GALILEO_ROOT}" "${dry_run}" || return $?
	# Whole tree, not the staged set: a cycle is a property of the graph, and
	# the commit that closes one usually touches only one of its edges.
	if galileo_source_graph_cycles "${GALILEO_ROOT}"; then
		printf 'Source graph: acyclic\n'
	else
		printf 'BLOCKER: the library source graph has a cycle\n' >&2
		return "${GALILEO_EXIT_INVALID_DATA}"
	fi
	# Bash has one namespace, so the file is the only separation there is: two
	# files defining one name is whichever was sourced last, silently.
	if galileo_source_graph_duplicate_functions "${GALILEO_ROOT}"; then
		printf 'Source graph: every sourced function name is defined once\n'
	else
		printf 'BLOCKER: a function name is defined in more than one library file\n' >&2
		return "${GALILEO_EXIT_INVALID_DATA}"
	fi
	if galileo_source_graph_tests_source_two_scripts "${GALILEO_ROOT}"; then
		printf 'Source graph: no test sources two executables\n'
	else
		printf 'BLOCKER: a test sources two executables into one shell\n' >&2
		return "${GALILEO_EXIT_INVALID_DATA}"
	fi
	galileo_lint_staged_script_interface "${GALILEO_ROOT}" "${dry_run}" ||
		return $?
	galileo_lint_staged_json "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_yaml "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_value_secret_stamps "${GALILEO_ROOT}" "${dry_run}" ||
		return $?
	galileo_lint_staged_domain_lock "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_architecture_schema "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_helm_chart "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_render_staged_helm_chart "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_kubernetes_schema "${GALILEO_ROOT}" "${dry_run}" || return $?
	galileo_lint_staged_kubernetes_policy "${GALILEO_ROOT}" "${dry_run}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	main "$@"
fi
