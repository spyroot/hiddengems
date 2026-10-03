# GAL-remember: remembered detection and provider invalidation

Status: proposal, revision 2. Not approved for implementation. The owner decides the choices in
[Alternatives](#alternatives-for-the-owners-decision). The overview of all features is
[provider-routing-design.md](../provider-routing-design.md). This proposal is written to its
[Abstraction-extension gate](../provider-routing-design.md#abstraction-extension-gate).

Revision 2 adds the [Exceptions](#exceptions) section required by the overview's
[exception rules](../provider-routing-design.md#exception-consistency). It also corrects how this feature
relates to `GAL-secure-cache`: that cache sits at the router through its own contract, does not subclass
`CachingGemProvider`, and shares no storage with remembered detection.

## Purpose and observable capability

Detection runs once and is remembered, so a later `HiddenGems` starts without scanning again. The caller can
drop everything remembered, both the stored detection and any state a provider keeps across calls, with one
call.

- **Observable capability:**
  - a second `HiddenGems` built with the same configuration, user, system, environment, and working directory
    reuses the remembered records without calling `GemProvider.detect`;
  - `HiddenGems(refresh=True)` detects again and replaces the remembered entry;
  - `HiddenGems.invalidate()` calls `invalidate()` on every provider instance, forgets the remembered entry,
    and detects again.
- **Today:**
  - `HiddenGems.__init__` detects on every construction (`hidden_gems.py:127-131`). Nothing is remembered.
  - `AbstractGemProvider` has no `invalidate()` (`abstract_provider.py`, blob `a0f74364`).
  - No provider keeps state across calls. Every assignment to an instance attribute in the four provider
    modules is inside `__init__`, and none uses `global`. This was checked by parsing the four modules at
    `4438234`.
  - `EvidenceSource.REMEMBERED` exists (`abstraction.py:38`), and nothing produces it.
  - `docs/README.md` defines remembered detection under "Remembered detection" and "Detection cache
    invalidation".

## Scope

The owner's minimum, in full:

| Contract point | Declared here as |
| --- | --- |
| Existing ABC extension | `AbstractGemProvider.invalidate(self) -> None`; existing members unchanged |
| Default behavior | no operation, for providers with no provider-owned cached state |
| Stateful implementations | subclass `CachingGemProvider`; state outside its store fails the contract kit |
| Existing caller | `HiddenGems.invalidate()` calls the declared hook directly on every instance |
| Shared behavior | `CachingGemProvider` implements the cache lifecycle once; providers supply `_release` |

Added beyond the minimum, each needed by a rule of `docs/README.md`:

- **`revalidate()`,** because "for every remembered provider `r`, detection performs a local revalidation"
  and "valid(r) = provider-specific local evidence still exists". A path check alone misses a context removed
  from a kubeconfig that still exists.
- **`environment_names`,** because remembered detection must be refreshed when "relevant environment
  variables change". Only the provider knows which variables its `detect()` reads.
- **The remembered store and its fingerprint,** because remembered detection must be refreshed when the
  operating system, the user, the explicit configuration, or the detector version changes, or the lifetime
  ends.
- **The working directory in the fingerprint.** `docs/README.md` does not list it. It is needed because dotenv
  discovery walks from the working directory (`dotenv_provider.py:551-552`), so records remembered in one
  directory would be wrong in another.

Out of scope:

- a configuration key that lowers `DETECTION_LIFETIME_SECONDS`: `GAL-targets`, in its schema;
- the secure value cache: `GAL-secure-cache`. It caches in front of provider reads at the router, through its
  own contract, and stores nothing in `~/.gem_provider.json`. It adds one step to `HiddenGems.invalidate`;
- expansion caching: none is needed. Expanded targets (`GAL-expand`) are part of the detection result, so they
  are remembered and invalidated with it.

## Layout

```text
~ src/hiddengems/abstract_provider.py            invalidate, revalidate, environment_names; CachingGemProvider
~ src/hiddengems/abstraction.py                  RecordValidity, Revalidation
+ src/hiddengems/atomic_file.py                  moved from dotenv_provider: exclusive_write_lock, rewrite_file
+ src/hiddengems/constants/remember.py           the constants below
+ src/hiddengems/detection_cache.py              the remembered store
~ src/hiddengems/gem_provider.py                 GemProvider.revalidate, GemProvider.detection_environment
~ src/hiddengems/hidden_gems.py                  refresh=, invalidate(), remembered detection in __init__
~ src/hiddengems/gems/dotenv_provider.py         environment_names; imports the moved helpers
~ src/hiddengems/gems/k8s_provider.py            environment_names; revalidate
~ src/hiddengems/gems/kubernetes_constants.py    revalidation reasons
~ src/hiddengems/gems/keyring_provider.py        environment_names; revalidate
~ src/hiddengems/gems/onepassword_provider.py    environment_names; revalidate
~ tests/contract/example_providers.py            CachingEnvironmentProvider
~ tests/contract/test_provider_contract.py       contract cases C1 to C9
~ tests/test_hidden_gems_routing.py              FakeProvider.invalidate; cases R1 to R15, F1, F2, K1, K2, O1
~ tests/test_hidden_gems_dotenv.py               case D1
~ tests/test_hidden_gems_keyring.py              case Y1
```

`constants/remember.py` and `kubernetes_constants.py` are created by whichever of this feature and
`GAL-settings` lands first.

## Abstraction-extension gate

### 1. Extensions

Each entry gives the signature, behavior, implementation owner, actual caller, and acceptance tests. The case
names are listed under [Acceptance cases](#acceptance-cases).

#### `AbstractGemProvider.invalidate`

- **Signature:** `def invalidate(self) -> None`. Not abstract.
- **Behavior:**
  - drops every value the instance holds across calls, so the next call reads its source again;
  - idempotent: a second call right after the first changes nothing;
  - never uses the network, starts a process, prompts, or reads a gem value;
  - the default body does nothing, and it is correct only for a provider that holds no state across calls.
- **Implementation owner:** `AbstractGemProvider`, the no-operation default; `CachingGemProvider`, the shared
  lifecycle. No built-in provider overrides it.
- **Actual caller:** `HiddenGems.invalidate`, step 1, as `instance.invalidate()` on each value of
  `HiddenGems._instances`. No `getattr`, `hasattr`, or branch on a provider's name.
- **Acceptance tests:** C1, C2, R1, R2.

#### `CachingGemProvider`

- **Signature:** `class CachingGemProvider(AbstractGemProvider)`. It defines no `__init__`. Its members:
  - `def invalidate(self) -> None`, the shared lifecycle;
  - `def _cached(self, key: Hashable, load: Callable[[], T]) -> T`;
  - `def _release(self, key: Hashable, value: object) -> None`, a hook whose default does nothing;
  - `def __init_subclass__(cls, **kwargs: Any) -> None`, which raises `TypeError` with
    `INVALIDATE_OVERRIDE_MESSAGE` when a subclass defines `invalidate` in its own class body.
- **Behavior:**
  - The store belongs to the instance: a `_CacheStore` with the fields `lock: threading.Lock`,
    `entries: dict[Hashable, object]`, and `generation: int`. It is created on first use with
    `self.__dict__.setdefault(CACHE_STORE_ATTRIBUTE, _CacheStore())` and never kept on the class.
  - `_cached` returns the entry for `key` when present. Otherwise it records the generation, calls `load()`
    outside the lock, and stores the result only if the generation has not changed meanwhile. It returns the
    loaded value either way. An exception from `load()` propagates, and nothing is stored.
  - `invalidate`, under the lock, takes every entry, empties the store, and adds one to `generation`. Outside
    the lock it calls `_release(key, value)` for each taken entry in insertion order. It collects every
    exception and, after the last entry, raises
    `ExceptionGroup(CACHE_RELEASE_FAILED_MESSAGE.format(count=len(errors)), errors)`.
  - A subclass supplies only `_release`, and only when a cached value holds a resource that must be closed.
- **Implementation owner:** `CachingGemProvider` in `abstract_provider.py`. At delivery no `p ∈ P` subclasses
  it. Its consumer at delivery is `CachingEnvironmentProvider` in `tests/contract/example_providers.py`. It
  exists because the owner's minimum requires one shared lifecycle for any provider that keeps state; no
  built-in provider does at `4438234`.
- **Actual caller:** `HiddenGems.invalidate` through `AbstractGemProvider.invalidate`; the subclass's own
  lookups call `_cached`.
- **Acceptance tests:** C3, C4, C5, C6.

#### `AbstractGemProvider.revalidate`

- **Signature:** `@classmethod def revalidate(cls, record: DetectedProvider) -> Revalidation`. Not abstract.
- **Behavior:**
  - It checks local evidence only: no network, no process, no prompt, and no gem value. It never returns
    `None`.
  - The default applies these rules to each entry of `record.evidence`:
    - an entry with a `path` that no longer exists gives `INVALID` with `REVALIDATION_PATH_GONE`;
    - an entry with a `path` that exists counts as checked;
    - an entry without a `path` whose source is `CALLER` or `CONFIG` counts as checked, because the config
      digest covers it;
    - any other entry without a `path` is unchecked.
  - The default result is `INVALID` if any entry is invalid; otherwise `UNKNOWN` with
    `REVALIDATION_NO_LOCAL_EVIDENCE` if any entry is unchecked or there are no entries; otherwise `VALID`.
  - `Revalidation.source` is the source of the entry that decided the result, or `EvidenceSource.UNKNOWN` when
    there are no entries.
- **Implementation owner:** `AbstractGemProvider`, the default. Overrides: `KubernetesProvider`,
  `KeyringProvider`, and `OnePasswordProvider`, as listed under [Providers in P](#3-providers-in-p).
- **Actual caller:** `GemProvider.revalidate`, which `HiddenGems.__init__` calls for each remembered record.
- **Acceptance tests:** C8, C9, D1, K1, K2, O1, Y1.

#### `AbstractGemProvider.environment_names`

- **Signature:** `environment_names: ClassVar[frozenset[str]] = frozenset()`.
- **Behavior:** the names of every environment variable that `detect()` reads, on every operating system.
  Each listed variable is non-secret. A provider never lists a credential, and stores no variable's value.
- **Implementation owner:** each `p ∈ P` sets its own set.
- **Actual caller:** `GemProvider.detection_environment`.
- **Acceptance tests:** C7, F2.

#### `GemProvider.revalidate`

- **Signature:** `@classmethod def revalidate(cls, record: DetectedProvider) -> Revalidation`.
- **Behavior:** finds the class in `provider_types` whose `name` equals `record.provider`, by the same lookup
  `GemProvider.create` uses, and returns that class's `revalidate(record)`. A provider that is no longer
  registered gives `Revalidation(RecordValidity.INVALID, EvidenceSource.REMEMBERED,
  REVALIDATION_NOT_REGISTERED)`.
- **Implementation owner:** `GemProvider` in `gem_provider.py`.
- **Actual caller:** `HiddenGems.__init__`, step 3.
- **Acceptance tests:** F1, R8.

#### `GemProvider.detection_environment`

- **Signature:** `@classmethod def detection_environment(cls) -> dict[str, str]`.
- **Behavior:** returns the current value of each name in the union of `environment_names` over
  `provider_types`, for the names that are set, with keys in sorted order. An unset variable is left out, and
  an empty value is kept.
- **Implementation owner:** `GemProvider` in `gem_provider.py`.
- **Actual caller:** `HiddenGems.__init__`, step 2, to build the fingerprint.
- **Acceptance tests:** F2, R7.

#### `RecordValidity` and `Revalidation`

- **Signature,** in `abstraction.py`:
  - `class RecordValidity(StrEnum)` with `VALID = "valid"`, `INVALID = "invalid"`, and `UNKNOWN = "unknown"`;
  - frozen dataclass `Revalidation`, with the fields `validity: RecordValidity`, `source: EvidenceSource`, and
    `reason: str`. The reason is a constant from this feature and never names a gem or a value.
- **Behavior:** the result of one revalidation. `VALID` alone lets a remembered record be used.
- **Implementation owner:** `abstraction.py`.
- **Actual caller:** every `revalidate`, and `HiddenGems.__init__`.
- **Acceptance tests:** C8, C9.

#### `HiddenGems.__init__`, parameter `refresh`

- **Signature:** the keyword-only parameter `refresh: bool = False` is added after `interactive`. Every other
  parameter is unchanged.
- **Behavior:**
  1. With `providers` given, nothing changes: the remembered store is neither read nor written.
  2. Otherwise `__init__` keeps the `detection_options` it builds today, then builds
     `fingerprint = current_fingerprint(detection_options, GemProvider.detection_environment())`.
  3. With `refresh=False`, it calls `load_remembered(document, fingerprint, now=..., lifetime=...)`, where
     `document` is the configuration it has already read. If the status is `CURRENT` and
     `GemProvider.revalidate` returns `VALID` for every record, those records become `self.records`.
  4. In every other case it detects with `GemProvider.detect(**detection_options)`, as today, and then calls
     `save_remembered(path, records, fingerprint, now=...)`.
  5. An `OSError` from `save_remembered` becomes a `RememberedStoreWarning` that names the path and the error
     class. The detected records are used as usual.
- **Implementation owner:** `HiddenGems`.
- **Actual caller:** the caller of the library.
- **Acceptance tests:** R5 to R15.

#### `HiddenGems.invalidate`

- **Signature:** `def invalidate(self) -> None`.
- **Behavior:**
  1. Calls `instance.invalidate()` on every value of `self._instances`, in record order.
  2. Calls `forget_remembered(self._config_path)`.
  3. When the records came from detection, it detects again with the stored detection options, then rebuilds
     `records`, `_unavailable`, and `_states`. It keeps the existing instance of every new record that has the
     same `provider`, `instance_id`, and `settings`, and creates the other instances through
     `GemProvider.create`. It then saves the new records as in `__init__`. Caller-given records are kept, but
     their instances are still invalidated in step 1.
  4. It runs every step, collecting each exception. After step 3 it raises
     `ExceptionGroup(INVALIDATION_FAILED_MESSAGE.format(count=len(errors)), errors)` if any step failed. If
     detection fails, `records`, `_instances`, `_states`, and `_unavailable` stay as they were.
  - It must not run at the same time as another call on the same `HiddenGems`. The class has no lock today,
    and this feature adds none.
- **Implementation owner:** `HiddenGems`.
- **Actual caller:** the caller of the library.
- **Acceptance tests:** R1, R2, R3, R4.

#### Remembered store, `detection_cache.py`

- **Signatures:**
  - `class RememberedStatus(StrEnum)` with `ABSENT = "absent"`, `CURRENT = "current"`, `STALE = "stale"`,
    and `UNREADABLE = "unreadable"`.
  - `class StaleReason(StrEnum)` with `FORMAT_CHANGED = "format_changed"`, `SYSTEM_CHANGED =
    "system_changed"`, `USER_CHANGED = "user_changed"`, `DIRECTORY_CHANGED = "directory_changed"`,
    `CONFIG_CHANGED = "config_changed"`, `ENVIRONMENT_CHANGED = "environment_changed"`,
    `VERSION_CHANGED = "version_changed"`, and `LIFETIME_EXCEEDED = "lifetime_exceeded"`.
  - Frozen dataclass `DetectionFingerprint`, with the fields `system: str`, `user: str`,
    `working_directory: str`, `config_digest: str`, `environment_digest: str`, and `package_version: str`.
  - Frozen dataclass `RememberedLoad`, with the fields `status: RememberedStatus`,
    `records: tuple[DetectedProvider, ...]`, and `reasons: tuple[StaleReason, ...]`.
  - `class RememberedStoreWarning(UserWarning)`.
  - `def current_fingerprint(detection_options: Mapping[str, Any], environment: Mapping[str, str]) ->
    DetectionFingerprint`.
  - `def load_remembered(document: Mapping[str, Any], fingerprint: DetectionFingerprint, *, now: datetime,
    lifetime: timedelta) -> RememberedLoad`.
  - `def save_remembered(path: Path, records: Sequence[DetectedProvider], fingerprint: DetectionFingerprint, *,
    now: datetime) -> None`.
  - `def forget_remembered(path: Path) -> None`.
- **Behavior:**
  - `current_fingerprint` takes `system` from `platform.system()`, `user` from `getpass.getuser()`,
    `working_directory` from `str(Path.cwd().resolve())`, and `package_version` from
    `importlib.metadata.version(BUILTIN_DISTRIBUTION)`. Each digest is the SHA-256 hex digest of
    `json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)`.
  - `load_remembered` never raises for the content of the document:
    - no `REMEMBERED_KEY` gives `ABSENT`;
    - a value of the wrong shape gives `UNREADABLE`;
    - a `format` other than `REMEMBERED_FORMAT` gives `STALE` with `FORMAT_CHANGED`;
    - otherwise each differing fingerprint field adds its reason, and `now - observed_at > lifetime` adds
      `LIFETIME_EXCEEDED`. Reasons follow the order of `StaleReason`. No reason gives `CURRENT`.
    - `records` is filled only for `CURRENT`, and each record gains one evidence entry,
      `DetectionEvidence(EvidenceSource.REMEMBERED, REMEMBERED_EVIDENCE_DESCRIPTION, None, observed_at)`.
  - `save_remembered` writes nothing, and emits `RememberedStoreWarning`, when any record carries `scan_issue`
    in its settings, because an incomplete detection is never remembered. It does the same when a setting
    cannot be encoded. Otherwise it holds `exclusive_write_lock(path)`, reads the file again, sets only
    `REMEMBERED_KEY`, and replaces the file through `rewrite_file`. Every other key is kept. A missing file
    is created with mode `0o600`.
  - **Encoding:** `str`, `int`, `float`, `bool`, `None`, `list`, and `dict` with `str` keys are stored as JSON.
    `Path` becomes `{"$path": "<str>"}`, `tuple` becomes `{"$tuple": [...]}`, and `datetime` becomes
    `{"$datetime": "<ISO 8601>"}`. Decoding restores the same types, so a record compares equal after a round
    trip. Any other type cannot be encoded.
  - `forget_remembered` removes `REMEMBERED_KEY` under the same lock. A missing file or key is not an error.
- **Implementation owner:** `detection_cache.py`.
- **Actual caller:** `HiddenGems.__init__` and `HiddenGems.invalidate`.
- **Acceptance tests:** R5 to R15.

#### Move to `atomic_file.py`

- **Signature:** `_exclusive_write_lock` (`dotenv_provider.py:45-78`) and `_rewrite_file`
  (`dotenv_provider.py:80-103`) move to `src/hiddengems/atomic_file.py` as
  `exclusive_write_lock(filename: Path) -> Iterator[None]` and
  `rewrite_file(filename: Path, contents: str) -> Iterator[Path]`. Their bodies are unchanged.
- **Behavior:** unchanged. `HiddenDotFileGem.hide_gem` (`dotenv_provider.py:169,182,189`) imports them.
- **Implementation owner:** `atomic_file.py`.
- **Actual caller:** `HiddenDotFileGem.hide_gem`, `save_remembered`, and `forget_remembered`.
- **Acceptance tests:** the existing dotenv write tests, unchanged; R11.

### 2. What each extension extends

- `invalidate`, `revalidate`, and `environment_names` extend `AbstractGemProvider` with defaults. No member of
  `B` changes.
- `CachingGemProvider` is a subclass of `AbstractGemProvider`, awaiting the owner's approval with this
  proposal.
- `GemProvider.revalidate` and `GemProvider.detection_environment` extend the existing factory. No other code
  dispatches on a provider's class or name.

### 3. Providers in P

| Provider | `environment_names` | `invalidate` | `revalidate` |
| --- | --- | --- | --- |
| `OnePasswordProvider` | listed below | inherited no-op | override |
| `DotEnvProvider` | `HOME`, `USERPROFILE` | inherited no-op | inherited default |
| `KeyringProvider` | listed below | inherited no-op | override |
| `KubernetesProvider` | `HOME`, `KUBECONFIG`, `USERPROFILE` | inherited no-op | override |

- **`invalidate`:** each of the four inherits the no-op, which is correct because each holds no state across
  calls. Their instance attributes are assigned only in `__init__`: `onepassword_provider.py:85-88`,
  `dotenv_provider.py:242-264`, `keyring_provider.py:132-134`, and `k8s_provider.py:71-80`. Case C2 keeps it
  so. `KeyringProvider._native` is the native library loaded from the `library` setting, not cached data.
  The module-level `_RESOURCE_FILES` (`keyring_provider.py:32`) keeps the packaged library file on disk while
  the process runs, and holds no lookup data. Neither is dropped.
- **`environment_names`,** measured at `4438234` by running each `detect()` under a recording environment
  with `platform.system()` set to `Darwin`, `Linux`, and `Windows`, and with no `op` on `PATH`. `USERPROFILE`
  and `PATHEXT` are added because `Path.home()` and `shutil.which` read them on Windows:
  - `OnePasswordProvider`: `ChocolateyInstall`, `HOME`, `HOMEBREW_PREFIX`, `LOCALAPPDATA`, `PATH`, `PATHEXT`,
    `ProgramFiles`, `ProgramFiles(x86)`, `SCOOP`, and `USERPROFILE`;
  - `DotEnvProvider`: `HOME` and `USERPROFILE`, read when a declared path starts with `~`;
  - `KeyringProvider`: `DYLD_FALLBACK_FRAMEWORK_PATH`, `DYLD_FALLBACK_LIBRARY_PATH`, `DYLD_FRAMEWORK_PATH`,
    `DYLD_IMAGE_SUFFIX`, `DYLD_LIBRARY_PATH`, and `HOME`. `ctypes.util.find_library` reads the `DYLD_*`
    variables;
  - `KubernetesProvider`: `HOME`, `KUBECONFIG`, and `USERPROFILE`.
- **`revalidate` overrides:**
  - `KubernetesProvider`: the default first. When it gives `VALID` and `record.settings["context"]` is a
    string, it parses the kubeconfig with `_kubeconfig_document(path, {})` (`k8s_provider.py:274-285`). No
    document gives `UNKNOWN` with `REVALIDATION_KUBECONFIG_UNREADABLE`. A context name missing from its
    `contexts` list gives `INVALID` with `REVALIDATION_CONTEXT_GONE`. Otherwise the result is `VALID`.
  - `KeyringProvider`: `VALID` when `_NativeKeychain(record.settings["library"])` loads, the same check
    `detect()` makes (`keyring_provider.py:146-150`). `AttributeError` or `OSError` gives `INVALID` with
    `REVALIDATION_LIBRARY_GONE`. This covers the record found through `ctypes.util.find_library`, which has no
    path.
  - `OnePasswordProvider`: `VALID` when every CLI evidence path passes `_is_executable`
    (`onepassword_provider.py:178-192`) and, if `record.settings["sdk_installed"]` is true,
    `find_spec("onepassword")` finds the module. A failed CLI check gives `INVALID` with
    `REVALIDATION_PATH_GONE`; a missing SDK gives `INVALID` with `REVALIDATION_SDK_GONE`.
  - `DotEnvProvider` inherits the default. Its evidence path is the dotenv file (`dotenv_provider.py:460-464`).

### 4. Baseline tests whose expectations change

None. Two baseline tests detect without caller records, and both keep their expectations:

- `test_empty_config_detects_conventional_dotenv_and_resolves_key` gains a `remembered` key in its temporary
  `gem_provider.json`. It builds one `HiddenGems`, so detection still runs once.
- `test_detection_runs_once_and_list_valued_gem_stays_nested` gains a `missing.json` in its `tmp_path`. Its
  one record has no evidence, so it is never reused, and `len(calls) == 1` still holds.

`FakeProvider` gains an `invalidate(self) -> None` method and an optional `invalidate_error` argument. Both are
additions that no baseline test calls.

### 5. Baseline

The baseline stays commit `4438234`. This proposal changes no member of `B` and does not redefine `B`.

## Exceptions

One new class, and four reuses, under the overview's exception rules:

- **New: `RememberedStoreWarning(UserWarning)`,** in `detection_cache.py`.
  - Emitted when the remembered entry cannot be saved, or is deliberately not saved: an `OSError` on write, a
    `scan_issue` record, or a setting that cannot be encoded. Construction continues with the detected records.
  - Needed because a caller, or a test, must filter this warning without matching its text. Remembered
    detection is an optimization; losing it must never fail a lookup, so it is a warning and not an error.
  - Closest existing classes: none in `E_B` is a warning. Plain `UserWarning` cannot be filtered apart from other
    libraries' warnings.
  - Message and attributes name the path, the error class, and the provider and instance id. They never
    include a setting's value.
  - Cases: R9, R12, R14.
- **Reused: built-in `ExceptionGroup`,** raised by `HiddenGems.invalidate` and `CachingGemProvider.invalidate`
  after every step has run. It carries each original exception unchanged, which no single class can.
- **Reused: `TypeError`,** raised by `CachingGemProvider.__init_subclass__` when a subclass overrides
  `invalidate`. A wrong class definition is a type error.
- **Reused: `OSError`,** from the file system, caught in `HiddenGems.__init__` and turned into the warning above.
- **Not raised:** `load_remembered` returns a status for every content problem, and `revalidate` returns a
  `Revalidation`; neither raises for a remembered entry that is missing, malformed, or stale.

## Constants

`src/hiddengems/constants/remember.py`:

- `DETECTION_LIFETIME_SECONDS: Final[int] = 86_400`, 24 hours, moved here from the overview's
  `constants/config.py` listing;
- `REMEMBERED_KEY: Final[str] = "remembered"`, the config key `docs/README.md` assigns to remembered
  detection;
- `REMEMBERED_FORMAT: Final[int] = 1`;
- `REMEMBERED_EVIDENCE_DESCRIPTION: Final[str] = "Remembered from an earlier detection"`;
- `CACHE_STORE_ATTRIBUTE: Final[str] = "_hiddengems_cache_store"`;
- `INVALIDATE_OVERRIDE_MESSAGE: Final[str] = "{cls} must not override invalidate; override _release instead"`;
- `CACHE_RELEASE_FAILED_MESSAGE: Final[str] = "Releasing {count} cached value(s) failed"`;
- `INVALIDATION_FAILED_MESSAGE: Final[str] = "Invalidation failed in {count} step(s)"`;
- `REVALIDATION_PATH_GONE: Final[str] = "Remembered evidence path no longer exists"`;
- `REVALIDATION_NO_LOCAL_EVIDENCE: Final[str] = "Remembered record has no local evidence to check"`;
- `REVALIDATION_NOT_REGISTERED: Final[str] = "Remembered provider is no longer registered"`;
- `REVALIDATION_LIBRARY_GONE: Final[str] = "Remembered native library no longer loads"`;
- `REVALIDATION_SDK_GONE: Final[str] = "Remembered Python SDK is no longer importable"`.

`src/hiddengems/gems/kubernetes_constants.py`:

- `REVALIDATION_CONTEXT_GONE: Final[str] = "Remembered context is no longer in the kubeconfig"`;
- `REVALIDATION_KUBECONFIG_UNREADABLE: Final[str] = "Remembered kubeconfig could not be read"`.

`BUILTIN_DISTRIBUTION` is the constant of `GAL-discovery` in `constants/config.py`. Whichever of the two
features lands first adds it.

## Libraries

None. `threading`, `hashlib`, `json`, `getpass`, `platform`, `importlib.metadata`, and `warnings` are in the
standard library.

## Behavior and compatibility

- **Contract extension.** `AbstractGemProvider` gains three members, each with a default. Every existing
  provider, including a third-party one, keeps working unchanged.
- **Behavior change: `HiddenGems` writes the config file.** Without caller records, construction adds or
  replaces the `remembered` key of `~/.gem_provider.json`, creating the file with mode `0o600` when missing.
  `docs/README.md` allows this file to remember detection. Every other key is kept.
- **Behavior change: a later `HiddenGems` may not call `detect()`.** It does so only while the fingerprint
  matches, the lifetime has not passed, and every record revalidates `VALID`. Remembered evidence stays the
  weakest source, as `docs/README.md` requires: a record that fails revalidation is never reported as
  available, and one failure triggers a full detection.
- **A different working directory detects again,** and replaces the one remembered entry.
- **Gates:** unchanged.

## Alternatives for the owner's decision

- **What `HiddenGems.invalidate()` leaves behind:**
  - (a) detect again and rebuild, keeping unchanged instances. Recommended: after the call, the object
    reports the system as it is now.
  - (b) only invalidate and forget, keeping the old records until a new `HiddenGems` is built.
- **Reusing remembered records:**
  - (a) all or nothing: one record that is not `VALID` triggers a full detection. Recommended: the factory needs
    no new way to detect a subset of providers.
  - (b) per provider: detect again only the providers whose records failed. This needs a factory change,
    `GemProvider.detect` with `only: Collection[str]`.
- **Where the remembered detection lives:**
  - (a) the `remembered` key of `~/.gem_provider.json`. Recommended: it is where `docs/README.md` puts it.
  - (b) a separate `~/.hiddengem/remembered.json`, mode `0o600`, which leaves the user's file untouched.
- **How many remembered entries:**
  - (a) one, replaced when the fingerprint changes. Recommended: it is the simplest, and detection is bounded.
  - (b) one per working directory, at most a fixed number, so work in two projects does not detect again on
    every switch.

## Acceptance cases

Every case uses `tmp_path` and a patched home directory, never the real `~/.gem_provider.json`. No case reads a
real secret. None has been run, because nothing is implemented.

`tests/contract/test_provider_contract.py`, extending the kit of `GAL-plugin`:

- **C1** `test_default_invalidate_is_an_idempotent_no_op`: for each `p ∈ P`, an instance built from a recorded
  detection compares equal in `vars()` before and after two `invalidate()` calls.
- **C2** `test_provider_state_is_assigned_only_in_init`: parsing each `p`'s module finds no assignment to a
  `self` attribute outside `__init__`, no `setattr` call, and no `global` statement.
- **C3** `test_caching_provider_invalidate_empties_the_store_and_releases_entries`: two cached keys; after
  `invalidate()`, `_release` saw both in insertion order, and the next `_cached` call loads again.
- **C4** `test_caching_provider_discards_a_load_that_finishes_after_invalidate`: a `load` that waits on an
  event while `invalidate()` runs returns its value, which is not stored.
- **C5** `test_caching_provider_rejects_an_invalidate_override`: defining a subclass with its own `invalidate`
  raises `TypeError` with `INVALIDATE_OVERRIDE_MESSAGE`.
- **C6** `test_caching_provider_release_failures_raise_one_group_after_all_entries`: two of three `_release`
  calls raise; all three run, the store is empty, and one `ExceptionGroup` holds the two errors.
- **C7** `test_detect_reads_only_declared_environment_names`: for each `p ∈ P` and each of `Darwin`, `Linux`,
  and `Windows`, the names that `detect()` reads from a recording `os.environ` are a subset of
  `environment_names`.
- **C8** `test_revalidate_returns_a_revalidation_for_every_provider`: for each `p ∈ P`, `revalidate` on a
  recorded detection returns a `Revalidation`, never `None`.
- **C9** `test_default_revalidate_rules`: an existing path gives `VALID`, a removed path `INVALID`, an
  `OS_FACILITY` entry without a path `UNKNOWN`, a `CONFIG` entry without a path `VALID`, and no evidence
  `UNKNOWN`.

`tests/test_hidden_gems_routing.py`:

- **R1** `test_invalidate_calls_every_provider_hook_once_in_record_order`.
- **R2** `test_invalidate_runs_every_hook_and_raises_one_group_after_failures`: the first of three hooks raises;
  the other two still run, the entry is forgotten, and one `ExceptionGroup` holds the error.
- **R3** `test_invalidate_keeps_unchanged_instances_and_creates_changed_ones`: one record unchanged and one
  with new settings; the first instance is the same object, and the second is new.
- **R4** `test_invalidate_keeps_caller_records`: with `providers=` given, `records` is unchanged and the hooks
  ran.
- **R5** `test_remembered_detection_skips_detect_on_next_construction`: a patched `GemProvider.detect` is called
  once across two constructions; the second's records carry `EvidenceSource.REMEMBERED`.
- **R6** `test_refresh_detects_again_and_replaces_the_entry`.
- **R7** `test_each_fingerprint_change_marks_the_entry_stale`, parametrized over the members of `StaleReason`,
  each produced by changing one input.
- **R8** `test_one_record_failing_revalidation_forces_live_detection`.
- **R9** `test_incomplete_detection_is_not_remembered`: a detection with a `scan_issue` record writes nothing.
- **R10** `test_unreadable_entry_detects_live_and_overwrites`.
- **R11** `test_store_keeps_other_keys_and_file_mode`: `providers` and `preferences` survive the write, and the
  file mode is `0o600`.
- **R12** `test_unwritable_store_warns_and_keeps_detection`: a read-only directory gives
  `RememberedStoreWarning`, and the lookup still works.
- **R13** `test_store_contains_no_gem_value`: after `dig_gem` returns `old-fake-token`, the file text does not
  contain it.
- **R14** `test_unserializable_settings_are_not_remembered`: a callable in settings writes nothing and warns.
- **R15** `test_remembered_records_round_trip_equal`: records from all four built-in `detect()` methods, on
  recorded inputs, compare equal after a save and a load.
- **F1** `test_factory_revalidate_dispatches_by_provider_name_and_reports_unregistered`.
- **F2** `test_factory_detection_environment_lists_only_set_declared_names`.
- **K1** `test_kubernetes_revalidate_reports_a_removed_context_invalid`: the kubeconfig file still exists.
- **K2** `test_kubernetes_revalidate_reports_an_unreadable_kubeconfig_unknown`.
- **O1** `test_onepassword_revalidate_checks_cli_and_sdk_evidence`.

`tests/test_hidden_gems_dotenv.py`:

- **D1** `test_dotenv_revalidate_reports_a_deleted_file_invalid`.

`tests/test_hidden_gems_keyring.py`:

- **Y1** `test_keyring_revalidate_requires_the_library_to_load`, with `_NativeKeychain` patched as the
  existing keyring tests do.

## Dependencies on other features

- **Blocked by:** `GAL-plugin`, whose `tests/contract/` kit and example providers this feature extends.
- **Shares constants with:** `GAL-discovery` (`BUILTIN_DISTRIBUTION`) and `GAL-settings`
  (`kubernetes_constants.py`).
- **Used by:** `GAL-chooser`, whose `remember=True` writes through `atomic_file.py`, and `GAL-secure-cache`,
  which adds a step to `HiddenGems.invalidate` and writes its files through `atomic_file.rewrite_file`.
