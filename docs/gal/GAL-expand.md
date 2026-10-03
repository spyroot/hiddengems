# GAL-expand: bounded expansion of a declared target

Status: proposal, revision 1. Not approved for implementation. The owner decides the choices in
[Alternatives](#alternatives-for-the-owners-decision). The overview of all features is
[provider-routing-design.md](../provider-routing-design.md).

## Purpose and observable capability

A declared target can name a set of locations instead of one: a directory of dotenv files, or a directory of
kubeconfig files. `expand()` turns such a target into one concrete target per location. It reads only local
directory entries and file metadata, within bounds the target declares.

- **Observable capability:**
  - the target `{"provider": "dotenv", "path": "~/work", "depth": 1}` yields one instance per dotenv-family
    file at most one level below `~/work`;
  - the target `{"provider": "kubernetes", "kubeconfig": "~/.kube/clusters"}` yields one instance per regular
    file directly in that directory.
- **Today, expansion exists only as implicit discovery,** with fixed roots and bounds:
  - `DotEnvProvider._scan` (`dotenv_provider.py:377-420`) walks down from the working directory. It is breadth
    first and sorted by name, stays within 1024 entries and 0.25 seconds, never follows a symlink, and never
    crosses a mount point. It has no depth limit, and it runs only when nothing is configured (`detect`,
    `dotenv_provider.py:551-552`).
  - `KubernetesProvider._directory_kubeconfigs` (`k8s_provider.py:85-121`) reads only the regular files
    directly in `~/.kube` and `./.kube`, at most 64 entries, and rejects a symlinked or mounted directory.
- **A declared directory is dropped today, with nothing reported.** `DotEnvProvider._inspect_candidate` keeps
  only a path for which `is_file()` holds (`dotenv_provider.py:448`), and `KubernetesProvider._paths` skips
  any path that is not a file (`k8s_provider.py:189`).

## Scope

In scope:

- the `expand()` hook and its default;
- depth and file-name bounds for the two existing walkers;
- expansion of declared dotenv and kubeconfig directories;
- the naming and merging of expanded instances;
- the acceptance tests for all of the above.

What each provider expands:

- **dotenv:** a directory into files. Bounds: depth, file names, entries, and time.
- **Kubernetes:** a directory into files. Bounds: depth, entries, and file size. Contexts inside each file belong
  to `GAL-kube-contexts`, which applies to every file this feature yields.
- **Not expanded:**
  - Kubernetes namespaces are coordinates that `find_gem` searches inside one instance (`candidate_namespaces`).
    They are not instances.
  - 1Password accounts are not listed, because listing them means running `op`, and detection never starts a
    process. Each account is declared as its own target. Vaults are a lookup selector (`vault_id`), not
    instances.
  - The Keychain has one instance per user.

Out of scope:

- exclude masks (open question `GAL-scan-masks`);
- choosing a scan profile per call (open question `GAL-scan-depth`);
- declared targets themselves (`GAL-targets`, required).

## Layout

```text
~ src/hiddengems/abstract_provider.py      expand() hook with its default
~ src/hiddengems/hidden_gems.py            declared targets pass through expand() before detection
~ src/hiddengems/gems/dotenv_provider.py   expand(); _scan gains depth and names; _matches_names replaces _is_dotenv_name
~ src/hiddengems/gems/k8s_provider.py      expand(); _directory_kubeconfigs gains depth
+ tests/test_hidden_gems_expand.py         acceptance cases
```

## Names, signatures, errors, and call sites

`src/hiddengems/abstract_provider.py`:

- **New hook, not abstract:** `@classmethod expand(cls, settings: Mapping[str, Any]) -> tuple[dict[str, Any], ...]`.
  - Default: `return (dict(settings),)`, the one target unchanged, as a new dict the caller owns.
  - Contract:
    - it reads only local directory entries and file metadata, plus, for Kubernetes through
      `GAL-kube-contexts`, the kubeconfig YAML;
    - it never reads a gem value, starts a process, or uses the network;
    - it never mutates `settings`;
    - it returns zero or more mappings in a deterministic order, and never `None`. Zero mappings means the
      declared directory holds no matching file; the caller reports that target as ABSENT;
    - each mapping describes exactly one concrete target, or carries `scan_issue` to mark a part that was not
      checked.

`src/hiddengems/hidden_gems.py`, the caller:

- `HiddenGems.__init__`, in the target translation step of `GAL-targets`, calls `provider_type.expand(settings)`
  for each declared target.
- **Naming:** each result becomes one detection candidate, named the target name, `TARGET_NAME_SEPARATOR`, and
  the file's path relative to the declared directory. A single result from the default keeps the target's name.
- **Unchecked parts:** a result that carries `scan_issue` becomes today's pseudo-instance record
  (`dotenv:unverified:<root>` or `kubernetes:unverified:<path>`), so a lookup in scope ends in
  `IncompleteGemLookupError`, as implicit discovery does today.
- **Identity and merging are unchanged:** instance ids stay `dotenv:<resolved path>` and
  `kubernetes:<resolved path>:<context>`. A file reached both by expansion and by implicit discovery becomes one
  record that keeps both evidence entries (`DotEnvProvider._merge_candidates`,
  `KubernetesProvider._merge_context`).
- **Evidence:** a file found by expansion carries `EvidenceSource.CONFIG`, described with
  `EXPANDED_EVIDENCE_DESCRIPTION`, because its root was declared.
- **An empty directory is reported as ABSENT, not dropped and not UNKNOWN.** A declared directory with no
  matching file yields no instance and no issue, because it was walked completely. The router lists that
  target in `LookupResult.providers` with `ProviderState.ABSENT`, from `GAL-sdk-optional`. It does not add
  it to `HiddenGems._unavailable`, whose "Configured provider was not detected" issue (`hidden_gems.py:133-143`)
  would make the lookup Incomplete for a fact that is known. An empty result stays reserved for the case
  where no provider is detected at all.

`src/hiddengems/gems/dotenv_provider.py`:

- **Changed:** `_scan(cls, root: Path, *, depth: float = UNBOUNDED_DEPTH,
  names: tuple[str, ...] = DEFAULT_DOTENV_NAMES, max_entries: int = MAX_SCAN_ENTRIES,
  max_seconds: float = MAX_SCAN_SECONDS) -> tuple[tuple[Path, ...], str | None]`.
  - The breadth-first walk keeps `(directory, level)` pairs. A directory at `level == depth` is listed but not
    descended.
  - Everything else works as today. The defaults reproduce today's walk exactly.
  - The `str | None` issue result is today's signature and is kept.
- **New:** `_matches_names(name: str, names: tuple[str, ...]) -> bool`, which returns
  `any(fnmatch.fnmatchcase(name, pattern) for pattern in names)`. It replaces `_is_dotenv_name`. With
  `DEFAULT_DOTENV_NAMES`, it accepts exactly the names accepted today (`name == ".env" or
  name.startswith(".env.")`).
- **New:** `expand(cls, settings)`.
  - When `settings["path"]` resolves to a directory, it walks it with `_scan` and the target's bounds, and
    returns one `{"path": file}` per file found. If the walk was cut short, it also returns
    `{"scan_issue": issue, "scan_root": path}`.
  - When `path` names a file, it returns the default.
  - A directory together with `backup_filename` raises `ValueError` naming the target, because one backup file
    cannot serve several files.
- **Settings it reads:**
  - `path`;
  - `depth`: a non-negative integer, or `"unbounded"`. Default `"unbounded"`.
  - `names`: a list of file-name patterns with no path separator. Default `DEFAULT_DOTENV_NAMES`.
  - `max_entries`: 1 to `MAX_SCAN_ENTRIES`.
  - `max_seconds`: above 0, at most `MAX_SCAN_SECONDS`.

  A value out of range raises `ValueError` naming the key. No value is clamped silently.

`src/hiddengems/gems/k8s_provider.py`:

- **Changed:** `_directory_kubeconfigs(cls, directory: Path, *, depth: int = DEFAULT_KUBE_DIRECTORY_DEPTH,
  max_entries: int = MAX_DIRECTORY_ENTRIES)`, with the same return value.
  - Above depth 0, it descends into subdirectories that are neither symlinks nor mount points, sharing one entry
    budget.
  - The defaults reproduce today.
- **New:** `expand(cls, settings)`.
  - When `kubeconfig` names a directory, it returns one `{"kubeconfig_paths": [file]}` per file found, with the
    target's `context` and `namespace` copied into each.
  - `MAX_AUTO_CONFIG_BYTES` applies to these files, as it does to discovered ones today.
  - A walk cut short also returns `{"scan_issue": issue, "scan_root": path}`.
- **Settings it reads:**
  - `kubeconfig`;
  - `depth`: a non-negative integer. Default `DEFAULT_KUBE_DIRECTORY_DEPTH`.
  - `max_entries`: 1 to `MAX_DIRECTORY_ENTRIES`.

## Constants

- In `src/hiddengems/constants/config.py`:
  - `UNBOUNDED_DEPTH: Final[float] = math.inf`, which the setting `"unbounded"` maps to, and
    `UNBOUNDED_DEPTH_WORD: Final[str] = "unbounded"`;
  - `TARGET_NAME_SEPARATOR: Final[str] = "/"`;
  - `EXPANDED_EVIDENCE_DESCRIPTION: Final[str] = "Found by expanding declared target {target}"`.
- In `src/hiddengems/gems/dotenv_constants.py`: `DEFAULT_DOTENV_NAMES: Final[tuple[str, ...]] = (".env",
  ".env.*")`. It replaces the overview's `DOTENV_FILE_NAME` and `DOTENV_FILE_PREFIX`.
- In `src/hiddengems/gems/kubernetes_constants.py`: `DEFAULT_KUBE_DIRECTORY_DEPTH: Final[int] = 0`.
- Existing limits: `MAX_SCAN_ENTRIES`, `MAX_SCAN_SECONDS`, `MAX_DIRECTORY_ENTRIES`, and `MAX_AUTO_CONFIG_BYTES`.

`"unbounded"` maps to `math.inf`, not to `None`, so no `None` stands for a state.

## Libraries

None. `fnmatch` and `math` are in the standard library.

## Behavior and compatibility

- **Implicit discovery is unchanged.** It calls the walkers with their defaults, so these tests must pass
  unchanged:
  - `test_empty_config_finds_nested_dotenv_files`;
  - `test_discovery_accepts_only_dotenv_filename_family`;
  - `test_bounded_dotenv_scan_reports_unchecked_provider`;
  - `test_kubeconfig_environment_and_default_are_both_candidates`.
- **A declared directory is expanded instead of being dropped.** Behavior change; a strengthening, since nothing
  is ignored silently any more.
- **`AbstractGemProvider` gains a non-abstract hook.** Contract extension. A third-party provider inherits the
  default, one target unchanged.
- **Gates:** unchanged.

## Alternatives for the owner's decision

- **Default depth of a declared dotenv directory:**
  - (a) unbounded within the entry and time budgets, the same rule as implicit discovery today. Recommended:
    one rule for both.
  - (b) 0: only the directory's own files. The conservative profile.
- **Size bound for files found in a declared kubeconfig directory:**
  - (a) apply `MAX_AUTO_CONFIG_BYTES`, as for discovered files. Recommended: the user named a directory, not
    each file.
  - (b) no size bound, as for a declared file.

## Acceptance cases

All cases are in `tests/test_hidden_gems_expand.py`, using `tmp_path` trees and no real home directory.

The dotenv cases use one fixture tree: `root/.env`, `root/a/.env.local`, and `root/a/b/.env`.

- `test_dotenv_directory_depth_one`.
  - Input: that tree; a target with `depth` 1.
  - Expected: instances for `root/.env` and `root/a/.env.local` only, named `<target>/.env` and
    `<target>/a/.env.local`.
- `test_dotenv_directory_unbounded_matches_todays_scan`.
  - Input: the same tree; `depth` `"unbounded"`.
  - Expected: all three files, in the order today's `_scan(root)` returns them.
- `test_expansion_never_follows_symlinks_or_mounts`.
  - Input: a symlinked directory holding `.env`, and a directory for which `os.path.ismount` is patched to
    return true.
  - Expected: neither directory is descended.
- `test_expansion_budget_marks_incomplete`.
  - Input: `max_entries` 1.
  - Expected: one file plus a pseudo-instance record; `dig_gem` for a gem in an unlisted file raises
    `IncompleteGemLookupError`.
- `test_declared_directory_with_backup_is_rejected`.
  - Input: a dotenv directory target with `backup_filename`.
  - Expected: `ValueError` naming the target.
- `test_out_of_range_bound_is_rejected`.
  - Input: `max_entries` 0, and `max_entries` above `MAX_SCAN_ENTRIES`.
  - Expected: `ValueError` naming the key; nothing is clamped.
- `test_kube_directory_depth_zero_and_one`.
  - Input: a directory with two kubeconfig files and a subdirectory with one.
  - Expected: depth 0 yields two instances; depth 1 yields three.
- `test_expanded_and_discovered_file_merge`.
  - Input: a file that implicit discovery finds and a declared directory also yields.
  - Expected: one record, with two evidence entries.
- `test_default_expand_returns_a_copy`.
  - Input: `OnePasswordProvider.expand(settings)`.
  - Expected: one dict equal to `settings` but not the same object; mutating it leaves `settings` unchanged.
- `test_expansion_reads_no_values`.
  - Input: `open` patched to fail for dotenv files while `expand` runs.
  - Expected: `expand` succeeds.
- `test_empty_declared_directory_is_reported_absent`.
  - Input: a declared directory with no matching file, and no other holder of the gem.
  - Expected:
    - no instance and no issue;
    - `LookupResult.providers` lists the target with `ProviderState.ABSENT`;
    - `dig_gem` raises `GemNotFoundError`, not `IncompleteGemLookupError`.

## Dependencies on other features

- **Requires:**
  - `GAL-targets`, whose translation step calls `expand()` and supplies the target names;
  - `GAL-sdk-optional`, for `ProviderState.ABSENT`. It is delivered earlier in the plan.
- **Required by:** `GAL-kube-contexts`, which splits each expanded kubeconfig file by context. It uses
  `TARGET_NAME_SEPARATOR` in place of its own separator constant.
- **Open questions it answers in part:**
  - `GAL-scan-depth`: `depth` and the budgets are the per-target knobs; how a caller selects a profile per call
    stays open.
  - `GAL-scan-masks`: `names` is the include mask; exclude masks stay open.
- **Independent of `GAL-plugin-contract`:** the hook does not depend on where `put_gem` lives.
