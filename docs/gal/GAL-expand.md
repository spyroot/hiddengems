# GAL-expand: controlled expansion of a declared target

Status: proposal, revision 3. Not approved for implementation. The owner decides the choices in
[Alternatives](#alternatives-for-the-owners-decision). The overview of all features is
[provider-routing-design.md](../provider-routing-design.md).

Revision 3 changes revision 2 in four ways:

- **The factory dispatches `expand()`.** `HiddenGems` calls `GemProvider.expand(provider, settings)`, which
  finds the class the way `GemProvider.create` does. Calling a provider class directly would bypass the
  factory, which the overview's gate forbids.
- **The [Abstraction-extension gate](#abstraction-extension-gate) section** declares each extension.
- **The [Exceptions](#exceptions) section:** no new class; every refusal is a `ValueError`.
- **The acceptance cases live in the existing suites,** with no new test file.

Revision 2 changed revision 1 in four ways:

- **Expansion is controlled.** A target expands only when its path is a pattern, such as
  `"kubeconfig": "~/some/dir/*"`. A plain path names exactly one file.
- **A guard keeps walks out of home-wide trees and cloud storage,** so a pattern can never drag a 4 TB Dropbox
  folder into detection.
- **Every declared location that yields nothing is reported.**
- **Walk outcomes are enums,** not free text.

## Purpose and observable capability

A user who keeps many credential files in one structure declares them with one pattern, not one target per file.
A user with 20 clusters, each in `~/clusters/<name>/kubeconfig`, writes:

```json
{"provider": "kubernetes", "kubeconfig": "~/clusters/*/kubeconfig"}
```

That one target yields 20 instances, one per file. `expand()` turns a pattern target into one concrete target per
matching file. It reads only local directory entries and file metadata, within bounds.

- **Observable capability:**
  - `"kubeconfig": "~/some/dir/*"` yields one instance per regular file directly in `~/some/dir`;
  - `"kubeconfig": "~/some/dir/**/*"` yields one instance per regular file in that tree;
  - `"path": "~/work/**/.env*"` yields one instance per dotenv-family file in the tree under `~/work`;
  - `"path": "~/**/.env"` is refused, because its tree starts at the home directory.
- **Today:**
  - Expansion exists only as implicit discovery, with fixed roots and bounds. `DotEnvProvider._scan`
    (`dotenv_provider.py:377-420`) walks down from the working directory, breadth first and sorted by name,
    within 1024 entries and 0.25 seconds, never following a symlink or crossing a mount point, with no depth
    limit. It runs only when nothing is configured (`dotenv_provider.py:551-552`).
    `KubernetesProvider._directory_kubeconfigs` (`k8s_provider.py:85-121`) reads only the regular files
    directly in `~/.kube` and `./.kube`, at most 64 entries, and rejects a symlinked or mounted directory.
  - Nothing today keeps the dotenv walk out of cloud-sync folders. Started in the home directory, it can descend
    into Dropbox or iCloud until its budget runs out.
  - A declared path that is not a file is dropped with no report: `DotEnvProvider._inspect_candidate` keeps only
    a path for which `is_file()` holds (`dotenv_provider.py:442-454`, skipped at `481`), and
    `KubernetesProvider._paths` skips any path that is not a file (`k8s_provider.py:189`).
  - Walk outcomes are free-text strings, with `None` meaning complete: the dotenv walk at
    `dotenv_provider.py:369-418`, and the Kubernetes walk and file checks at `k8s_provider.py:92-203`.

## Scope

In scope:

- the `expand()` hook and its default;
- the pattern grammar for declared dotenv and kubeconfig paths;
- the bounds of a pattern walk;
- the guard against home-wide trees, cloud-sync folders, and online-only files;
- reporting a declared location that yields nothing;
- enums for walk and file outcomes;
- naming and merging of expanded instances;
- the acceptance tests for all of the above.

What each provider expands:

- **dotenv:** a pattern into files, filtered by dotenv-family names.
- **Kubernetes:** a pattern into files. Contexts inside each file belong to `GAL-kube-contexts`, which applies to
  every file this feature yields.
- **Not expanded:**
  - Kubernetes namespaces are coordinates that `find_gem` searches inside one instance (`candidate_namespaces`).
    They are not instances.
  - 1Password accounts are not listed, because listing them means running `op`, and detection never starts a
    process. Each account is declared as its own target. Vaults are a lookup selector (`vault_id`), not
    instances.
  - The Keychain has one instance per user.
- **Other providers:** a file-based provider proposed later, such as SOPS files, uses the same hook, grammar, and
  guard.

Out of scope:

- exclude masks (open question `GAL-scan-masks`);
- choosing a scan profile per call (open question `GAL-scan-depth`);
- declared targets themselves (`GAL-targets`, required).

## Layout

```text
~ src/hiddengems/abstract_provider.py           expand() hook with its default
~ src/hiddengems/abstraction.py                 Presence enum
~ src/hiddengems/gem_provider.py                GemProvider.expand dispatches to the provider class
~ src/hiddengems/hidden_gems.py                 declared targets pass through expand(); empty ones reported ABSENT
+ src/hiddengems/pattern_walk.py                bounded, guarded pattern walk shared by both providers
~ src/hiddengems/constants/config.py            pattern and guard constants
~ src/hiddengems/gems/dotenv_constants.py       DotEnvWalkOutcome enum, DEFAULT_DOTENV_NAMES
~ src/hiddengems/gems/dotenv_provider.py        expand(); _scan returns DotEnvWalkOutcome and applies the guard
~ src/hiddengems/gems/kubernetes_constants.py   KubeWalkOutcome and KubeFileIssue enums
~ src/hiddengems/gems/k8s_provider.py           expand(); walk and file checks return the enums
~ tests/test_hidden_gems_dotenv.py              dotenv and pattern-walk cases
~ tests/test_hidden_gems_routing.py             Kubernetes and router cases
~ tests/contract/test_provider_contract.py      default and factory cases
```

`pattern_walk.py` is one new module because both providers need the same grammar, bounds, and guard, and
`software-design.md` forbids two implementations of one algorithm.

## Pattern grammar

A declared dotenv `path` or Kubernetes `kubeconfig` is a pattern when any path segment contains `*`, `?`, or `[`.

- **Plain path:** names exactly one file and is never expanded.
  - If it names a directory, `expand()` raises `ValueError` naming the target and suggesting `<dir>/*`.
  - If it does not exist, the target is reported ABSENT.
- **Pattern path:**
  - `~` is expanded first.
  - The segments before the first pattern segment form the root, which must be an existing directory. A
    missing root reports the target ABSENT.
  - Each later segment is matched against entry names at its level with `fnmatch.fnmatchcase`.
  - A segment of exactly `**` matches zero or more directory levels.
  - Only the last segment matches files; earlier segments match directories.
- **Dot names:** `*` matches names that start with a dot, unlike shell globbing, because `.env` files and `.kube`
  directories start with one.
- **dotenv filter:** matched files are also filtered by `names`, `DEFAULT_DOTENV_NAMES` unless the target sets it.
  So `"~/work/**/*"` still yields only dotenv-family files.
- **Examples:**
  - `~/some/dir/*`: every regular file directly in `~/some/dir`;
  - `~/clusters/*/kubeconfig`: the file `kubeconfig` in each direct subdirectory of `~/clusters`;
  - `~/some/dir/**/*`: every regular file in the tree;
  - `~/work/**/.env*`: dotenv files in the tree under `~/work`.

## Guard: home-wide trees, cloud storage, and online-only files

A walk must never pull a cloud-synced folder into detection. Listing a synced tree can make the sync service
enumerate it remotely, and reading an online-only file forces a download. A 4 TB Dropbox folder must cost nothing.

- **Tree patterns need a specific root.** A pattern whose root is the home directory, a filesystem root (`/`), or
  a drive root, and that contains `**`, raises `ValueError` naming the target. A single-level pattern such as
  `~/.env*` stays allowed, because it lists one directory and descends nowhere.
- **Cloud-sync folders are never entered** unless the pattern root is itself inside one. If the user names a
  directory inside Dropbox on purpose, that is the user's controlled choice. The folders are
  `CLOUD_SYNC_DIRECTORIES`, relative to the home directory:
  - `Library/CloudStorage`, where macOS keeps Dropbox, Google Drive, OneDrive, and Box;
  - `Library/Mobile Documents`, the iCloud Drive store on macOS;
  - `Dropbox`, `OneDrive`, `Google Drive`, `Box`, and `iCloudDrive`, the folders those clients create on Linux
    and Windows.

  A skipped folder is recorded as `PatternWalkOutcome.CLOUD_SYNC_SKIPPED`. That makes the walk incomplete, so a
  lookup in scope ends in `IncompleteGemLookupError`, and the caller sees which folder was skipped.
- **Online-only files are never read.** A matched file the operating system marks as not stored locally is not
  opened:
  - on macOS, `st_flags & MACOS_SF_DATALESS`;
  - on Windows, `st_file_attributes` with `FILE_ATTRIBUTE_OFFLINE` or `WINDOWS_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`.

  The file is reported UNKNOWN with `KubeFileIssue.NOT_LOCAL`, or the dotenv equivalent, and the next action
  "make the file available offline, then re-run". Linux has no portable marker, so the cloud-folder rule is the
  protection there.
- **Network mounts are never crossed.** An NFS or SMB share mounted anywhere under the home directory is a mount
  point (`os.path.ismount`), and no walk crosses a mount point. A user who wants files on that share sets the
  pattern root inside it, which is the explicit, advanced-user form of control; detection never reaches the
  share on its own.
- **The guard also applies to implicit dotenv discovery.** Started in the home directory today, that discovery
  can enter a cloud folder; with the guard, it skips it and reports the skip.

## Bounds of a pattern walk

- **Links and mounts:** never follows a symlink, to a file or a directory, and never crosses a mount point, the
  same rules as today's walkers.
- **`depth`:** how many directory levels `**` may span. A non-negative integer or `"unbounded"`; default
  `"unbounded"`. `"unbounded"` maps to `UNBOUNDED_DEPTH`, which is `math.inf`, not `None`.
- **`max_entries`:** directory entries read, from 1 to `PATTERN_MAX_ENTRIES`; default `PATTERN_MAX_ENTRIES`.
- **`max_seconds`:** wall time, above 0 and at most `PATTERN_MAX_SECONDS`; default `PATTERN_MAX_SECONDS`.
- **Size:** for Kubernetes, a matched file above `MAX_AUTO_CONFIG_BYTES` is skipped with
  `KubeFileIssue.SIZE_LIMIT`.
- **Out-of-range values:** raise `ValueError` naming the key. No value is clamped silently.
- **Order:** breadth first, with entries sorted by name at each level, so results are deterministic.
- **A walk cut short** by a budget, an unreadable directory, or a skipped cloud folder, ends with the matching
  `PatternWalkOutcome`. The target then also yields a pseudo-instance record carrying that outcome, so a lookup in
  scope ends in `IncompleteGemLookupError`, as discovery does today.

## Names, signatures, errors, and call sites

`src/hiddengems/abstract_provider.py`:

- **New hook, not abstract:** `@classmethod expand(cls, settings: Mapping[str, Any]) -> tuple[dict[str, Any], ...]`.
  - Default: `return (dict(settings),)`, the one target unchanged, as a new dict the caller owns.
  - Contract:
    - it reads only directory entries and file metadata, plus, for Kubernetes through `GAL-kube-contexts`, the
      YAML of files stored locally;
    - it never reads a gem value, starts a process, uses the network, or opens an online-only file;
    - it never mutates `settings`;
    - it returns zero or more mappings in a deterministic order, never `None`. Zero mappings means a declared
      location that yields nothing; the caller reports that target ABSENT.

`src/hiddengems/pattern_walk.py` (new):

- `class PatternWalkOutcome(StrEnum)`, with these members:
  - `COMPLETE = "complete"`;
  - `ENTRY_LIMIT = "Pattern walk entry limit reached"`;
  - `TIME_LIMIT = "Pattern walk time limit reached"`;
  - `DIRECTORY_UNREADABLE = "Pattern walk could not read every directory"`;
  - `ROOT_MISSING = "Pattern root does not exist"`;
  - `CLOUD_SYNC_SKIPPED = "Pattern walk skipped a cloud-sync folder"`.
- Frozen dataclass `PatternWalkResult`, with the fields `files: tuple[Path, ...]`, `not_local: tuple[Path, ...]`,
  `outcome: PatternWalkOutcome`, and `skipped: tuple[Path, ...]`.
- `is_pattern(path: str) -> bool`: true when any segment contains `*`, `?`, or `[`.
- `is_local(path: Path) -> bool`: false when the operating system marks the file as online-only.
- `cloud_sync_roots(home: Path) -> tuple[Path, ...]`: `CLOUD_SYNC_DIRECTORIES` resolved under `home`, computed at
  run time.
- `walk_pattern(pattern: str, *, depth: float, max_entries: int, max_seconds: float) -> PatternWalkResult`, which
  implements the grammar, bounds, and guard above. It raises `ValueError` for a tree pattern with a home or
  filesystem root.

`src/hiddengems/gem_provider.py`, the factory:

- **New:** `@classmethod expand(cls, provider: str, settings: Mapping[str, Any]) -> tuple[dict[str, Any], ...]`.
  It finds the class in `provider_types` whose `name` equals `provider`, by the lookup `GemProvider.create`
  uses, and returns that class's `expand(settings)`. An unregistered name raises
  `ValueError(f"Unknown provider type: {provider!r}")`, the message `create` raises today
  (`gem_provider.py:81-82`).

`src/hiddengems/hidden_gems.py`, the caller:

- `HiddenGems.__init__`, in the target translation step of `GAL-targets`, calls
  `GemProvider.expand(target.provider, settings)` for each declared target.
- **Naming:** each result becomes one detection candidate, named the target name, `TARGET_NAME_SEPARATOR`, and
  the file's path relative to the pattern root. A single result from a plain path keeps the target's name.
- **Unchecked parts:** a result carrying `scan_issue` becomes today's pseudo-instance record, so a lookup in
  scope ends in `IncompleteGemLookupError`.
- **Online-only files:** a result carrying `presence_issue` becomes an instance whose lookups are UNKNOWN, with
  the next action "make the file available offline, then re-run".
- **Zero results:** a target that expands to nothing is listed in `LookupResult.providers` with
  `ProviderState.ABSENT`, from `GAL-sdk-optional`. It is not added to `HiddenGems._unavailable`, whose
  "Configured provider was not detected" issue (`hidden_gems.py:133-143`) would make the lookup Incomplete for a
  fact that is known. An empty result stays reserved for the case where no provider is detected at all.
- **Identity and merging are unchanged:** instance ids stay `dotenv:<resolved path>` and
  `kubernetes:<resolved path>:<context>`. A file reached both by a pattern and by implicit discovery becomes one
  record that keeps both evidence entries (`DotEnvProvider._merge_candidates`,
  `KubernetesProvider._merge_context`).
- **Evidence:** a file found by a pattern carries `EvidenceSource.CONFIG`, described with
  `EXPANDED_EVIDENCE_DESCRIPTION`.

`src/hiddengems/gems/dotenv_provider.py`:

- **New:** `expand(cls, settings)`.
  - For a pattern `path`, it calls `walk_pattern`, filters the files with `_matches_names`, and returns one
    `{"path": file}` per local file and one `{"path": file, "presence_issue": "not_local"}` per online-only file.
    If the outcome is not `COMPLETE`, it also returns `{"scan_issue": outcome.value, "scan_root": root}`.
  - For a plain file path, it returns the default.
  - A pattern together with `backup_filename` raises `ValueError` naming the target, because one backup file
    cannot serve several files.
- **New:** `_matches_names(name: str, names: tuple[str, ...]) -> bool`, which returns
  `any(fnmatch.fnmatchcase(name, pattern) for pattern in names)`. It replaces `_is_dotenv_name`. With
  `DEFAULT_DOTENV_NAMES`, it accepts exactly the names accepted today.
- **Changed:** `_scan` returns `tuple[tuple[Path, ...], DotEnvWalkOutcome]` instead of `str | None`, and skips
  cloud-sync folders with `DotEnvWalkOutcome.CLOUD_SYNC_SKIPPED`. `DotEnvWalkOutcome.COMPLETE` replaces `None`;
  the other members' values are today's exact messages, so existing pseudo-instance text does not change.
- **Changed:** `_inspect_candidate` no longer drops a declared file that is missing. It returns
  `_DotEnvPathRecord` with a new field `presence: Presence`, and `detect` lists a declared file that is `ABSENT`
  in `LookupResult.providers` instead of skipping it.
- **Settings it reads:** `path`, `names`, `depth`, `max_entries`, and `max_seconds`.

`src/hiddengems/gems/k8s_provider.py`:

- **New:** `expand(cls, settings)`.
  - For a pattern `kubeconfig`, it calls `walk_pattern` and returns one `{"kubeconfig_paths": [file]}` per local
    file, with the target's `context` and `namespace` copied into each, and a `presence_issue` mapping per
    online-only file.
  - The size bound applies, as above.
  - If the outcome is not `COMPLETE`, it also returns `{"scan_issue": outcome.value, "scan_root": root}`.
- **Changed:** `_directory_kubeconfigs` and `_paths` return `KubeWalkOutcome` and `KubeFileIssue` members instead
  of free-text warnings. The values are today's exact messages.
- **Changed:** a declared kubeconfig file that is missing is reported ABSENT, instead of being skipped at
  `k8s_provider.py:189`.
- **Settings it reads:** `kubeconfig`, `depth`, `max_entries`, and `max_seconds`.

## Abstraction-extension gate

This section follows the [gate](../provider-routing-design.md#abstraction-extension-gate) of the overview. The
baseline is commit `4438234`.

### 1. Extensions

#### `AbstractGemProvider.expand`

- **Signature:** `@classmethod def expand(cls, settings: Mapping[str, Any]) -> tuple[dict[str, Any], ...]`. Not
  abstract.
- **Status:** optional; the default returns `(dict(settings),)`.
- **Behavior:** the contract listed under `src/hiddengems/abstract_provider.py` above: local directory entries
  and metadata only, no value, process, or network; `settings` never mutated; a deterministic order; never
  `None`; zero results mean the target is ABSENT.
- **Implementation owner:** `AbstractGemProvider`, the default; overrides in `DotEnvProvider` and
  `KubernetesProvider`.
- **Actual caller:** `GemProvider.expand`.
- **Acceptance tests:** `test_default_expand_returns_a_copy`, `test_expansion_reads_no_values`, and the
  pattern cases.

#### `GemProvider.expand`

- **Signature:** as listed under `src/hiddengems/gem_provider.py` above.
- **Status:** MUST ADD.
- **Behavior:** dispatch by provider name, the same lookup `GemProvider.create` uses; `ValueError` for an
  unregistered name.
- **Implementation owner:** `GemProvider` in `gem_provider.py`.
- **Actual caller:** `HiddenGems.__init__`, in the target translation step of `GAL-targets`.
- **Acceptance tests:** `test_factory_expand_dispatches_by_provider_name`.

#### `pattern_walk.py`

- **Signature:** `PatternWalkOutcome`, `PatternWalkResult`, `is_pattern`, `is_local`, `cloud_sync_roots`, and
  `walk_pattern`, as listed above.
- **Status:** MUST ADD; a module, not a member of `B`.
- **Behavior:** the grammar, guard, and bounds above.
- **Implementation owner:** `pattern_walk.py`.
- **Actual caller:** `DotEnvProvider.expand`, `KubernetesProvider.expand`, and, for the guard,
  `DotEnvProvider._scan`.
- **Acceptance tests:** the pattern, guard, and bound cases.

#### `Presence`, `DotEnvWalkOutcome`, `KubeWalkOutcome`, and `KubeFileIssue`

- **Signature:** as listed under [Constants and enums](#constants-and-enums).
- **Status:** MUST ADD.
- **Behavior:** values only. Each walk-outcome value equals today's message, so existing text is unchanged.
- **Implementation owner:** `abstraction.py`, `dotenv_constants.py`, and `kubernetes_constants.py`.
- **Actual caller:** `DotEnvProvider._scan` and `_inspect_candidate`; `KubernetesProvider._directory_kubeconfigs`
  and `_paths`; `HiddenGems.__init__`, which lists ABSENT targets.
- **Acceptance tests:** `test_dotenv_walk_outcomes_keep_todays_messages`,
  `test_kube_walk_outcomes_keep_todays_messages`, `test_missing_root_and_empty_match_are_absent`, and
  `test_declared_missing_file_is_absent`.

### 2. What each extension extends

`expand` extends `AbstractGemProvider` with a default; no member of `B` changes. `GemProvider.expand` extends
the existing factory, and no other code calls a provider class's `expand`.

### 3. Providers in P

| Provider | `expand` | What it expands |
| --- | --- | --- |
| `OnePasswordProvider` | inherited default | nothing: accounts are declared one per target |
| `DotEnvProvider` | override | a pattern `path` into files, filtered by `names` |
| `KeyringProvider` | inherited default | nothing: one instance per user |
| `KubernetesProvider` | override | a pattern `kubeconfig` into files |

### 4. Baseline tests whose expectations change

None. The baseline tests that touch the changed walkers keep their expectations:

- `tests/test_hidden_gems_dotenv.py:475` compares `scan_issue` with `"Dotenv discovery entry limit reached"`.
  The record still carries the outcome's string value, which equals that text.
- `tests/test_hidden_gems_routing.py:155-158` unpacks `KubernetesProvider._paths(...)` as `paths, warnings` and
  asserts `warnings == ()`. The shape is kept, and no warning occurs there.
- `tests/test_hidden_gems_routing.py:281,294,300` test the truthiness of `scan_issue`. `COMPLETE` is never stored
  in `scan_issue`, because only an incomplete walk creates a pseudo-instance.
- `tests/test_hidden_gems_dotenv.py:96-97` passes `object()` as `scan_issue` and expects `TypeError`. That check
  is unchanged.

The five tests listed under [Behavior and compatibility](#behavior-and-compatibility) pass unchanged.

### 5. Baseline

The baseline stays commit `4438234`. This proposal does not redefine `B`.

## Exceptions

No new class. Every refusal reuses `ValueError`, which already means "this argument's value is not acceptable"
in the baseline (`gem_provider.py:38`, and the dotenv settings checks):

- a declared plain path that names a directory, with the suggestion `<dir>/*`;
- a tree pattern rooted at the home directory, a filesystem root, or a drive root;
- `depth`, `max_entries`, or `max_seconds` out of range, naming the key;
- a dotenv pattern together with `backup_filename`;
- an unregistered provider name in `GemProvider.expand`, with the message of `GemProvider.create`.

An unreadable directory, a skipped cloud folder, or a budget limit is not an exception: it is a
`PatternWalkOutcome` on a pseudo-instance, so the lookup ends in `IncompleteGemLookupError` as today. An
online-only file is `Presence.NOT_LOCAL`, whose lookups are UNKNOWN. `test_exception_classes_match_the_register`
of `GAL-plugin` therefore needs no new entry for this feature.

## Constants and enums

- In `src/hiddengems/constants/config.py`:
  - `UNBOUNDED_DEPTH: Final[float] = math.inf` and `UNBOUNDED_DEPTH_WORD: Final[str] = "unbounded"`.
  - `PATTERN_MAX_ENTRIES: Final[int] = 1024` and `PATTERN_MAX_SECONDS: Final[float] = 0.25`: the same budget as
    today's dotenv discovery. A declared pattern is larger than automatic `~/.kube` discovery, so it does not
    reuse that walk's 64-entry limit.
  - `CLOUD_SYNC_DIRECTORIES: Final[tuple[str, ...]]`, holding `"Library/CloudStorage"`,
    `"Library/Mobile Documents"`, `"Dropbox"`, `"OneDrive"`, `"Google Drive"`, `"Box"`, and `"iCloudDrive"`,
    relative to the home directory. These are the clients' default folders. A client configured to use another
    folder is not guarded; the user can still declare a pattern rooted inside it.
  - `MACOS_SF_DATALESS: Final[int] = 0x40000000`, the macOS `SF_DATALESS` file flag. Python 3.11's `stat` module
    does not define it.
  - `WINDOWS_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS: Final[int] = 0x00400000`, the Windows attribute. `stat`
    defines `FILE_ATTRIBUTE_OFFLINE` but not this one.
  - `NOT_LOCAL_NEXT_ACTION: Final[str] = "Make the file available offline, then re-run"`.
  - `TARGET_NAME_SEPARATOR: Final[str] = "/"`.
  - `EXPANDED_EVIDENCE_DESCRIPTION: Final[str] = "Found by expanding declared target {target}"`.
- In `src/hiddengems/abstraction.py`: `class Presence(StrEnum)` with `PRESENT = "present"`, `ABSENT = "absent"`,
  and `NOT_LOCAL = "not_local"`. A file that exists but cannot be read stays `PRESENT` with today's
  `access_issue`, which makes its lookups UNKNOWN.
- In `src/hiddengems/gems/dotenv_constants.py`:
  - `DEFAULT_DOTENV_NAMES: Final[tuple[str, ...]] = (".env", ".env.*")`;
  - `class DotEnvWalkOutcome(StrEnum)`, with these members:
    - `COMPLETE = "complete"`;
    - `DIRECTORY_UNREADABLE = "Dotenv discovery could not read every directory"`;
    - `TIME_LIMIT = "Dotenv discovery time limit reached"`;
    - `ENTRY_LIMIT = "Dotenv discovery entry limit reached"`;
    - `ENTRY_UNREADABLE = "Dotenv discovery could not inspect every entry"`;
    - `CLOUD_SYNC_SKIPPED = "Dotenv discovery skipped a cloud-sync folder"`.
- In `src/hiddengems/gems/kubernetes_constants.py`:
  - `class KubeWalkOutcome(StrEnum)`, with these members:
    - `COMPLETE = "complete"`;
    - `LINKED_OR_MOUNTED = "Automatic kubeconfig directory is a link or mount"`;
    - `DIRECTORY_UNREADABLE = "Automatic kubeconfig directory could not be read"`;
    - `ENTRY_LIMIT = "Automatic kubeconfig directory entry limit reached"`;
    - `ENTRY_UNREADABLE = "Automatic kubeconfig entry could not be read"`.
  - `class KubeFileIssue(StrEnum)`, with these members:
    - `NOT_READABLE = "Kubeconfig file is not readable"`;
    - `SIZE_LIMIT = "Automatic kubeconfig file size limit reached"`;
    - `PATH_UNREADABLE = "Kubeconfig path could not be read"`;
    - `NOT_LOCAL = "Kubeconfig file is stored online only"`.
- Existing limits used: `MAX_SCAN_ENTRIES`, `MAX_SCAN_SECONDS`, `MAX_DIRECTORY_ENTRIES`, and
  `MAX_AUTO_CONFIG_BYTES`.

## Libraries

None. `fnmatch`, `math`, `stat`, and `enum` are in the standard library.

## Behavior and compatibility

- **Implicit discovery changes in one way:** the dotenv walk skips cloud-sync folders and reports the skip. Its
  outcome enums keep today's messages. These tests must pass unchanged:
  - `test_empty_config_finds_nested_dotenv_files`;
  - `test_discovery_accepts_only_dotenv_filename_family`;
  - `test_bounded_dotenv_scan_reports_unchecked_provider`;
  - `test_kubeconfig_environment_and_default_are_both_candidates`;
  - `test_bounded_scan_reports_incomplete_but_explicit_path_is_checked`.
- **Skipping cloud folders in implicit discovery.** Behavior change; a strengthening. A dotenv file inside a
  cloud folder is no longer found implicitly; the lookup reports the skip, and the user can declare that folder
  with a pattern.
- **A declared plain path that names a directory now raises `ValueError`** instead of being dropped silently.
  Behavior change: a configuration that relied on the silent drop now fails at load, and the error names the
  fix.
- **A declared missing file or empty pattern is reported ABSENT** instead of being dropped. Behavior change; a
  strengthening.
- **`AbstractGemProvider` gains a non-abstract hook.** Contract extension. A third-party provider inherits the
  default, one target unchanged.
- **Gates:** unchanged.

## Alternatives for the owner's decision

- **Meaning of a trailing `*`:**
  - (a) standard glob: `*` matches within one directory level, and `**` matches the tree. Recommended: it is
    the convention every shell user already knows.
  - (b) a trailing `*` expands the whole tree under that directory.
- **A declared plain path that names a directory:**
  - (a) raise `ValueError` that suggests `<dir>/*`. Recommended: it is explicit and tells the user the fix.
  - (b) report the target ABSENT.
- **Cloud-sync guard in implicit discovery:**
  - (a) apply it there too. Recommended: implicit discovery is where nobody chose the walk.
  - (b) apply it only to declared patterns, which keeps implicit discovery exactly as it is today.

## Acceptance cases

The cases extend the existing suites; there is no new test file. Each uses `tmp_path` trees and a patched home
directory, and none touches the real home directory or any real cloud folder.

- **`tests/test_hidden_gems_routing.py`,** beside the existing Kubernetes and router tests:
  `test_twenty_kubeconfigs_from_one_pattern`, `test_plain_directory_path_is_rejected`,
  `test_online_only_file_is_never_opened`, `test_missing_root_and_empty_match_are_absent`, and
  `test_kube_walk_outcomes_keep_todays_messages`.
- **`tests/test_hidden_gems_dotenv.py`,** beside the existing discovery tests: every other pattern, guard, and
  bound case below, and `test_dotenv_walk_outcomes_keep_todays_messages`.
- **`tests/contract/test_provider_contract.py`,** the kit of `GAL-plugin`:
  `test_default_expand_returns_a_copy` and `test_factory_expand_dispatches_by_provider_name`.

Cases:

- `test_twenty_kubeconfigs_from_one_pattern`.
  - Input: 20 directories under `clusters/`, each holding `kubeconfig`; the target
    `"kubeconfig": "<tmp>/clusters/*/kubeconfig"`.
  - Expected: 20 instances, named `<target>/<dir>/kubeconfig`.
- `test_single_star_is_one_level`.
  - Input: `dir/a`, `dir/b`, and `dir/sub/c`; the pattern `dir/*`.
  - Expected: `a` and `b` only, under alternative (a).
- `test_double_star_is_the_tree_within_depth`.
  - Input: the same tree; the pattern `dir/**/*`, with `depth` `"unbounded"` and then `depth` 0.
  - Expected: `a`, `b`, and `sub/c` with unbounded depth; `a` and `b` only with depth 0.
- `test_tree_pattern_at_home_is_rejected`.
  - Input: the home directory patched to `<tmp>/home`; the patterns `~/**/.env` and `/**/kubeconfig`.
  - Expected: `ValueError` naming the target.
- `test_single_level_pattern_at_home_is_allowed`.
  - Input: the patched home holding `.env.local`; the pattern `~/.env*`.
  - Expected: one instance.
- `test_cloud_sync_folder_is_skipped_and_reported`.
  - Input: the patched home with `Dropbox/app/.env` and `work/.env`; a pattern rooted at the patched home's
    `work` parent that would otherwise reach both.
  - Expected:
    - only `work/.env` is found;
    - the walk outcome is `CLOUD_SYNC_SKIPPED`, naming `Dropbox`;
    - a lookup for a gem only in the Dropbox file raises `IncompleteGemLookupError`.
- `test_network_mount_under_home_is_not_crossed`.
  - Input: the patched home with `share/app/.env`, where `os.path.ismount` is patched to return true for
    `share`; the pattern `~/*/app/.env`, and then the pattern `~/share/app/*`, rooted inside the share.
  - Expected: the first pattern does not enter `share`; the second finds `share/app/.env`.
- `test_pattern_rooted_inside_cloud_folder_is_allowed`.
  - Input: the pattern `~/Dropbox/app/*`, rooted inside the cloud folder.
  - Expected: `Dropbox/app/.env` is found.
- `test_online_only_file_is_never_opened`.
  - Input: a matched file for which `is_local` is patched to return false, and `open` patched to fail for it.
  - Expected: no open; the instance's lookups are UNKNOWN with `NOT_LOCAL_NEXT_ACTION`.
- `test_dotenv_pattern_filters_names`.
  - Input: `work/.env`, `work/x/.env.local`, and `work/x/notes.txt`; the pattern `work/**/*`.
  - Expected: the two dotenv files only.
- `test_star_matches_dot_names`.
  - Input: `dir/.env`; the pattern `dir/*`.
  - Expected: `.env` is matched.
- `test_pattern_never_follows_symlinks_or_mounts`.
  - Input: a symlinked directory and a symlinked file, and a directory for which `os.path.ismount` is patched to
    return true.
  - Expected: none of them is matched or descended.
- `test_pattern_budget_marks_incomplete`.
  - Input: `max_entries` 1.
  - Expected: a pseudo-instance with `PatternWalkOutcome.ENTRY_LIMIT`; `dig_gem` for a gem in an unlisted file
    raises `IncompleteGemLookupError`.
- `test_out_of_range_bound_is_rejected`.
  - Input: `max_entries` 0, and `max_entries` above `PATTERN_MAX_ENTRIES`.
  - Expected: `ValueError` naming the key.
- `test_plain_directory_path_is_rejected`.
  - Input: `"kubeconfig": "<tmp>/dir"`, where `dir` is a directory.
  - Expected: `ValueError` naming the target and suggesting `<tmp>/dir/*`.
- `test_missing_root_and_empty_match_are_absent`.
  - Input: a pattern whose root does not exist, and a pattern that matches nothing.
  - Expected: each target appears in `LookupResult.providers` with `ProviderState.ABSENT`; no issue;
    `dig_gem` raises `GemNotFoundError`.
- `test_declared_missing_file_is_absent`.
  - Input: a declared plain dotenv path that does not exist.
  - Expected: listed with `ProviderState.ABSENT`, not dropped.
- `test_pattern_with_backup_is_rejected`.
  - Input: a dotenv pattern with `backup_filename`.
  - Expected: `ValueError` naming the target.
- `test_expanded_and_discovered_file_merge`.
  - Input: a file found both by implicit discovery and by a pattern.
  - Expected: one record, with two evidence entries.
- `test_dotenv_walk_outcomes_keep_todays_messages`.
  - Input: each existing bounded failure of the dotenv walk.
  - Expected: the `DotEnvWalkOutcome` member's value equals the message today's code produces.
- `test_kube_walk_outcomes_keep_todays_messages`.
  - Input: each existing failure of the Kubernetes directory walk and file checks.
  - Expected: the `KubeWalkOutcome` or `KubeFileIssue` member's value equals today's message.
- `test_default_expand_returns_a_copy`.
  - Input: `OnePasswordProvider.expand(settings)` and `KeyringProvider.expand(settings)`, the two providers
    that keep the default.
  - Expected: one dict equal to `settings` but not the same object, for each.
- `test_factory_expand_dispatches_by_provider_name`.
  - Input: `GemProvider.expand("dotenv", {"path": "<tmp>/work/*"})`, and `GemProvider.expand("unknown", {})`.
  - Expected: the same result as `DotEnvProvider.expand` for the first; `ValueError` with
    `"Unknown provider type: 'unknown'"` for the second.
- `test_expansion_reads_no_values`.
  - Input: `open` patched to fail for dotenv files while `expand` runs.
  - Expected: `expand` succeeds.

## Dependencies on other features

- **Requires:**
  - `GAL-targets`, whose translation step calls `expand()` and supplies the target names;
  - `GAL-sdk-optional`, for `ProviderState.ABSENT`. It is delivered earlier in the plan.
- **Required by:** `GAL-kube-contexts`, which splits each expanded kubeconfig file by context. It uses
  `TARGET_NAME_SEPARATOR` in place of its own separator constant, and reads only files `is_local` accepts.
- **Open questions it answers in part:**
  - `GAL-scan-depth`: `depth` and the budgets are the per-target knobs; how a caller selects a profile per call
    stays open.
  - `GAL-scan-masks`: the pattern, `names`, and the cloud-sync guard are the include and exclude sides that exist
    so far; user-defined exclude masks stay open.
- **Independent of `GAL-plugin-contract`:** the hook does not depend on where `put_gem` lives.
