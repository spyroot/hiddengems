# Provider Extensibility and Routing Design

Status: proposal. Nothing in this document is implemented unless the "Current state" section says so.

## Problem as sets

The notation follows `docs/README.md`.

### Sets

- **Providers.** `P = {p₁, …, pₙ}`, with `|P| = N`, is the set of detected and configured providers. `A(o)` is
  the set of their available instances on operating system `o`.
- **Found.** `R(g) = {i ∈ A(o) | i contains g}` is where gem `g` was found, so `R(g) ⊆ A(o)`.
- **Unchecked.** `Unchecked(g) ⊆ A(o)` holds detected instances that could not be checked for `g`: no SDK, a
  rejected prompt, an unreachable cluster, or a time limit.
- **Undetected.** `Undetected` holds instances that store the user's gems but lie outside the detection scope,
  such as a kubeconfig in some other directory. It is never observable.
- **Preferences.** `Pref(g)` is the set of preferences that apply to `g`:
  - explicit: the caller's argument, the per-gem preference with its custom path and location mapping, a
    route;
  - implicit: environment and native-tool defaults, such as `KUBECONFIG`, the current context, and the
    working directory.

The library can only bound where `g` really is:

```text
R(g)  ⊆  true locations of g  ⊆  R(g) ∪ Unchecked(g) ∪ Undetected
```

### Complete and partial preferences

- **Complete.** A preference is complete when it fixes every coordinate of one location: provider, instance,
  and the place inside it (namespace, Secret, key; account, vault, item; file, key). Nothing is unknown, so
  given the N providers we know exactly what the caller asks for. This is the Direct case.
- **Partial.** A partial preference leaves some coordinates unknown. Resolution then checks everything we
  know: it evaluates `R(g) ∩ Pref(g)` over all of `A(o)`.

### Tractable resolution, bounded detection

Once `A(o)` is known, resolution is a finite set operation over N providers, so it is tractable. Building
`A(o)` is the hard part. An exhaustive search for every location of every current and future provider would
scan the whole filesystem and every account. Detection therefore stays bounded:

- each provider inspects a finite, declared set of locations within time and entry limits;
- detection is itself controlled by a preference: a scan profile, conservative by default and deeper when the
  caller asks, plus include and exclude masks on the scan. The masks and per-call profiles stay open questions
  `GAL-scan-depth` and `GAL-scan-masks`; `GAL-expand` answers both in part with per-target `depth`, budgets,
  `names`, and the cloud-sync guard;
- a scan runs once and is cached (`GAL-remember`), so `A(o)` grows across runs without rescanning. The cache
  is invalidated when the caller explicitly asks through the API, for example `refresh=True`, or when an
  invalidation rule from `docs/README.md` fires. Each provider therefore needs an invalidation hook in its
  interface. That hook, `invalidate()`, is declared in [GAL-remember.md](gal/GAL-remember.md);
- whatever stays outside the scope is `Undetected`. Only the user can bring it into `A(o)`, by declaring the
  provider and its location.

### Least user effort

The user should never spend an hour writing JSON. In set terms, the user supplies only what the sets cannot
determine, and only once. This strategy is a proposal derived from the sets above.

- **Cost.** A user decision is needed for gem `g` only when the resolution table does not end in Direct,
  Single, or Preferred. Let `Ambiguous` be the set of requested gems that end elsewhere. Configuration written
  up front costs one entry per target and per preference, whether or not any gem is ambiguous. Asking on
  demand costs at most one decision per member of `Ambiguous`. For the bare-minimum user, `Ambiguous` is
  empty, so the cost is zero.
- **Ask late, ask once.** Resolve on demand. An unresolved lookup returns the candidates, and the caller's one
  pick is saved as a preference (`remember=True`). The same gem does not ask again until its sets change and
  invalidation (`GAL-remember`) reopens it. The library writes the configuration; the user does not.
- **Ask the smallest question.** The question is "which key of `candidates`", never "describe your providers".
- **Authentication prompts count too.** A Keychain password or a 1Password unlock for every request to the same
  gem is user effort. In `GAL-case-repeated-reads`, where `x` is a username and password read by 200 tests,
  that is 200 prompts. The Keychain is worse today: each `dig_gem` reads it twice, in `find_gem` and again in
  `get_gem`, so an item that prompts on every read can prompt up to 400 times. An encrypted cache with a
  re-prompt interval would ask once per interval instead of once per request; `GAL-secure-cache` proposes it in
  [GAL-secure-cache.md](gal/GAL-secure-cache.md).
- **Only decision-relevant unknowns block.** An unchecked instance `u` matters only if adding it to `R(g)`
  could change the decision. With `Pref(g) = ∅`, every `u` matters, because it could add a tie or the only
  match. With an explicit `Pref(g)`, only a `u` inside the preference matters. This is a proposed answer to
  `GAL-unchecked-scope`; how implicit preferences count is still `GAL-explicit-implicit`.
- **Grow detection on a miss.** Detection starts with the conservative profile. Only a Not found result
  justifies looking further. The result can offer a deeper scan profile (`GAL-scan-depth`), or ask the user to
  declare the location. Each expansion is cached until it is invalidated.
- **One answer for a class (option).** Gems with the same found set `R(g)` share one ambiguity. A user who
  answers "prefer `c₁` over `c₂`" once could resolve that whole class. This applies a human decision to gems
  the user has not seen, so it must be explicit and visible in `explain`. It is open question
  `GAL-class-preference`.
- **Bulk setup without hand-writing.** For the advanced user, `config init` (`GAL-cli-init`) writes the
  configuration from detection, and the user edits only what detection got wrong.

Consumption stays easy across the range of users:

- **Bare minimum.** Nothing to configure. Detection finds the defaults, and most lookups land on Single.
- **Advanced.** The user declares targets and preferences. A choice made once is saved with `remember=True`.
- **At scale** (many clusters, providers, and dotenv files). Declared targets and context expansion
  (`GAL-targets`, `GAL-kube-contexts`), remembered detection (`GAL-remember`), and parallel lookup
  (`GAL-parallel`).

### Unknown is a value

A state the library cannot establish is reported as UNKNOWN, together with the `EvidenceSource` it rests on and
a next action. It is never reported as `None`, and never as absence. `None` appears only as "not given" for an
optional argument.

- **An empty result means one thing only:** no provider was detected at all, `A(o) = ∅`
  (`GAL-case-no-provider`). Every other result lists something: a found location, an UNKNOWN instance, or a
  declared target reported as ABSENT.
- **Example.** The 1Password desktop app is present, but neither its CLI nor its SDK is enabled, so `x` may or
  may not be there. The library reports that instance as detected, and `x` there as UNKNOWN, with the next
  action "install or enable the 1Password CLI or SDK, then re-run".
- **The caller decides what to do with it.** If `x` is also found in another provider, the result is Incomplete
  and carries both: the found `x` and the UNKNOWN 1Password instance. A caller that uses the found `x`
  (`choice=`) accepts that 1Password does not matter for this call. That choice is the caller's, not the
  library's.
- **Today this case is silent.** `OnePasswordProvider.detect` returns nothing when it finds neither the CLI nor
  the SDK (`onepassword_provider.py:234-235`), so 1Password counts as absent and nothing is reported. The fix
  is the proposed feature `GAL-onepassword-unknown` in the [Decision register](#9-decision-register).
- **Capabilities follow the same rule.** What a target supports is reported as SUPPORTED, UNSUPPORTED, or
  UNKNOWN, with the `EvidenceSource` it rests on. `GAL-plugin` applies this to the write capability
  (`write_capability`) and `GAL-parallel` to the deadline capability (`deadline_capability`); each returns a
  `CapabilityObservation` with `SUPPORTED`, `UNSUPPORTED`, or `UNKNOWN` and `EvidenceSource.PROVIDER_CLASS`.
- **Today's source still uses `None`, or free text, for a state in these places,** each assigned to the feature
  that replaces it with an explicit value. Lines are at commit `4438234`.

| Where | `None` stands for | Becomes | Feature |
| --- | --- | --- | --- |
| `abstraction.py:109` | declared but not detected | `ProviderState.ABSENT` | `GAL-sdk-optional` |
| `keyring_provider.py:67-74` | Keychain item not found | status enum from `keychain_bridge.h` | Keychain existence |
| `onepassword_provider.py:223` | no CLI, labelled `UNKNOWN` | a known "not found" result | `GAL-onepassword-unknown` |
| `onepassword_provider.py:86,234` | no access path; nothing detected | lookups UNKNOWN | `GAL-onepassword-unknown` |
| `onepassword_provider.py:346` | use the default account | a default-account marker | `GAL-onepassword-unknown` |
| `k8s_provider.py:277-285,452` | bad kubeconfig, skipped | UNKNOWN with a reason | `GAL-kube-contexts` |
| `k8s_provider.py:292-298,459` | malformed contexts list, skipped | UNKNOWN with a reason | `GAL-kube-contexts` |
| `dotenv_provider.py:442-454,481` | declared file missing, skipped | `Presence.ABSENT` | `GAL-expand` |
| `dotenv_provider.py:369-418` | walk outcome as free text | `DotEnvWalkOutcome` | `GAL-expand` |
| `k8s_provider.py:92-203` | walk and file outcomes as free text | `KubeWalkOutcome`, `KubeFileIssue` | `GAL-expand` |
| `dotenv_provider.py:207` | `_DotEnvPathRecord.access_issue`: file readable | `presence: Presence` | `GAL-expand` |
| `dotenv_provider.py:243-248` | `scan_issue`: walk complete | `walk_outcome` | `GAL-expand` |
| `dotenv_provider.py:243-248` | `access_issue`: file readable | `presence: Presence` | `GAL-expand` |
| `k8s_provider.py:74` | `scan_issue`: walk complete | `walk_outcome` | `GAL-expand` |
| `hidden_gems.py:166` | no explicit selection | an explicit scope rule | `GAL-scope` |
| `k8s_provider.py:76` | pseudo-instance with no file | the pseudo-instance marker | first consumer |

  `None` stays only where it means "not given" for an optional argument, such as `criteria=None`, and for a date
  a provider does not expose, which `docs/README.md` says "remains empty". The specification's own names that
  still return `None` for a state are replaced the same way, each in its feature's revision:
  `GemProvider.classify`, `settings_type()`, `selector_type()`, `LookupResult.resolution`,
  `HiddenGems._route_for`, and `terminal_chooser`.

### Cases

Each case names the row of the [resolution table](#45-router-and-resolution) it lands on.

Required cases:

| Name | Sets | Lands on |
| --- | --- | --- |
| `GAL-case-found-in-one` | Two clusters `c₁`, `c₂`; `Pref(x) = ∅`; `R(x) = {c₁}` | Single: return `c₁` |
| `GAL-case-found-in-none` | Two clusters; `Pref(x) = ∅`; `R(x) = ∅` | Not found; maybe in an undetected kubeconfig |
| `GAL-case-found-in-both` | Two clusters; `Pref(x) = ∅`; `R(x) = {c₁, c₂}` | Tie: both returned to the caller |
| `GAL-user-basic` | 1Password and one cluster; `Pref(x) = ∅`; `R(x) = {i}` | Single, unless something is hidden |
| `GAL-user-advanced` | Five clusters in five kubeconfig files | A declared scope, then Preferred or Tie |
| `GAL-hidden-unqueried` | Two clusters; `R(x) = {c₁}`; `Unchecked(x) = {c₂}` | Incomplete |
| `GAL-hidden-undetected` | `x` lies only in `Undetected` | No row sees it; the caller declares it |
| `GAL-case-onepassword-unknown` | 1Password app, no CLI or SDK; `R(x) = {dotenv}` | Incomplete; caller decides |
| `GAL-case-no-provider` | No provider detected at all: `A(o) = ∅` | Not found, with the only empty result |
| `GAL-case-repeated-reads` | `R(x) = {i}`; 200 tests read `x` | Single; one prompt per period with `GAL-secure-cache` |

Additional cases:

| Name | Sets | Lands on |
| --- | --- | --- |
| `GAL-case-complete-preference` | `Pref(x)` fixes cluster, namespace, Secret, and key | Direct |
| `GAL-case-partial-preference` | `Pref(x)` names only a namespace | Preferred, or Tie in preference |
| `GAL-case-stale-preference` | `Pref(x)` points at `c₂`; `R(x) = {c₁}` | Stale; no fallback |

## Abstraction-extension gate

B := existing abstraction contract at the approved baseline revision.
Δ := explicitly approved contract changes.
P := registered concrete provider classes.
C := B with only Δ applied.

MUST extend the existing AbstractGemProvider or an explicitly approved subclass;
MUST NOT introduce a parallel replacement contract or bypass the existing factory.

Every extension MUST declare its signature, behavior, implementation owner,
actual caller, and acceptance tests BEFORE implementation.

PASS only when the resulting abstraction matches C and every p ∈ P satisfies
its applicable contract. Preserve baseline tests except for explicitly approved
changes to their expectations.

The proposed change MUST NOT redefine its own baseline to make the gate pass.

## Gate baseline and register

The owner's gate above is quoted exactly. It binds every feature in the [Feature index](#feature-index). This
section fixes what its symbols denote. The owner approves the baseline revision in this document's pull
request, and no feature proposal changes it.

### Baseline

- **Baseline revision:** commit `4438234`, today's `main`.
- **`B`:** `AbstractGemProvider` in `src/hiddengems/abstract_provider.py`, blob `a0f74364`, with these members:
  - `name: ClassVar[str]`;
  - abstract `__init__(self, **settings: Any) -> None`;
  - abstract classmethod `detect(cls, **options: Any) -> Tuple[DetectedProvider, ...]`;
  - abstract `find_gem(self, name: str, *, criteria: Mapping[str, Any] | None = None) ->
    tuple[GemReference, ...]`;
  - abstract `get_gem(self, reference: GemReference) -> List[Gem]`;
  - abstract `put_gem(self, name: str, value: Gem, *, criteria: Mapping[str, Any] | None = None,
    dry_run: bool = False) -> GemReference`.

  `B` also holds the obligations its docstrings state: detection and lookup return only non-secret metadata,
  and `put_gem` never prints the value or puts it in the reference. The types its members name are those of
  `src/hiddengems/abstraction.py`, blob `f99ec57c`.
- **Not `B`:** `src/hiddengems/abstract_provier.py`, blob `5e152963`, defines a second, older
  `AbstractGemProvider` that nothing imports at `4438234`. No feature adopts it as a baseline. Its removal is
  the owner's decision listed in [GAL-plugin.md](gal/GAL-plugin.md).
- **The existing factory:** `GemProvider` in `src/hiddengems/gem_provider.py`, blob `ec811213`, with
  `provider_types`, `detect(cls, **options: Any) -> Tuple[DetectedProvider, ...]`, and
  `create(cls, record: DetectedProvider, **overrides: Any) -> AbstractGemProvider`. `HiddenGems.__init__`
  detects only through `GemProvider.detect` (`hidden_gems.py:130`) and creates every instance through
  `GemProvider.create` (`hidden_gems.py:146`). A feature bypasses the factory when it detects, creates, or
  dispatches to a provider class by any other path, including a branch on a provider's name.
- **`P` at the baseline,** in `provider_types` order: `OnePasswordProvider`, `DotEnvProvider`,
  `KeyringProvider`, and `KubernetesProvider`. `P` grows only through the factory. `FakeProvider` in
  `tests/test_hidden_gems_routing.py` is a test double injected by patching `GemProvider.create`; it is not in
  `P`.
- **Baseline tests:** the 44 test functions at `4438234`: 28 in `tests/test_hidden_gems_dotenv.py`, 5 in
  `tests/test_hidden_gems_keyring.py`, 2 in `tests/test_hidden_gems_package.py`, and 9 in
  `tests/test_hidden_gems_routing.py`.

### How the gate is judged

- **Matching `C`:** for each member of `AbstractGemProvider` and of each approved subclass, the gate compares
  the name; the kind (class attribute, method, or classmethod); whether it is abstract; the parameter names,
  kinds, and defaults; and the return annotation. Annotations compare as types, so `Tuple[...]` equals
  `tuple[...]` and `List[...]` equals `list[...]`. The import-style rewrite of `GAL-lint-clean` therefore
  leaves `B` unchanged. Docstring wording is not compared; the obligations the docstrings state are.
- **`Δ`:** each feature's proposal lists its contract changes. They join `Δ` only when the owner approves that
  proposal's pull request. Until then they are a request.
- **Explicitly approved subclass:** a subclass of `AbstractGemProvider` is approved together with the feature
  that declares it. Awaiting approval: `WritableGemProvider` (`GAL-plugin`), `DeadlineAwareGemProvider`
  (`GAL-parallel`), and `CachingGemProvider` (`GAL-remember`).
- **Applicable contract of `p`:** `C` restricted to the classes `p` subclasses. An inherited default satisfies
  a member unless the declaring feature names the condition under which `p` must override it.
- **Every feature proposal has a section titled "Abstraction-extension gate"** with these five parts:
  1. one entry per extension, giving its signature, behavior, implementation owner, actual caller, and
     acceptance tests;
  2. the class each extension extends: `AbstractGemProvider`, or a named subclass awaiting approval;
  3. one entry per `p ∈ P`, giving the contract that applies to it and how it satisfies that contract;
  4. the baseline tests whose expectations change, by name, or "none";
  5. a statement that the baseline stays `4438234`.
- **Rejected by the gate:** the first-version `ProviderSpec` protocol in [Provider contract](#42-provider-contract)
  is a parallel replacement contract.

### Register

A feature not listed here has `Δ = ∅`: it leaves `B` and the factory as they are, and the gate still applies to
it. Its pull request shows that every `p ∈ P` still satisfies `B` and that baseline test expectations are kept.

Status values:

- **Declared:** the proposal declares all five parts; implementation waits for the owner's approval.
- **Locked:** no proposal declares the change yet. Nothing of it is implemented until one does and the owner
  approves it.

| Feature | Change to `B` | Change to the factory | Status |
| --- | --- | --- | --- |
| `GAL-plugin` | `put_gem` moves to `WritableGemProvider` | none | Declared |
| `GAL-expand` | classmethod `expand()`, not abstract | `expand()` | Declared |
| `GAL-parallel` | subclass `DeadlineAwareGemProvider` | none | Declared |
| `GAL-remember` | 3 members with defaults; `CachingGemProvider` | `revalidate`, `detection_environment` | Declared |
| `GAL-settings` | `settings_type()` | `check_settings()` | Locked |
| `GAL-selector` | `selector_type()` | none | Locked |
| `GAL-sdk-optional` | `supported_os()`, `required_modules()` | `classify()` | Locked |
| `GAL-discovery` | none | `load_provider_types()`; `types=` | Locked |
| `GAL-verify` | subclass `VerifiableGemProvider` | none | Locked |
| `GAL-secure-cache` | none; a separate cache contract | none | Declared |
| `GAL-return-gate` | none | checks every return of `detect` | Locked |

Owner-mandated exceptions to the no-consumer rule of `software-design.md`: `CachingGemProvider` (`GAL-remember`)
and `AbstractAsyncSecretCache` (`GAL-secure-cache`).

The functions and types that are not members of `AbstractGemProvider`, such as `write_capability` of
`GAL-plugin`, are declared with the same five parts in their feature's proposal.

### Exception consistency

The gate applies to exceptions and warnings in the same way. `E_B` is the set of exception classes the package
defines at `4438234`, found by parsing every module:

| Class | Base | Defined at | Meaning in the baseline |
| --- | --- | --- | --- |
| `ProviderNotAvailableError` | `LookupError` | `abstraction.py:12` | the caller selected an unavailable provider |
| `ProviderLookupError` | `Exception` | `abstraction.py:74` | an instance could not be checked; no absence |
| `ProviderNotApplicable` | `Exception` | `abstraction.py:89` | the instance is outside the partial preference |
| `GemNotFoundError` | `LookupError` | `hidden_gems.py:29` | no checked provider contains the name |
| `ProviderRequiredError` | `ValueError` | `hidden_gems.py:33` | a write needs an explicit provider |
| `AmbiguousGemError` | `LookupError` | `hidden_gems.py:37` | more than one location matches |
| `IncompleteGemLookupError` | `LookupError` | `hidden_gems.py:45` | a detected provider could not be checked |
| `StaleGemPreferenceError` | `LookupError` | `hidden_gems.py:56` | the chosen provider lacks the name |
| `_NativeKeychainError` | `RuntimeError` | `gems/keyring_provider.py:45` | private; becomes `ProviderLookupError` |

The baseline also raises built-in exceptions directly: `ValueError`, `TypeError`, `NotImplementedError` from the
three `put_gem` stubs, and `KeyError`, which escapes `KubernetesProvider.get_gem` for a removed key.

Rules for every feature:

1. **Reuse first.** A situation that a class in `E_B`, or a built-in exception, already names reuses it.
2. **A new class needs a caller that must tell it apart.** The proposal names the closest existing class and
   says why it does not fit.
3. **A new class subclasses what today's handlers catch** for the same situation, so existing `except` clauses
   keep working. A warning subclasses `UserWarning` or `DeprecationWarning`. A warning that announces a one-time
   migration reuses `DeprecationWarning`; a warning that reports a runtime condition while the library keeps
   working gets its own `UserWarning` subclass, so a caller filters it by category.
4. **No secret in an exception.** Messages and attributes never hold a gem value, a token, a password, or key
   material. Gem names, provider names, instance ids, and paths are allowed, as in `E_B` today.
5. **Declared like any extension:** signature, behavior, implementation owner, actual caller, and acceptance
   tests, plus the closest existing class, why it does not fit, and the handlers that still catch it.

**Check:** `test_exception_classes_match_the_register`, in `tests/contract/test_provider_contract.py`. It is
delivered by `GAL-plugin`, the first feature that adds a class. It imports every module under `hiddengems` with
`pkgutil.walk_packages` and collects each class derived from `BaseException` that the package defines, warnings
included. It compares their module, name, and direct bases with an expected set: `E_B` plus the approved
additions. An undeclared class, a changed base, or a missing baseline class fails. A feature that adds a class
adds its entry to that set in its own pull request, where the owner sees it. Each new class also has a case that
an existing handler catches it.

New classes proposed so far:

- **`ProviderNotWritableError(NotImplementedError)`, `GAL-plugin`, declared.** Closest: `ProviderNotAvailableError`,
  which means no provider matched. Here one matched and cannot store. Today's stubs raise `NotImplementedError`,
  so its handlers keep working.
- **`RememberedStoreWarning(UserWarning)`, `GAL-remember`, declared.** A failed save of remembered detection must
  not fail construction, so it is a warning. Its own category lets a caller filter it without matching text.
- **`CacheIntegrityError(ValueError)`, `GAL-secure-cache`, declared.** Closest: `cryptography`'s `InvalidTag`,
  which belongs to one library, while a replacement cipher raises its own. Plain `ValueError` would also hide
  programming errors around decoding. It never leaves the cache.
- **`SecretCacheWarning(UserWarning)`, `GAL-secure-cache`, declared.** Closest: `RememberedStoreWarning`, which
  reports the detection store. Value caching stays separate from it.
- **`InvalidSelectorError(ValueError)`, `GAL-selector`, locked.** Closest: `ProviderNotApplicable`, which means an
  instance is outside a valid preference. A key that no provider declares is an error in the caller's input.
- **`UnknownSettingError(ValueError)`, `GAL-settings`; `ProviderLoadError(RuntimeError)`, `GAL-discovery`;
  `ConfigError(ValueError)`, `GAL-targets`; `ProviderContractError(RuntimeError)`, `GAL-return-gate`:** locked,
  each until its proposal gives the justification above.

Decided reuse, with no new class: a lookup timeout is `ProviderLookupError` with `TIMEOUT_REASON` (`GAL-parallel`);
a plain directory path or a home-rooted tree is `ValueError` (`GAL-expand`); several failed invalidation steps
are one built-in `ExceptionGroup` (`GAL-remember`); `write_capability` with a non-class input is `TypeError`
(`GAL-plugin`); a uniform read error would be `ProviderLookupError` (proposed `GAL-read-errors`); a legacy writer
or a provider that defines `find_gem_within` without its base class warns with `DeprecationWarning` (`GAL-plugin`,
`GAL-parallel`); an `invalidate` override is `TypeError` (`GAL-remember`); a timed-out cache wait is
`IncompleteGemLookupError` (`GAL-secure-cache`).

## Feature index

Each feature has one short name. The same name labels the feature's entry in the
[Delivery plan](#8-delivery-plan), its pull request, its line in [Contract impact](#5-contract-impact) when it
changes a contract, and its entry in the [Specification](#10-specification).

| Name | Delivers | Section |
| --- | --- | --- |
| `GAL-readme-frozen` | Commit gate on `docs/README.md` against the owner's hash | [Specification](#10-specification) |
| `GAL-lint-clean` | `make bless` passes on the tracked tree | [Specification](#10-specification) |
| `GAL-plugin` | `hide_gem` rejects a read-only provider before any call | [GAL-plugin.md](gal/GAL-plugin.md) |
| `GAL-settings` | Unknown settings keys rejected at config load | [Provider contract](#42-provider-contract) |
| `GAL-selector` | Unknown lookup keys raise `InvalidSelectorError` | [Router](#45-router-and-resolution) |
| `GAL-discovery` | Entry-point provider loading with a config allowlist | [Registry](#43-registry-and-loading) |
| `GAL-sdk-optional` | Missing provider SDK reported as `Unsupported`, not an import error | [Registry](#43-registry-and-loading) |
| `GAL-targets` | Named targets in config v2, with v1 migration and JSON Schema | [Configuration](#44-configuration-v2) |
| `GAL-expand` | A declared pattern expands into one target per file, bounded and guarded | [GAL-expand.md](gal/GAL-expand.md) |
| `GAL-scope` | Explicit preference order; a stale preference never widens | [Router](#45-router-and-resolution) |
| `GAL-routing` | Pattern routes from gem names to targets | [Configuration](#44-configuration-v2) |
| `GAL-local-off` | `discovery.local` switch for implicit discovery | [Configuration](#44-configuration-v2) |
| `GAL-kube-contexts` | One kubeconfig expanded into one target per context | [Configuration](#44-configuration-v2) |
| `GAL-write-target` | `hide_gem(target=...)` writes through one named target | [Writes](#46-writes) |
| `GAL-chooser` | Caller picks from a `candidates` dict; `remember=True` saves it | [Router](#45-router-and-resolution) |
| `GAL-explain` | `explain(name)` reports a routing decision without reading values | [Router](#45-router-and-resolution) |
| `GAL-parallel` | Parallel lookup; every provider call stops at the deadline | [GAL-parallel.md](gal/GAL-parallel.md) |
| `GAL-cli-detect` | `detect` command | [Additional pieces](#7-additional-pieces) |
| `GAL-cli-targets` | `targets` command | [Additional pieces](#7-additional-pieces) |
| `GAL-cli-explain` | `explain` command | [Additional pieces](#7-additional-pieces) |
| `GAL-cli-validate` | `config validate` command | [Additional pieces](#7-additional-pieces) |
| `GAL-cli-init` | `config init` command | [Additional pieces](#7-additional-pieces) |
| `GAL-remember` | Remembered detection; `invalidate()` on every provider | [GAL-remember.md](gal/GAL-remember.md) |
| `GAL-verify` | Explicit active verification of one target | [Provider contract](#42-provider-contract) |
| `GAL-cli-verify` | `verify` command | [Additional pieces](#7-additional-pieces) |
| `GAL-masked-value` | `SecretValue` wrapper with masked `repr` and `str` | [Additional pieces](#7-additional-pieces) |
| `GAL-secure-cache` | Encrypted value cache; one prompt per period | [GAL-secure-cache.md](gal/GAL-secure-cache.md) |
| `GAL-secret-service` | Linux Secret Service provider | [Additional pieces](#7-additional-pieces) |
| `GAL-wincred` | Windows Credential Manager provider | [Additional pieces](#7-additional-pieces) |
| `GAL-vault` | HashiCorp Vault KV v2 provider | [Additional pieces](#7-additional-pieces) |
| `GAL-aws-secrets` | AWS Secrets Manager provider | [Additional pieces](#7-additional-pieces) |
| `GAL-aws-ssm` | AWS SSM Parameter Store provider | [Additional pieces](#7-additional-pieces) |
| `GAL-gcp-secrets` | GCP Secret Manager provider | [Additional pieces](#7-additional-pieces) |
| `GAL-azure-keyvault` | Azure Key Vault provider | [Additional pieces](#7-additional-pieces) |
| `GAL-sops` | SOPS-encrypted file provider | [Additional pieces](#7-additional-pieces) |
| `GAL-encrypted-store` | Encrypted local gem store provider | [Additional pieces](#7-additional-pieces) |
| `GAL-pass` | `pass`/`gopass` provider | [Additional pieces](#7-additional-pieces) |
| `GAL-bitwarden` | Bitwarden CLI provider | [Additional pieces](#7-additional-pieces) |

## Layout

The tree is the current tracked layout. A mark in the first column shows the planned change: `+` new, `~`
changed, `-` removed, followed by the feature that makes it. Existing modules keep their roles:
`abstract_provider.py` is the provider contract, `abstraction.py` holds the shared types, `gem_provider.py`
is the registry, and `hidden_gems.py` is the router. A new module appears only for a new responsibility, and
code moved out of an existing function is labelled as a move in its [Specification](#10-specification) entry.
Nothing is rewritten wholesale.

```text
  CMakeLists.txt
~ Makefile                                   GAL-readme-frozen
  README.md
  bless.sh
~ environment.yml                            GAL-targets
~ pyproject.toml                             GAL-discovery, GAL-sdk-optional, GAL-secure-cache (extra cache),
                                             one extra per new provider
~ standards-binding.yaml                     GAL-lint-clean (decision GAL-yaml-start)
  .githooks/
    commit-msg
~   pre-commit                               GAL-readme-frozen
  docs/
    README.md                                frozen by GAL-readme-frozen
+   gal/GAL-expand.md                        GAL-expand proposal
+   gal/GAL-parallel.md                      GAL-parallel proposal
+   gal/GAL-plugin.md                        GAL-plugin proposal
+   gal/GAL-remember.md                      GAL-remember proposal
+   gal/GAL-secure-cache.md                  GAL-secure-cache proposal
    provider-routing-design.md               this document
  lib/
    core/exit_codes.bash
    lint/
+     frozen_docs.bash                       GAL-readme-frozen
      secret_paths.bash
  src/hiddengems/
    __init__.py
+   __main__.py                              GAL-cli-detect
~   abstract_provider.py                     GAL-plugin, GAL-expand, GAL-parallel, GAL-remember
    abstract_provier.py                      removal is a separate decision in GAL-plugin
~   abstraction.py                           GAL-plugin, GAL-settings, GAL-sdk-optional, GAL-expand, GAL-scope,
                                             GAL-chooser, GAL-explain, GAL-parallel, GAL-verify, GAL-remember
+   atomic_file.py                           GAL-chooser (locked file rewrite moved out of dotenv_provider), used by
                                             GAL-remember and GAL-secure-cache
+   canonical_json.py                        GAL-remember
+   chooser.py                               GAL-chooser (prompt moved out of resolve_gem)
+   cli.py                                   GAL-cli-detect, then one subcommand per GAL-cli-* feature
+   config.py                                GAL-targets (config reading moved out of HiddenGems.__init__),
                                             GAL-chooser, GAL-remember
+   constants/__init__.py                    GAL-plugin
+   constants/cache.py                       GAL-secure-cache, only if approved
+   constants/capability.py                  GAL-plugin, GAL-parallel
+   constants/cli.py                         GAL-cli-detect
+   constants/config.py                      GAL-settings, then GAL-discovery, GAL-targets, GAL-expand, GAL-local-off,
                                             GAL-chooser, GAL-remember
+   constants/lookup.py                      GAL-settings, then GAL-chooser, GAL-parallel
+   constants/platform.py                    GAL-sdk-optional, then GAL-expand
+   constants/remember.py                    GAL-remember
+   detection_cache.py                       GAL-remember
~   gem_provider.py                          GAL-settings (check_settings), GAL-discovery, GAL-sdk-optional, GAL-expand,
                                             GAL-remember
~   hidden_gems.py                           GAL-plugin, GAL-expand, GAL-settings, GAL-selector, GAL-targets,
                                             GAL-local-off, GAL-scope, GAL-routing, GAL-chooser, GAL-explain,
                                             GAL-write-target, GAL-parallel, GAL-remember, GAL-verify,
                                             GAL-masked-value, GAL-secure-cache
+   pattern_walk.py                          GAL-expand
+   schemas/gem-provider-config.schema.json  GAL-targets, GAL-remember
+   secret_cache.py                          GAL-secure-cache
+   secret_cache_profiles.py                 GAL-secure-cache
+   secret_value.py                          GAL-masked-value
    gems/
      __init__.py
+     dotenv_constants.py                    GAL-settings, GAL-expand, GAL-chooser
~     dotenv_provider.py                     GAL-plugin, GAL-expand, GAL-settings, GAL-selector, GAL-local-off,
                                             GAL-chooser, GAL-parallel, GAL-remember
~     k8s_provider.py                        GAL-plugin, GAL-expand, GAL-settings, GAL-selector, GAL-sdk-optional,
                                             GAL-local-off, GAL-kube-contexts, GAL-parallel, GAL-remember
~     keychain_bridge.cpp                    GAL-parallel under Keychain option (a), or a proposed Keychain feature
~     keychain_bridge.h                      GAL-parallel under Keychain option (a), or a proposed Keychain feature
~     keychain_reader.cpp                    GAL-parallel under Keychain option (a), or a proposed Keychain feature
      keychain_reader.hpp                changed only by a proposed Keychain feature
+     keychain_constants.py                  GAL-settings, GAL-parallel
      keychainorpasswordread.cpp
~     keyring_provider.py                    GAL-plugin, GAL-settings, GAL-sdk-optional, GAL-parallel, GAL-remember
+     kubernetes_constants.py                GAL-settings, GAL-expand, GAL-kube-contexts, GAL-remember
+     onepassword_constants.py               GAL-settings
~     onepassword_provider.py                GAL-plugin, GAL-settings, GAL-selector, GAL-discovery, GAL-parallel,
                                             GAL-remember
      types.py
+     <name>_provider.py, <name>_constants.py   one pair per new provider
  tests/
+   contract/example_providers.py            GAL-plugin, then GAL-discovery, GAL-expand, GAL-parallel, GAL-remember
+   contract/test_provider_contract.py       GAL-plugin, then GAL-discovery, GAL-expand, GAL-parallel, GAL-remember
+   contract/test_secret_cache_contract.py   GAL-secure-cache
+   fixtures/config/                         GAL-targets
+   frozen_docs.bats                         GAL-readme-frozen
+   test_hidden_gems_cli.py                  GAL-cli-detect
+   test_hidden_gems_config.py               GAL-targets
~   test_hidden_gems_dotenv.py               named in each feature's entry
~   test_hidden_gems_keyring.py              named in each feature's entry
~   test_hidden_gems_package.py              GAL-discovery, GAL-sdk-optional
~   test_hidden_gems_routing.py              named in each feature's entry
+   test_hidden_gems_<name>.py               one per new provider
```

## 1. Goals

- Adding a provider means writing one module and declaring one entry point. No edit to the router, the
  registry tuple, or `HiddenGems`.
- Complex routing is configuration, not code: many Kubernetes clusters and contexts, several 1Password
  accounts, several dotenv files, and per-gem routing rules.
- The invariants in `docs/README.md` hold: passive bounded detection, deterministic results, no silent
  choice among duplicates, explicit provider for writes, and no secret values in metadata, errors, or logs.

Non-goals: caching gem values, remote configuration, and a daemon or server mode. An encrypted cache that
avoids repeated prompts relaxes the first non-goal only for a caller that passes one. It is proposed in
[GAL-secure-cache.md](gal/GAL-secure-cache.md) (see [Contract impact](#5-contract-impact)).

## 2. Current state

```text
HiddenGems.__init__ ── reads ~/.gem_provider.json {providers, preferences}
        │               turns preferences into detection hints
        ▼
GemProvider.detect ──── fixed tuple: onepassword, dotenv, keyring, kubernetes
        │               each provider parses its own **settings dict
        ▼
DetectedProvider records (instance_id strings built per provider)
        │
        ▼
inspect_gem / resolve_gem ── sequential find_gem over every in-scope instance
```

Gaps that block the goals, with evidence:

1. **Closed registry.** `GemProvider.provider_types` in `gem_provider.py` is a hardcoded tuple, and every provider
   module is imported at package import. Importing `hiddengems.hidden_gems` imports the `kubernetes` SDK even
   when no cluster is used.
2. **Untyped settings.** Every provider reads `**settings: Any` and silently ignores unknown keys. A typo such
   as `kubeconfg` is not reported.
3. **Unknown selector keys look like absence.** Each `find_gem` raises `ProviderNotApplicable` for criteria
   keys it does not know. A typo in criteria therefore makes every instance "not applicable", and
   `resolve_gem` reports `GemNotFoundError` or `StaleGemPreferenceError` instead of the typo.
4. **Preferences double as detection hints and name mappings.** `HiddenGems.__init__` strips `provider` and
   `instance_id` from each preference and forwards the rest to `detect()`. There it adds instances, through
   keys each provider interprets differently (`path`, `kubeconfig_paths`, `account_name`). `inspect_gem` passes
   the same keys to `find_gem` as criteria, which map the gem to a native location: Kubernetes `location`
   (`namespace`, `secret_name`, `key`) and 1Password `vault_id`. A Kubernetes preference cannot select a
   non-current context, because `context` is not an accepted criterion.
5. **Kubernetes routing is alias-per-context.** Each `clusters` alias selects one context per kubeconfig. The
   `local` alias is always added, so local kubeconfig discovery cannot be turned off. The fallback namespace
   `default` is a literal.
6. **Detection issues are pseudo-instances.** Bounded-scan failures become records such as
   `dotenv:unverified:<root>` and `kubernetes:unverified:<path>`. This design keeps them.
   A separate issue list gives the same lookup outcome, `IncompleteGemLookupError`, and changing what
   `detect()` returns would break every provider, including one registered by replacing
   `GemProvider.provider_types`. The first feature that must tell a pseudo-instance from a real one, such as
   `GAL-cli-targets` or `GAL-explain`, adds an optional `DetectedProvider.issue` field in its own scope.
7. **Writes cannot target an instance.** `hide_gem` raises `AmbiguousGemError` with an empty result when a
   provider has more than one instance, and it has no parameter to pick one. Only dotenv implements `put_gem`.
8. **Sequential fan-out.** `inspect_gem` calls `find_gem` instance by instance. With N clusters, each checking
   several namespaces, lookup latency adds up.
9. **Scattered constants.** Limits and literals live in each module: scan entries and seconds, kubeconfig
   size, the 1Password CLI timeout of 20 seconds, the SDK `integration_version` string, and the namespace
   `default`.
10. **Spec items not implemented.** These include the `Unsupported` and `Absent` states, OS declaration per
    provider, remembered detection with invalidation, `refresh=True`, and the explicit verification operation.
11. **Stale module.** `abstract_provier.py` is an unimported older copy of `abstract_provider.py` with a
    different interface.
12. **Keychain cannot report a tie.** `keychain_reader.cpp` tries six attribute probes in order, each limited
    to one match, and returns the first value found. Two Keychain items for one name therefore never reach
    `GAL-case-found-in-both`; the provider picks silently. `find_gem` reads the value to test that it exists,
    and `get_gem` reads it again.

## 3. Requirements

Functional:

- Register built-in and third-party providers without changing core code.
- Declare named targets (concrete instances) with typed, validated settings.
- Expand one kubeconfig into many targets (all contexts or a list), passively.
- Route gem names to targets by explicit selection, per-gem preference, or pattern rule.
- Store through one named target.
- Explain a routing decision for a gem without reading its value.

Non-functional:

- Detection stays local and bounded: no network, no prompt, no credential plugin execution.
- Lookup across targets runs concurrently under one time limit per lookup, which every provider call honors.
  Results stay deterministic.
- Optional provider dependencies stay optional. A missing SDK is reported as evidence, not as an `ImportError`.
- Unit tests need no network or real credentials.

Constraints: Python 3.11, standalone package, and `~/.gem_provider.json` in its current shape keeps working.

## 4. Proposed design

### 4.1 Components

```text
            ┌─────────────── ConfigLoader ───────────────┐
 ~/.gem_provider.json ─► parse ─► migrate v1→v2 ─► validate (JSON Schema + typed settings)
            └──────────────────────┬──────────────────────┘
                                   ▼
 entry points ─► ProviderRegistry ─► Detector ─► InstanceCatalog
 (allowlisted)   (lazy import,       (bounded,     (canonical ids, target aliases,
                  OS + deps check)    passive)      pseudo-instances)
                                                        │
 dig_gem / hide_gem / explain ─► Router ─► scope ─► fan-out (parallel, timed) ─► Resolver
                                  │                                           (rules from docs/README.md)
                                  └─ selector validation per provider schema
```

`HiddenGems` stays the public facade. The boxes are roles, not modules; the [Layout](#layout) names the module
that holds each. Routing rules, providers, and configuration can be tested in isolation.

### 4.2 Provider contract

Status: this section is the rejected first version and is not part of the specification. Its replacement keeps
today's method names and splits what a provider MUST and MAY implement; it waits on decision
`GAL-plugin-contract` in the [Decision register](#9-decision-register). The names used only here,
`ProviderSpec`, `DetectionReport`, `TargetSettings`, `Selector`, and `DetectionContext`, are not part of the
specification. `Capability` is specified anew by `GAL-plugin` as a `StrEnum`, extended by `GAL-parallel`. The
[Abstraction-extension gate](#abstraction-extension-gate) rejects `ProviderSpec` as a parallel replacement
contract.

The contract extends `AbstractGemProvider` instead of replacing it. Existing providers keep working while
they move to it.

```python
class ProviderSpec(Protocol):
    kind: ClassVar[str]                          # "kubernetes"
    supported_os: ClassVar[frozenset[OsName]]    # {"linux", "macos", "windows11"}
    requires: ClassVar[tuple[str, ...]]          # importable modules, e.g. ("kubernetes",)
    settings_type: ClassVar[type[TargetSettings]]
    selector_type: ClassVar[type[Selector]]
    capabilities: ClassVar[frozenset[Capability]]  # FIND, READ, WRITE, VERIFY

    @classmethod
    def detect(cls, settings: TargetSettings, context: DetectionContext) -> DetectionReport: ...
    @classmethod
    def expand(cls, settings: TargetSettings) -> tuple[TargetSettings, ...]: ...  # default: (settings,)

    def find(self, name: str, selector: Selector) -> tuple[GemReference, ...]: ...
    def read(self, reference: GemReference) -> list[Gem]: ...
    def write(self, name: str, value: Gem, selector: Selector, *, dry_run: bool) -> GemReference: ...
    def verify(self) -> VerificationResult: ...  # explicit, active; never called by detection
```

- **`settings_type` and `selector_type`** are frozen dataclasses with `from_mapping()`, which rejects unknown keys
  and names the target and key in the error.
- **`DetectionReport`** holds `instances` and `issues`. Issues replace the `unverified` pseudo-instances. The
  router turns every in-scope issue into a `LookupIssue`, so a lookup still ends in `IncompleteGemLookupError`,
  as it does today.
- **`expand()`** is where a provider turns one declared target into several concrete ones, for example one
  per kubeconfig context. It reads only local files.
- **`capabilities`** lets the router reject `hide_gem` against a read-only provider before calling it, and
  lets `explain` report what each target supports.
- **`supported_os` and `requires`** let the registry classify a provider as `Unsupported` before import.
  Platform checks inside `detect()`, as the keyring provider does today, move out.

### 4.3 Registry and loading

- Built-in providers are declared as entry points in `pyproject.toml`, in group `hiddengems.providers`.
  Third-party packages use the same group.
- The registry loads a provider module only when its kind is in scope, and imports its SDK lazily. A missing
  SDK yields state `Unsupported` with evidence naming the module, instead of failing the package import.
- Third-party entry points load only if the configuration allowlists them (`"plugins": {"allow": [...]}`). A
  provider runs inside the caller's process with access to secrets, so installing a package must not be
  enough to activate it.
- Registration order never selects a provider. Order is used only for display and the deterministic merge.

### 4.4 Configuration v2

```json
{
  "version": 2,
  "plugins": {"allow": []},
  "discovery": {"local": true},
  "targets": {
    "prod":  {"provider": "kubernetes", "kubeconfig": "~/.kube/prod.yaml", "context": "prod-admin",
              "namespaces": ["payments", "default"]},
    "stage": {"provider": "kubernetes", "kubeconfig": "~/.kube/config", "context": "stage"},
    "lab":   {"provider": "kubernetes", "kubeconfig": "~/.kube/lab.yaml", "contexts": "*"},
    "team":  {"provider": "onepassword", "account": "team.1password.com", "vault": "Engineering"},
    "env":   {"provider": "dotenv", "path": "./.env"}
  },
  "routes": [
    {"match": "payments-*", "targets": ["prod"], "selector": {"namespace": "payments"}},
    {"match": "lab-*", "targets": ["lab/*"]}
  ],
  "preferences": {
    "openai_api_key": {"target": "team"}
  }
}
```

- **`targets`** names concrete instances. The name is a stable, non-secret alias. It is distinct from the
  canonical instance id (`kubernetes:<path>:<context>`), which the catalog keeps for deduplication. A declared
  target and a locally discovered instance with the same canonical id are merged, and both evidence records
  are kept.
- **`"contexts": "*"`** (or a list) expands `lab` into `lab/<context>` targets, one per context in the
  kubeconfig. The expansion is bounded by `MAX_KUBE_CONTEXTS`. One line covers a cluster fleet.
- **`discovery.local: false`** turns off implicit discovery (`KUBECONFIG`, `~/.kube`, the current directory's
  `.env`). Only declared targets are used.
- **`routes`** are evaluated in declared order, and the first match applies. `match` is a glob on the gem name.
  `targets` may use globs on target names. `selector` carries provider-specific location keys.
- **`preferences`** keeps its current meaning: one gem name maps to one target, optionally with a selector.
- **v1 files** (`providers` and `preferences` with `provider` and `instance_id`) are migrated in memory. The
  file is never rewritten without an explicit command.
- **A JSON Schema** for the file is checked in under `schemas/` and validated by tests against valid and
  invalid fixtures.

### 4.5 Router and resolution

The sets and cases are defined in [Problem as sets](#problem-as-sets). Explicit preferences apply highest first
(`GAL-scope`): the caller's `target=` or `provider=` argument, then the per-gem preference,
then the first route whose `match` covers `g` (`GAL-routing`).

A declared provider brings a part of `Undetected` into `A(o)`: the user names the provider and its location
(`GAL-targets`). The engine loads that provider's code if it is installed (`GAL-discovery`), and reports it
`Unsupported` otherwise (`GAL-sdk-optional`).

Resolution:

| Case | Condition | Decision | Caller resolves by |
| --- | --- | --- | --- |
| Direct | Complete reference given | Read that location | Nothing to resolve |
| Single | `Pref(g) = ∅` and `R(g) = {i}` | Return `i` | Nothing to resolve |
| Preferred | `R(g) ∩ Pref(g) = {i}` | Return `i` | Nothing to resolve |
| Not found | `Pref(g) = ∅` and `R(g) = ∅` | `GemNotFoundError` | Declaring the hidden provider and location |
| Stale | `Pref(g) ≠ ∅` and `R(g) ∩ Pref(g) = ∅` | `StaleGemPreferenceError` | Picking one, or fixing the preference |
| Tie | `Pref(g) = ∅` and `R(g)` has two or more members | All of `R(g)` returned | Picking one |
| Tie in preference | `R(g) ∩ Pref(g)` has two or more members | All of `R(g) ∩ Pref(g)` returned | Picking one |
| Conflict | Explicit and implicit disagree within `R(g)` | Open: `GAL-explicit-implicit` | Picking one candidate |
| Incomplete | An instance in scope is in `Unchecked(g)` | `IncompleteGemLookupError` | Picking one, or fixing access |

- **Incomplete takes precedence.** Every row except Direct assumes that no instance in scope is unchecked.
  Which members of `Unchecked(g)` count as in scope is open question `GAL-unchecked-scope`.
- **No fallback.** A stale preference never widens to a larger scope. The error names the preference or route
  that selected it.
- **`Undetected` stays outside every row.** Single and Not found are correct only for `A(o)`. If the user knows
  `g` also lives somewhere undetected, they declare that provider and location.

Picking a candidate: every unresolved result carries `candidates`, a dict from a stable location key to
non-secret metadata (provider, target, location, and the created and modified dates when the provider has
them). The key comes from the canonical instance identity and the location, never from order. Dates are shown
to the human and never used to decide. Unless `interactive=True` is passed, the call never blocks or prompts.
How the candidates reach the caller, as an exception or a return value and as references or values, is open
question `GAL-return-both`. The example uses today's exceptions, with the result attached; the caller picks a
key and passes it back:

```python
try:
    values = gems.dig_gem("openai_api_key")
except (AmbiguousGemError, IncompleteGemLookupError, StaleGemPreferenceError) as error:
    key = pick(error.result.candidates)  # the human, or the caller's own rule
    values = gems.dig_gem("openai_api_key", choice=key, remember=True)
```

`choice=` reads exactly that location, which makes the call the Direct case. `remember=True` records the
choice as an explicit per-gem preference, so the next call is the Preferred case and nobody writes JSON by
hand (`GAL-chooser`). An optional terminal chooser can be built on the same dict; core code stops calling
`print` and `input`.

Then:

- **Selector validation.** Keys unknown to every provider in scope raise `InvalidSelectorError`. A key known to
  some providers but not others leaves those others `not applicable`, which is today's behavior for valid
  keys.
- **Fan-out.** `find_gem_within()` runs on a thread pool bounded by `LOOKUP_WORKERS`, under one `Deadline` of
  `LOOKUP_TIMEOUT_SECONDS` per lookup, and every provider call stops at it. A provider that runs out of time
  becomes a `LookupIssue`. Results are merged in record order, not completion order, so output is
  deterministic.
- **`explain(name)`** returns the scope rule that fired, the targets in scope, and the selector, plus each
  target's state and capabilities. It reads no value.

### 4.6 Writes

`hide_gem(name, value, target="team")` resolves exactly one target, checks that its provider can write
(decision `GAL-plugin-contract`), then calls `put_gem()`. Passing `provider=` alone works only when that
provider has exactly one instance. With more than one, the error lists the candidate target names instead
of an empty result.

### 4.7 Constants and limits

Constants are grouped by the domain that owns them, in small modules that import no concrete implementation,
as `software-design.md` requires. An earlier version of this section put every constant in one
`hiddengems/constants.py`; that is a global collection of unrelated constants, so it is replaced.

- Every constant is declared with `typing.Final`.
- Class-level limits that exist today (`ClassVar`, such as `DotEnvProvider._MAX_SCAN_ENTRIES`,
  `KubernetesProvider._MAX_AUTO_CONFIG_BYTES`, and `_MAX_DIRECTORY_ENTRIES`) keep their class attribute as the
  read path, initialized from the new module constant. Baseline tests that patch the class attribute
  (`tests/test_hidden_gems_dotenv.py:470`, `tests/test_hidden_gems_routing.py:278,296,297`) keep working
  unchanged.
- Values that can be computed are computed. The 1Password integration version comes from
  `importlib.metadata.version(BUILTIN_DISTRIBUTION)`, replacing the literal `"v0.1.0"`.
- Configuration may lower a limit. Raising one above its constant is rejected.

`src/hiddengems/constants/capability.py`, created with `constants/__init__.py` by `GAL-plugin`; every constant
in it is `Final[str]`:

- `WRITE_SUPPORTED_REASON`, `WRITE_LEGACY_REASON`, `WRITE_UNSUPPORTED_REASON`, `WRITE_CAPABILITY_INPUT_ERROR`,
  `LEGACY_WRITER_WARNING`, and `PROVIDER_NOT_WRITABLE_MESSAGE`, listed in [GAL-plugin.md](gal/GAL-plugin.md)
  (`GAL-plugin`).
- `DEADLINE_SUPPORTED_REASON`, `DEADLINE_LEGACY_REASON`, `DEADLINE_UNSUPPORTED_REASON`,
  `DEADLINE_CAPABILITY_INPUT_ERROR`, and `LEGACY_DEADLINE_WARNING`, listed in
  [GAL-parallel.md](gal/GAL-parallel.md#constants) (`GAL-parallel`).

`src/hiddengems/constants/lookup.py`:

- `LOOKUP_WORKERS: Final[int] = 8`: the most provider calls one lookup runs at once (`GAL-parallel`).
- `LOOKUP_TIMEOUT_SECONDS: Final[float] = 20.0`: the time limit of one lookup, shared by every provider call it
  starts; the value the 1Password CLI call already uses (`GAL-parallel`).
- `TIMEOUT_REASON: Final[str] = "Lookup time limit reached"` and
  `TIMEOUT_NEXT_ACTION: Final[str] = "Check that the target is reachable, then retry"` (`GAL-parallel`).
- `LOOKUP_SHUTDOWN_GRACE_SECONDS`, `LOOKUP_THREAD_PREFIX`, `NO_DEADLINE_REASON`, `NO_DEADLINE_NEXT_ACTION`, and
  `CHILD_START_METHOD`, listed in [GAL-parallel.md](gal/GAL-parallel.md#constants) (`GAL-parallel`).
- `CANDIDATE_KEY_SEPARATOR: Final[str] = "#"`: joins instance id and location in a candidate key
  (`GAL-chooser`).

`src/hiddengems/constants/config.py`:

- `CONFIG_FILE_NAME: Final[str] = ".gem_provider.json"`: the literal in `HiddenGems.__init__` today
  (`GAL-targets`).
- `CONFIG_VERSION: Final[int] = 2` and
  `CONFIG_SCHEMA_RESOURCE: Final[str] = "schemas/gem-provider-config.schema.json"` (`GAL-targets`).
- `CONFIG_FILE_MODE: Final[int] = 0o600`: the mode used when the library writes the file (`GAL-chooser`; also
  used by `GAL-remember`).
- `ENTRY_POINT_GROUP: Final[str] = "hiddengems.providers"` and
  `BUILTIN_DISTRIBUTION: Final[str] = "hiddengems"` (`GAL-discovery`).
- `UNBOUNDED_DEPTH: Final[float] = math.inf` and `UNBOUNDED_DEPTH_WORD: Final[str] = "unbounded"`; the
  setting `"unbounded"` maps to `math.inf`, never to `None` (`GAL-expand`).
- `TARGET_NAME_SEPARATOR: Final[str] = "/"` and
  `EXPANDED_EVIDENCE_DESCRIPTION: Final[str] = "Found by expanding declared target {target}"` (`GAL-expand`).
- `UNKNOWN_PROVIDER_TYPE_MESSAGE: Final[str] = "Unknown provider type: {provider!r}"`: the literal
  `GemProvider.create` raises today, used by `create` and `GemProvider.expand` (`GAL-expand`).
- `CANONICAL_JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")`: the separators of
  `canonical_json.canonical_json` (`GAL-remember`).
- `DISCOVERY_LOCAL_OPTION: Final[str] = "include_local"`: the detection option the router passes to
  providers (`GAL-local-off`).
- `DETECTION_LIFETIME_SECONDS` moves to `constants/remember.py`, with the other `GAL-remember` constants
  listed in [GAL-remember.md](gal/GAL-remember.md#constants).

`src/hiddengems/constants/platform.py`:

- `PLATFORM_OS: Final[Mapping[str, OsName]]`: a `MappingProxyType` of `"Linux"` to `OsName.LINUX`, `"Darwin"`
  to `OsName.MACOS`, and `"Windows"` to `OsName.WINDOWS11`, keyed by `platform.system()`
  (`GAL-sdk-optional`).

`src/hiddengems/constants/cli.py`:

- `EXIT_OK: Final[int] = 0`, `EXIT_BLOCKED: Final[int] = 2`, and `EXIT_USAGE: Final[int] = 64`: the values in
  `lib/core/exit_codes.bash` (`GAL-cli-detect`).

`src/hiddengems/constants/cache.py`, created only if `GAL-secure-cache` is approved:

- `CACHE_DIR_NAME: Final[str] = ".hiddengem"`, `CACHE_DIR_MODE: Final[int] = 0o700`, and
  `CACHE_FILE_MODE: Final[int] = 0o600`: the directory and file modes `~/.ssh` uses.
- `CACHE_PERIOD_SECONDS: Final[int] = 86_400`: the 24-hour default period.
- `CACHE_KEY_BYTES: Final[int] = 32`: an AES-256 key. The nonce length belongs to each cipher adapter;
  [GAL-secure-cache.md](gal/GAL-secure-cache.md#constants) lists the full module.

`src/hiddengems/secret_value.py`:

- `MASK_TEXT: Final[str] = "<hidden>"`: what `repr` and `str` show (`GAL-masked-value`).

`src/hiddengems/gems/kubernetes_constants.py` (`GAL-settings` unless noted):

- `MAX_DIRECTORY_ENTRIES: Final[int] = 64` and `MAX_AUTO_CONFIG_BYTES: Final[int] = 2 * 1024 * 1024`: the
  values of the `KubernetesProvider` class attributes, which stay as the read path.
- `DEFAULT_NAMESPACE: Final[str] = "default"`, `KUBECONFIG_ENV_VAR: Final[str] = "KUBECONFIG"`,
  `DEFAULT_KUBECONFIG: Final[str] = "~/.kube/config"`, `KUBE_DIRECTORY_NAME: Final[str] = ".kube"`, and
  `LOCAL_ALIAS: Final[str] = "local"`: moved literals.
- `MAX_KUBE_CONTEXTS: Final[int] = 64`: the same bound as directory entries (`GAL-kube-contexts`).
- `ALL_CONTEXTS: Final[str] = "*"` (`GAL-kube-contexts`); the separator is `TARGET_NAME_SEPARATOR` of
  `GAL-expand`.

`src/hiddengems/gems/dotenv_constants.py` (`GAL-settings` unless noted):

- `MAX_SCAN_ENTRIES: Final[int] = 1024` and `MAX_SCAN_SECONDS: Final[float] = 0.25`: the values of the
  `DotEnvProvider` class attributes, which stay as the read path.
- `DEFAULT_DOTENV_NAMES: Final[tuple[str, ...]] = (".env", ".env.*")`: the file-name patterns that reproduce
  today's `_is_dotenv_name` (`GAL-expand`).
- `DOTENV_FILE_MODE: Final[int] = 0o600`: the mode `HiddenDotFileGem` writes with, a move of today's literal
  (`GAL-chooser`).

`src/hiddengems/gems/onepassword_constants.py` (`GAL-settings`):

- `CLI_EXECUTABLE: Final[str] = "op"` and `INTEGRATION_NAME: Final[str] = "Hidden Gems"`: moved literals.
- `CLI_TIMEOUT_SECONDS: Final[float] = LOOKUP_TIMEOUT_SECONDS`: replaces the literal `20`.

`src/hiddengems/gems/keychain_constants.py` (`GAL-settings` unless noted):

- `KEYCHAIN_OK: Final[int] = 0`, `KEYCHAIN_NOT_FOUND: Final[int] = 1`, and
  `KEYCHAIN_ABI_VERSION: Final[int] = 1`: moved; they mirror `keychain_bridge.h`.
- Under Keychain option (a), `GAL-parallel` raises `KEYCHAIN_ABI_VERSION` to 2 and adds
  `KEYCHAIN_INTERACTION_NOT_ALLOWED: Final[int]`, the new bridge status, and `KEYCHAIN_LOCKED_NEXT_ACTION`,
  listed in [GAL-parallel.md](gal/GAL-parallel.md).
- `LIBRARY_FILENAME: Final[str] = "libkeychainorpasswordread.dylib"` and
  `LIBRARY_NAME: Final[str] = "keychainorpasswordread"`: moved literals.
- `BUILD_LIBRARY_DIRS: Final[tuple[str, ...]]`, holding `"cmake-build-debug/Debug"`,
  `"cmake-build-release/Release"`, and `"build"`: moved from `_library_candidates`.

`GAL-expand` also adds the pattern budget, cloud-sync guard, and walk-outcome constants and enums listed in
[GAL-expand.md](gal/GAL-expand.md#constants-and-enums).

Each new provider gets `src/hiddengems/gems/<name>_constants.py`. Every environment variable, path, and
executable named in its [Specification](#10-specification) entry becomes a constant there, named
`<WHAT>_ENV_VAR`, `<WHAT>_PATH`, or `<WHAT>_EXECUTABLE`.

### 4.8 Libraries

| Library | Version | Declared as | Introduced by | Import |
| --- | --- | --- | --- | --- |
| `kubernetes` | `>=36.0.3`, today | dependency; an extra if `GAL-kube-extra` | today | lazy from `GAL-sdk-optional` |
| `python-dotenv` | `>=1.2.3`, today | dependency | today | eager |
| `PyYAML` | `>=6.0.3`, today | dependency | today | eager |
| `onepassword` | `>=0.4.1`, today | extra `onepassword` | today | lazy, today |
| `importlib.metadata` | standard library | none | `GAL-discovery` | eager |
| `concurrent.futures` | standard library | none | `GAL-parallel` | eager |
| `fnmatch` | standard library | none | `GAL-expand` | eager |
| `argparse` | standard library | none | `GAL-cli-detect` | command line only |
| `jsonschema` | stated in its proposal | tests only | `GAL-targets` | tests only |
| `cryptography` | `>=50.0`, checked with 50.0.2 | extra `cache` | `GAL-secure-cache`, if approved | lazy |
| `secretstorage` | stated in its proposal | extra `secretservice` | `GAL-secret-service` | lazy |
| `pywin32` | stated in its proposal | extra `wincred` | `GAL-wincred` | lazy |
| `hvac` | stated in its proposal | extra `vault` | `GAL-vault` | lazy |
| `boto3` | stated in its proposal | extra `aws` | `GAL-aws-secrets`, `GAL-aws-ssm` | lazy |
| `google-cloud-secret-manager` | stated in its proposal | extra `gcp` | `GAL-gcp-secrets` | lazy |
| `azure-keyvault-secrets`, `azure-identity` | stated in its proposal | extra `azure` | `GAL-azure-keyvault` | lazy |

- **Version bounds.** Each proposal states the lower bound its acceptance cases were run with; a proposal
  without it stays Locked.
- **Test-only library.** `jsonschema` is not in `environment.yml`; `GAL-targets` adds it there with the bound
  its proposal states.
- **Executables, not Python packages:** `sops` (`GAL-sops`), `pass` or `gopass` (`GAL-pass`), and `bw`
  (`GAL-bitwarden`). They are found on `PATH` during detection and never installed by this package.
- **Development tools** stay where they are declared: `pytest`, `ruff`, and `yamllint` in `environment.yml`;
  `markdownlint-cli2`, `shellcheck`, and `bats` from Homebrew.

## 5. Contract impact

Each change below alters a contract or behavior and requires explicit approval before implementation. Changes
to `AbstractGemProvider` and the factory are also listed in the [Register](#register) of the
[Abstraction-extension gate](#abstraction-extension-gate).

- **`GAL-readme-frozen`: a staged change to `docs/README.md` is blocked unless it matches the owner's hash.**
  Gate, strengthening. The pre-commit hook gains one blocking check; lint findings stay non-blocking. Why:
  `docs/README.md` is the original design document and must stay as it is.
- **`GAL-lint-clean`: no behavior change.** Import-style and formatting fixes only; the test suite passes
  unchanged.
- **`GAL-plugin`: `put_gem` moves from `AbstractGemProvider` to `WritableGemProvider`; `hide_gem` raises
  `ProviderNotWritableError` before calling a read-only provider.** Contract change: relaxation for
  implementers, strengthening for callers. A direct call to a read-only provider's `put_gem` raises
  `AttributeError`. A third-party writer that does not subclass `WritableGemProvider` keeps writing with a
  `DeprecationWarning` under the legacy writer path, or is refused without it.
- **`GAL-sdk-optional`: a supported provider that detected nothing reports `ABSENT`.** Behavior change:
  `LookupResult.providers` showed `None` for it before.
- **`GAL-expand`: `AbstractGemProvider` gains the non-abstract `expand()` hook, dispatched by
  `GemProvider.expand`; declared patterns expand under a guard.** Contract extension. Behavior changes:
  implicit dotenv discovery skips cloud-sync folders and reports the skip; a declared plain path that names a
  directory raises `ValueError` instead of being dropped; a declared location that yields nothing is reported
  ABSENT.
- **`GAL-local-off`: `discovery.local: false` skips implicit discovery.** Relaxation, opt-in: instances
  reachable only through `KUBECONFIG`, `~/.kube`, or the working directory's `.env` are not detected, so
  duplicates there are not reported.
- **`GAL-remember`: `AbstractGemProvider` gains `invalidate()`, `revalidate()`, and `environment_names`, all
  with defaults; `CachingGemProvider` holds the shared cache lifecycle.** Contract extension: `HiddenGems`
  also gains the parameter `refresh` (`HiddenGems(refresh=True)`) and the method `HiddenGems.invalidate()`.
  Behavior change: `HiddenGems` writes the `remembered` key of the config file, and a later `HiddenGems` reuses
  those records without detecting again while they stay current and valid. Relaxation: while a remembered entry
  is reused, an instance created after it was saved is not detected until `refresh=True`, a fingerprint change,
  or the lifetime ends.
- **`GAL-selector`: unknown selector key raises `InvalidSelectorError`.** Behavior change, strengthening. Today the
  result is `GemNotFoundError`, which hides the typo.
- **`GAL-settings`: unknown settings key raises at config load.** Behavior change, strengthening. Today it is
  silently ignored.
- **`GAL-routing`: routes narrow the lookup scope.** Relaxation. For a routed name, targets outside the route are not
  checked, so a duplicate outside the route is not reported. This is the same relaxation an explicit
  `provider=` already makes, but it now comes from configuration. It is opt-in, `explain` shows it, and a
  stale route never falls back.
- **`GAL-parallel`: lookup timeout produces a `LookupIssue`.** Behavior change, strengthening. Today a hung target
  blocks lookup indefinitely, except the 1Password CLI, which has a 20-second limit. Contract extension: the
  subclass `DeadlineAwareGemProvider`, `deadline_capability`, and `Capability.DEADLINE`; `find_gem` and
  `get_gem` keep their signatures. Compatibility choice for the owner: a provider whose `deadline_capability` is
  `UNSUPPORTED` runs in a child process that is terminated at the deadline (default), or is refused in parallel
  lookup.
- **`GAL-discovery`: entry-point allowlist.** Strengthening. Third-party providers do not run unless listed.
  Behavior change: providers load sorted by `name`, so the built-in order becomes dotenv, keyring, kubernetes,
  onepassword.
- **`GAL-chooser`: unresolved results carry `candidates`, and `dig_gem` gains `choice=` and `remember=`.**
  Contract extension; existing calls keep working. `StaleGemPreferenceError` gains the lookup result that
  `AmbiguousGemError` and `IncompleteGemLookupError` already carry. Behavior change: `interactive=True` no
  longer prompts through `input()` in core code.
- **`GAL-write-target`: `hide_gem(target=...)`.** Contract extension. Existing calls keep working, and the
  multi-instance error gains candidate names.
- **`GAL-secure-cache`, specified in [GAL-secure-cache.md](gal/GAL-secure-cache.md): looked-up values held
  encrypted between calls.** Relaxation of the non-goal
  "no caching of gem values": a value would outlive the call that read it. Why: without it, every request to
  the same gem can prompt for authentication again. Where the per-period key lives, and what one
  authentication unlocks, must be settled before this can be approved. A second relaxation, opt-in like the
  first: within a cache period, the caller's earlier choice outlives a duplicate that appears later in another
  provider. `dig_gem` gains `refresh: bool = False`, and `refresh=True` resolves again at once. `HiddenGems`
  gains `cache: AbstractSecretCache = PASS_THROUGH_CACHE`, and a successful `hide_gem` and
  `HiddenGems.invalidate()` empty the cache.
- **`GAL-sdk-optional`: `kubernetes` SDK moves to an extra** (`hiddengems[kubernetes]`). Packaging contract change.
  A plain install no longer pulls the SDK. Open question `GAL-kube-extra`.

## 6. Trade-offs

- **Entry points versus an explicit registry.** Entry points let third-party packages add providers without
  a core release. The costs are import-time discovery and a supply-chain surface, which the allowlist bounds.
- **Typed dataclasses plus a JSON Schema.** These duplicate the field lists. The schema validates the file and
  serves editor tooling, while the dataclasses validate in code. A test asserts that the two agree.
- **Threads versus asyncio.** Provider SDKs are mostly synchronous, and the 1Password SDK runs its own event
  loop. Threads need no provider rewrite. The cost is per-target thread overhead, bounded by the worker limit.
- **Globs versus regular expressions for routes.** Globs cover name prefixes and are hard to get wrong.
  Regular expressions are more powerful, but a wrong pattern routes silently.
- **First-match routes versus a merged rule set.** First match is easy to reason about and to `explain`.
  Overlapping rules must be ordered by hand.

## 7. Additional pieces

Providers, in a suggested order:

1. Linux Secret Service (`GAL-secret-service`, libsecret over D-Bus) and Windows Credential Manager
   (`GAL-wincred`). The spec targets `linux` and `windows11`, and no keyring exists for either.
2. HashiCorp Vault (`GAL-vault`, KV v2).
3. AWS Secrets Manager (`GAL-aws-secrets`) and SSM Parameter Store (`GAL-aws-ssm`).
4. GCP Secret Manager (`GAL-gcp-secrets`) and Azure Key Vault (`GAL-azure-keyvault`).
5. SOPS-encrypted files (`GAL-sops`), and an encrypted local gem store (`GAL-encrypted-store`, named in the
   spec's instance examples).
6. `pass`/`gopass` (`GAL-pass`) and the Bitwarden CLI (`GAL-bitwarden`).

Library pieces:

- **CLI** (`python -m hiddengems`) with these commands, all emitting non-secret JSON:
  - `detect` (`GAL-cli-detect`);
  - `targets` (`GAL-cli-targets`): catalog with states and capabilities;
  - `explain <name>` (`GAL-cli-explain`);
  - `verify <target>` (`GAL-cli-verify`): explicit active check;
  - `config validate` (`GAL-cli-validate`);
  - `config init` (`GAL-cli-init`), which writes a v2 file from detection and is the fastest way to set up
    many clusters.
- **Remembered detection** (`GAL-remember`) with the invalidation rules in `docs/README.md` (`refresh=True`, OS
  or user change, vanished path, detector version, lifetime).
- **A `SecretValue` wrapper** (`GAL-masked-value`) whose `repr` and `str` are masked, so a value cannot be
  logged by accident.
- **A provider contract test kit** in `tests/contract/`, started by `GAL-plugin` (writable and concreteness
  cases), extended by `GAL-discovery` (detection cases), and parametrized over every registered provider. It checks that:
  - detection is passive (socket and subprocess patched to fail);
  - detection is deterministic;
  - no secret appears in `repr`, evidence, or errors;
  - unknown keys are rejected;
  - declared capabilities are honest.

  A new provider is accepted when this suite passes.

## 8. Delivery plan

Each entry is one pull request delivering one observable capability. Its tests, fixtures, schemas, and
documentation ride with it. The pull request title starts with the feature name. A feature blocked in the
[Decision register](#9-decision-register) starts only after that decision. The work runs in phases, and the
entries inside a phase are in delivery order:

1. **Guard.** Protect the original design document and make the existing checks pass.
   1. `GAL-readme-frozen`. After it merges, the owner runs `make install-hooks` once.
   2. `GAL-lint-clean`.
2. **Contract.** Settle what a provider implements before anything builds on it.
   1. `GAL-plugin`. Rides along: its acceptance tests in `tests/contract/test_provider_contract.py`.
   2. `GAL-settings`. Rides along: the constants modules of [Constants and limits](#47-constants-and-limits).
   3. `GAL-selector`.
   4. `GAL-discovery`.
   5. `GAL-sdk-optional`, including the `UNSUPPORTED` and `ABSENT` states.
3. **Configuration.** Declared targets, so each cluster is one entry.
   1. `GAL-targets`. Rides along: the v1 migration and the JSON Schema.
   2. `GAL-expand`.
   3. `GAL-local-off`.
   4. `GAL-kube-contexts`.
4. **Resolution.** The table in [Router and resolution](#45-router-and-resolution).
   1. `GAL-scope`.
   2. `GAL-routing`.
   3. `GAL-chooser`.
   4. `GAL-explain`.
   5. `GAL-write-target`.
   6. `GAL-parallel`.
5. **Command line.**
   1. `GAL-cli-detect`.
   2. `GAL-cli-targets`.
   3. `GAL-cli-explain`.
   4. `GAL-cli-validate`.
   5. `GAL-cli-init`.
6. **Detection memory and verification.**
   1. `GAL-remember`.
   2. `GAL-verify`.
   3. `GAL-cli-verify`.
   4. `GAL-masked-value`.
   5. `GAL-secure-cache`, only if approved.
7. **Providers**, each passing the contract test kit, in this order: `GAL-secret-service`, `GAL-wincred`,
   `GAL-vault`, `GAL-aws-secrets`, `GAL-aws-ssm`, `GAL-gcp-secrets`, `GAL-azure-keyvault`, `GAL-sops`,
   `GAL-encrypted-store`, `GAL-pass`, `GAL-bitwarden`.

## 9. Decision register

Every open point, with its options where there is more than one, a recommended default, and the features it
blocks. A default is a recommendation, not a decision; each entry stays open until the owner decides it.

- **`GAL-plugin-contract`:** where `put_gem` lives, fully specified in [GAL-plugin.md](gal/GAL-plugin.md).
  Options: keep it in `AbstractGemProvider`, which leaves `GAL-plugin` with no capability; or move it to
  `WritableGemProvider`. A separate choice in the same file covers removing `abstract_provier.py`. Default:
  move `put_gem`, and remove the stale module in a separate cleanup. The same file also decides the legacy
  writer path (default: keep it, with a `DeprecationWarning`) and the read errors of `get_gem` (default: keep the
  baseline; the uniform error becomes the proposed `GAL-read-errors`). Blocks: `GAL-plugin` and
  `GAL-write-target`. The hooks this entry listed before now belong to the features that call them.
- **`GAL-expand` choices:** listed in
  [GAL-expand.md](gal/GAL-expand.md#alternatives-for-the-owners-decision). The meaning of a trailing `*`
  (default: standard glob); a declared plain path that names a directory (default: `ValueError`); the
  cloud-sync guard in implicit discovery (default: apply it). Blocks: `GAL-expand`.
- **`GAL-parallel` choices:** listed in
  [GAL-parallel.md](gal/GAL-parallel.md#alternatives-for-the-owners-decision). An `UNKNOWN` provider (default:
  call it in process, with a `DeprecationWarning`); a non-cooperative provider (default: run it in a child
  process); the Keychain (default: query without interactive UI). Blocks: `GAL-parallel`.
- **`GAL-remember` choices:** listed in
  [GAL-remember.md](gal/GAL-remember.md#alternatives-for-the-owners-decision). What `HiddenGems.invalidate()`
  leaves behind (default: detect again); reusing remembered records (default: all or nothing); where the
  remembered detection lives (default: the `remembered` key); how many remembered entries (default: one).
  Blocks: `GAL-remember`.
- **`GAL-explicit-implicit`:** when explicit and implicit preferences select different members of `R(g)`, does
  explicit win, or does the caller decide?
  Options: explicit first; unanimous, or else the caller decides; implicit only as the chooser's default. Default:
  the caller decides, which is the Conflict row. Blocks: the Conflict row of `GAL-scope`.
- **`GAL-unchecked-scope`:** which members of `Unchecked(g)` block a result: only those in the preferred scope,
  or every detected instance?
  Options: every detected instance; only the preferred scope; only decision-relevant instances, as in Least user
  effort. Default: decision-relevant. Blocks: the Incomplete row of `GAL-scope`.
- **`GAL-return-both`:** in a Tie, do the candidates reach the caller as an exception or as a return value, and
  as references (values read after the pick) or as values?
  Default: today's exceptions carrying references; values are read only after the pick, so `dig_gem` keeps its return
  type. Blocks: the Tie path of `GAL-chooser`.
- **`GAL-scan-depth`:** which scan profiles exist besides the conservative default, what each one inspects, and
  where the caller selects one.
  Default: one extra profile, chosen per call, that adds declared roots only. Partly answered by `GAL-expand`:
  `depth` and the walk budgets are per-target settings; selecting a profile per call stays open. Blocks: growing
  detection on a miss, which no feature delivers yet.
- **`GAL-scan-masks`:** how include and exclude masks on the scan are expressed, and how they combine with
  declared targets.
  Default: glob masks under `discovery` in the config, applied after targets. Partly answered by `GAL-expand`:
  the pattern, `names`, and the cloud-sync guard; user-defined exclude masks stay open. Blocks: a new feature,
  named when approved.
- **`GAL-class-preference`:** may one answer resolve every gem with the same found set `R(g)`, and how is that
  shown in `explain`?
  Default: not offered before `GAL-chooser` ships. Blocks: nothing yet.
- **`GAL-secure-cache`:** proposed in [GAL-secure-cache.md](gal/GAL-secure-cache.md), revision 3. The lifecycle
  is settled there: one epoch per period with two random keys (`seal` and `index`), a fixed period of
  `CACHE_PERIOD_SECONDS` that configuration may shorten to no less than `CACHE_MIN_PERIOD_SECONDS`, and
  retirement in the order revoke, destroy key, delete records. Owner choices, with recommendations, in that
  file: key placement (default: `FileCacheKeyStore` in `~/.hiddengem`, `0700` and `0600`), the async contract
  (default: deliver now), a timed-out wait (default: `IncompleteGemLookupError`), a failed refresh (default:
  keep the record), a write through `hide_gem` (default: invalidate the whole cache), and what one
  authentication unlocks (default: one request identity). Blocks: `GAL-secure-cache` and `GAL-encrypted-store`.
- **`GAL-kube-extra`:** should `kubernetes` (and later cloud SDKs) become optional extras?
  Default: yes. Blocks: the packaging part of `GAL-sdk-optional`.
- **`GAL-routing`:** are pattern routes wanted, given that they relax full-scope duplicate detection for routed
  names (see [Contract impact](#5-contract-impact))?
  Default: yes, opt-in. Blocks: `GAL-routing`.
- **`GAL-config-env`:** should an environment variable such as `HIDDENGEMS_CONFIG` override the config path? The
  spec lists environment variables as a detection source.
  Default: no override until asked. Approval would add `CONFIG_ENV_VAR: Final[str] = "HIDDENGEMS_CONFIG"` to
  `constants/config.py`. Blocks: nothing.
- **`GAL-secret-mapping`:** a per-gem preference can already map a gem name to a Kubernetes Secret through
  `location` (`namespace`, `secret_name`, `key`). Open: should routes map whole name patterns the same way, or
  through a naming rule?
  Default: routes carry a static `selector` only. Blocks: name mapping in `GAL-routing`.
- **`GAL-masked-value-api`:** does `dig_gem` always return `SecretValue` objects, or only when asked?
  Options: always, which breaks callers that use the value as a `str`; or opt-in with `masked=True`. Default: opt-in.
  Blocks: `GAL-masked-value`.
- **`GAL-onepassword-unknown`:** proposed feature. When the 1Password desktop app is present but neither the
  CLI nor the SDK is available, detection reports a detected instance whose lookups are UNKNOWN, with the
  next action "install or enable the 1Password CLI or SDK, then re-run", instead of nothing.
  Options: add it to the feature index, with its own proposal listing the app locations it checks per
  operating system; or keep today's silence. Default: add it. Blocks: `GAL-case-onepassword-unknown`.
- **`GAL-yaml-start`:** add `---` to `standards-binding.yaml` to clear the yamllint warning, or leave the
  warning, which does not fail `make bless`.
  Default: add it, as `environment.yml` does. Blocks: nothing; it is part of `GAL-lint-clean`.

Revisit as usage grows: per-provider concurrency limits, an async public API, structured non-secret audit
events, and write support for Kubernetes and 1Password, which today raise `NotImplementedError`.

## 10. Specification

Each entry names the files it changes (`~`), adds (`+`), or removes (`-`); the names it adds, with signatures;
the existing tests it changes, by name; and what blocks it. Every entry is bound by the
[Abstraction-extension gate](#abstraction-extension-gate). Constants are listed in
[Constants and limits](#47-constants-and-limits) and libraries in [Libraries](#48-libraries). Names that exist
today keep their names and signatures. Code moved out of an existing function is labelled as a move.

### GAL-readme-frozen

Status: locked.

- **Files:** `+ lib/lint/frozen_docs.bash`, `~ .githooks/pre-commit`, `~ Makefile`, `+ tests/frozen_docs.bats`.
- **Constants**, `readonly` in `lib/lint/frozen_docs.bash`: `CI_FROZEN_DOC_PATH="docs/README.md"`,
  `CI_FROZEN_HASH_DIR_NAME=".no_edit"`, `CI_FROZEN_HASH_FILE_NAME="hiddengems.doc.hash"`, and
  `CI_FROZEN_HASH_PATTERN='^[0-9a-f]{64}$'`. Exit codes come from `lib/core/exit_codes.bash`.
- **Functions**, each documented with Summary, Arguments, Stdout, Stderr, and Returns:
  - `ci_frozen_doc_sha256`: hashes standard input with `sha256sum`, or `shasum -a 256` when `sha256sum` is
    missing, and prints the 64-character digest. Returns `CI_EXIT_OK`, or `CI_EXIT_BLOCKED` when neither
    tool exists.
  - `ci_frozen_expected_hash`: prints the first whitespace-separated field of
    `${HOME}/.no_edit/hiddengems.doc.hash`, for its caller only. Returns `CI_EXIT_MISSING_FILE` when the file
    is unreadable, and `CI_EXIT_BLOCKED` when the field does not match `CI_FROZEN_HASH_PATTERN`.
  - `ci_block_staged_frozen_doc_change` (`$1`: repository root): returns `CI_EXIT_OK` without reading the hash
    file when `git diff --cached --quiet -- docs/README.md` finds no staged change. A staged deletion or rename
    blocks. Otherwise it compares the digest of the staged blob (`git show :docs/README.md`) with the expected
    hash. Equal passes; a mismatch, a missing file, or a malformed file blocks with `CI_EXIT_BLOCKED`. It never
    prints the expected hash. On a mismatch it prints the staged digest, so the owner can update the hash file.
- **Pre-commit hook:** sources `lib/lint/frozen_docs.bash` and runs
  `ci_block_staged_frozen_doc_change "${repo_root}" || exit $?` before `bless.sh --staged`. Its header comment
  becomes: "Early indicator for lint findings, never a blocker for them. One check blocks: a staged change to
  docs/README.md must match the SHA-256 kept in ~/.no_edit/hiddengems.doc.hash. A blocked commit leaves the
  working tree untouched." Lint findings stay non-blocking.
- **Makefile:** a new target `check-frozen` runs `ci_block_staged_frozen_doc_change` against the index. `help`
  lists it and describes `install-hooks` as activating the frozen-document check.
- **Hash file:** one line whose first field is the 64-character lowercase SHA-256 of `docs/README.md`. The owner
  writes it with `shasum -a 256 docs/README.md > ~/.no_edit/hiddengems.doc.hash`. Only the owner writes it;
  automated tools have no access to `~/.no_edit`.
- **Tests,** in `tests/frozen_docs.bats`, each with a temporary `HOME` and a temporary repository, never the
  real `~/.no_edit`:
  - an unchanged `docs/README.md` passes without reading the hash file;
  - a staged change that matches the hash passes;
  - a staged change with a different hash is blocked;
  - a missing hash file blocks a staged change;
  - a malformed hash file blocks a staged change;
  - a staged deletion is blocked;
  - a staged rename is blocked;
  - the expected hash never appears in the output.
- **Limits:**
  - The check is inactive until `make install-hooks` sets `core.hooksPath`.
  - `git commit --no-verify` skips it, and merges made on the hosting service or by rebase are not checked.
  - CI cannot run it, because the hash exists only on the owner's machine.
  - A process that cannot read `~/.no_edit` fails closed, so it can never commit a change to `docs/README.md`.
- **Libraries:** none; bash, git, and `sha256sum` or `shasum`.
- **Blocked by:** nothing.

### GAL-lint-clean

- **Delivers:** `make bless` passes on the tracked tree.
- **Findings at commit `4438234`:**
  - `ruff check` reports 31 findings: `UP006` 17 times, `UP035` 9 times, and `I001` 5 times; 22 are fixable
    with `--fix`, and the 9 `UP035` are fixed by hand;
  - `ruff format --check` would reformat 9 files;
  - `yamllint` warns once (`document-start`) in `standards-binding.yaml`, which does not fail the run;
  - `markdownlint-cli2` and `shellcheck` are clean.
- **Change:** `ruff check --fix`, the 9 `UP035` fixes by hand, and `ruff format` over `src` and `tests`, plus
  decision `GAL-yaml-start`. No behavior change: the full `pytest` suite passes unchanged before and after.
- **Files:** `~` the nine files `ruff format` rewrites: `src/hiddengems/abstract_provider.py`,
  `abstract_provier.py`, `abstraction.py`, `gem_provider.py`, `hidden_gems.py`, `gems/dotenv_provider.py`,
  `gems/k8s_provider.py`, `gems/keyring_provider.py`, `gems/onepassword_provider.py`; no test file; no new
  files.
- **Libraries:** none new.
- **Blocked by:** nothing.

### GAL-plugin

Specified in [GAL-plugin.md](gal/GAL-plugin.md), revision 8. `put_gem` moves to `WritableGemProvider`, the
read-only providers lose their stubs, and `hide_gem` raises `ProviderNotWritableError` before any call to a
read-only provider. The hooks and shared types of revision 1 moved to the features that call them; that file's
Scope lists where each one went.

- **Blocked by:** `GAL-plugin-contract`.

### GAL-settings

- **Files:** `~ hidden_gems.py`, `~ gem_provider.py`; each provider module gains a settings class;
  `+ constants/lookup.py`, `+ constants/config.py`, and `+ gems/<provider>_constants.py` for the four
  providers, with the moves listed in [Constants and limits](#47-constants-and-limits).
- **Settings classes,** subclasses of `ProviderSettings` whose fields are the configuration keys each
  `detect()` reads today:
  - `DotEnvSettings`: `path`, `backup_filename`, `instances`;
  - `KubernetesSettings`: `clusters`, `kubeconfig_paths`, `context`, `namespace`;
  - `OnePasswordSettings`: `cli`, `account_name`, `instances`;
  - `KeyringSettings`: `library`.
- **Router:** `HiddenGems.__init__` passes each configuration block to
  `GemProvider.check_settings(provider: str, settings: Mapping[str, Any]) -> Mapping[str, Any]`, which finds the
  class by the lookup `GemProvider.create` uses and returns `settings_type().from_mapping(settings)`. The keys
  the router adds itself (`preferences` and `DISCOVERY_LOCAL_OPTION`) are not part of the check.
- **Class-level limits:** `DotEnvProvider._MAX_SCAN_ENTRIES`, `KubernetesProvider._MAX_AUTO_CONFIG_BYTES`, and
  `_MAX_DIRECTORY_ENTRIES` keep their class attribute as the read path, initialized from the new module
  constant, so the baseline tests that patch them keep working unchanged.
- **Existing tests changed:** none; the provider constructors keep their current checks.
- **Blocked by:** nothing.

### GAL-selector

- **Files:** `~ hidden_gems.py`; each provider module gains a selector class.
- **New names:**
  - `InvalidSelectorError(ValueError)` in `hidden_gems.py`, with the attribute `keys: tuple[str, ...]`.
  - `HiddenGems._validate_criteria(self, criteria: Mapping[str, Any], records: Sequence[DetectedProvider]) ->
    None`.
  - Selector classes, subclasses of `ProviderSelector` whose fields are the criteria keys each `find_gem`
    accepts today: `DotEnvSelector` (`path`), `KubernetesSelector` (`cluster`, `kubeconfig_paths`,
    `location`, `namespace`), and `OnePasswordSelector` (`account_name`, `vault_id`). The Keychain provider
    declares none.
- **Rule:** a key that no provider in scope declares raises `InvalidSelectorError` before any lookup. A key that
  some providers declare leaves the others `ProviderNotApplicable`, as today.
- **Existing tests changed:** none; new tests in `tests/test_hidden_gems_routing.py`.
- **Blocked by:** nothing.

### GAL-discovery

- **Files:** `~ gem_provider.py`, `~ hidden_gems.py`, `~ pyproject.toml`.
- **New names in `gem_provider.py`:**
  - `ProviderLoadError(RuntimeError)`, with the attributes `entry_point: str` and `reason: str`. It stays Locked
    until its proposal gives the exception justification.
  - `GemProvider.load_provider_types(cls, allow: Collection[str] = ()) -> tuple[type[AbstractGemProvider], ...]`.
    It reads `importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)`. It always accepts entries from
    `BUILTIN_DISTRIBUTION`, and accepts any other entry only when its name is in `allow`. Each class must be a
    subclass of `AbstractGemProvider`, have no abstract methods left (`inspect.isabstract`), and have a
    non-empty `name`. Two classes with the same `name` are both rejected. It returns the classes sorted by
    `name`, as `docs/README.md` "Detection result" orders by provider type name. Behavior change: the built-in
    order becomes dotenv, keyring, kubernetes, onepassword.
  - `GemProvider.detect` and `GemProvider.create` gain `types: Sequence[type[AbstractGemProvider]] | None =
    None`. `None` means `GemProvider.provider_types`.
- **Moves:**
  - The literal tuple in `GemProvider.provider_types` becomes `load_provider_types()` of the built-ins, run at
    import.
  - The `import_module` workaround for `OnePasswordProvider` is removed.
- **Router:** `HiddenGems.__init__` adds the allowlisted third-party types from `plugins.allow` once
  `GAL-targets` exists. Until then only built-ins load.
- **`pyproject.toml`:** `[project.entry-points."hiddengems.providers"]` with
  `dotenv = "hiddengems.gems.dotenv_provider:DotEnvProvider"`,
  `kubernetes = "hiddengems.gems.k8s_provider:KubernetesProvider"`,
  `onepassword = "hiddengems.gems.onepassword_provider:OnePasswordProvider"`, and
  `keyring = "hiddengems.gems.keyring_provider:KeyringProvider"`.
- **Existing tests changed:** `test_dotenv_provider_is_in_the_production_registry`, which now goes through
  `load_provider_types()`; and `test_wheel_contains_provider_package_and_declared_dependencies`, which also
  asserts the four entry points in the wheel metadata.
- **Blocked by:** `GAL-plugin`, whose `AbstractGemProvider` it validates against and whose `tests/contract/` kit
  it extends.

### GAL-sdk-optional

- **Files:** `~ abstraction.py`, `+ constants/platform.py`, `~ gem_provider.py`, `~ k8s_provider.py`,
  `~ keyring_provider.py`, and `~ pyproject.toml` if `GAL-kube-extra` is approved.
- **New names:**
  - `OsName(StrEnum)` with `LINUX`, `MACOS`, and `WINDOWS11`.
  - `ProviderState.UNSUPPORTED = "unsupported"` and `ProviderState.ABSENT = "absent"`.
  - `GemProvider.classify(cls, provider_type: type[AbstractGemProvider]) -> ProviderObservation | None`. It
    returns an `UNSUPPORTED` observation, with evidence naming the cause, when
    `PLATFORM_OS[platform.system()]` is not in `supported_os()`, or when a module in `required_modules()` has
    no `importlib.util.find_spec`. Otherwise it returns `None`.
- **Moves:**
  - `from kubernetes import client, config` and the `ApiException` import move into `KubernetesProvider._api`
    and `KubernetesProvider.find_gem`.
  - The `platform.system() != "Darwin"` check in `KeyringProvider.detect` moves into `supported_os()`.
- **Behavior change:** in `LookupResult.providers`, a supported provider that detected nothing reports
  `ABSENT` instead of `None`.
- **Existing tests changed:** `test_detect_records_native_library_without_reading_a_gem`, because the platform
  check moved; and `test_wheel_contains_provider_package_and_declared_dependencies`, for the extra marker if
  `GAL-kube-extra` is approved.
- **Blocked by:** the packaging part by `GAL-kube-extra`.

### GAL-targets

- **Files:** `+ src/hiddengems/config.py`, `+ src/hiddengems/schemas/gem-provider-config.schema.json`,
  `~ hidden_gems.py`, `~ environment.yml`, `+ tests/test_hidden_gems_config.py`, and `+ tests/fixtures/config/`
  with `v1-providers.json`, `v2-targets.json`, `invalid-unknown-key.json`, and `invalid-version.json`.
  `environment.yml` gains `jsonschema`, with the bound this feature's proposal states.
- **Move:** the JSON reading and type checks in `HiddenGems.__init__` move to `load_config()`, which
  `HiddenGems.__init__` calls. Reason: version 2 and the v1 migration need one owner.
- **New names in `config.py`:**
  - Frozen dataclasses:
    - `GemConfig`, with the fields `version: int`, `plugins_allow: tuple[str, ...]`,
      `discovery_local: bool`, `targets: Mapping[str, TargetConfig]`, `routes: tuple[RouteConfig, ...]`,
      `preferences: Mapping[str, Any]`, and `providers: Mapping[str, Any]`;
    - `TargetConfig(name: str, provider: str, settings: Mapping[str, Any])`;
    - `RouteConfig(match: str, targets: tuple[str, ...], selector: Mapping[str, Any])`.
  - `ConfigError(ValueError)`. It stays Locked until its proposal gives the justification.
  - `default_config_path() -> Path`, which returns `Path.home() / CONFIG_FILE_NAME`.
  - `load_config(path: Path | None = None) -> GemConfig`.
  - `migrate_v1(document: Mapping[str, Any]) -> GemConfig`.
- **Rules:**
  - A missing file gives an empty `GemConfig` with `version=CONFIG_VERSION`, as today's empty document does.
    Loading never rewrites the file.
  - Each target becomes detection options for its provider. A target and a discovered instance with the same
    canonical `instance_id` merge into one record, keeping both evidence entries.
- **Existing tests changed:** none; the routing tests keep pointing `config_path` at a missing file.
- **Blocked by:** nothing. `GAL-config-env` would only add an override later.

### GAL-expand

Specified in [GAL-expand.md](gal/GAL-expand.md), revision 4. A target expands only when its path is a pattern,
such as `~/clusters/*/kubeconfig`; a plain path names one file. A guard keeps every walk out of home-wide
trees, cloud-sync folders, network mounts, and online-only files. Every declared location that yields nothing
is reported ABSENT, and walk outcomes become enums.

- **Blocked by:** `GAL-targets`, `GAL-sdk-optional`, `GAL-settings`, and `GAL-plugin`.

### GAL-local-off

- **Files:** `~ hidden_gems.py`, `~ k8s_provider.py`, `~ dotenv_provider.py`.
- **Change:**
  - `HiddenGems.__init__` passes `DISCOVERY_LOCAL_OPTION` set to `GemConfig.discovery_local` in each provider's
    detection options.
  - `KubernetesProvider.candidate_clusters` adds the implicit `LOCAL_ALIAS` target only when the option is true.
  - `DotEnvProvider.detect` runs `_discovered_candidates` only when it is true.
- **Existing tests changed:** none; new tests for `false` in the routing and dotenv test files.
- **Blocked by:** `GAL-targets`.

### GAL-kube-contexts

- **Files:** `~ k8s_provider.py`.
- **Change:**
  - A cluster setting `contexts` holds `ALL_CONTEXTS` or a list of context names, as the plural form of today's
    `context`.
  - New `KubernetesProvider._context_candidates(cls, document: Mapping[str, Any], settings: Mapping[str, Any])
    -> tuple[_ContextCandidate, ...]` returns one candidate per selected context, at most `MAX_KUBE_CONTEXTS`.
    Beyond that it adds a pseudo-instance record whose `scan_issue` names the limit, as bounded scans do today.
    `_context_candidate` stays for the single-context case.
  - Each alias becomes the cluster alias, `TARGET_NAME_SEPARATOR`, and the context name.
- **Existing tests changed:** none; new tests in `tests/test_hidden_gems_routing.py`.
- **Blocked by:** `GAL-expand`.

### GAL-scope

- **Files:** `~ abstraction.py`, `~ hidden_gems.py`.
- **New names:**
  - `Resolution(StrEnum)` in `abstraction.py`, with one member per row of the resolution table: `DIRECT`,
    `SINGLE`, `PREFERRED`, `NOT_FOUND`, `STALE`, `TIE`, `TIE_IN_PREFERENCE`, `CONFLICT`, and `INCOMPLETE`.
  - `LookupResult.resolution: Resolution | None = None`.
  - `resolve_decision(matches: Sequence[GemReference], issues: Sequence[LookupIssue], *, preferred: bool,
    complete: bool) -> Resolution`, a module function in `hidden_gems.py` that implements the table.
  - `inspect_gem`, `resolve_gem`, and `dig_gem` gain `target: str | None = None`, which takes precedence over
    `provider=`.
- **Rule:** `resolve_gem` raises the existing exceptions by decision. `NOT_FOUND` raises `GemNotFoundError`,
  `STALE` raises `StaleGemPreferenceError`, `TIE` and `TIE_IN_PREFERENCE` raise `AmbiguousGemError`, and
  `INCOMPLETE` raises `IncompleteGemLookupError`.
- **Existing tests changed,** each now also asserting `resolution`:
  `test_unchecked_onepassword_does_not_silently_return_old_keyring`,
  `test_namespace_only_preference_resolves_unique_cluster`,
  `test_kubernetes_keeps_confirmed_match_when_other_namespace_is_unchecked`, and
  `test_onepassword_preserves_match_when_another_account_is_unchecked`.
- **Blocked by:** `GAL-targets`; the Conflict row by `GAL-explicit-implicit`, and the Incomplete row by
  `GAL-unchecked-scope`.

### GAL-routing

- **Files:** `~ hidden_gems.py`.
- **New names:** `HiddenGems._route_for(self, name: str) -> RouteConfig | None`. It returns the first
  `RouteConfig` whose `match` covers the name under `fnmatch.fnmatchcase`. The route's `targets` patterns select
  target names with the same function, and its `selector` becomes the criteria.
- **Rule:** a route that selects no found location raises `StaleGemPreferenceError` naming the route. It never
  falls back.
- **Existing tests changed:** none; new tests in `tests/test_hidden_gems_routing.py`.
- **Blocked by:** `GAL-scope`, decision `GAL-routing`, and `GAL-secret-mapping`.

### GAL-chooser

- **Files:** `~ abstraction.py`, `~ hidden_gems.py`, `~ config.py`, `+ src/hiddengems/chooser.py`,
  `+ src/hiddengems/atomic_file.py`, `~ gems/dotenv_provider.py`, and `~ gems/dotenv_constants.py`
  (`DOTENV_FILE_MODE`).
- **Move to `atomic_file.py`:** `_exclusive_write_lock` (`dotenv_provider.py:45-78`) and `_rewrite_file`
  (`dotenv_provider.py:80-103`) become `exclusive_write_lock(filename: Path) -> Iterator[None]` and
  `rewrite_file(filename: Path, contents: str, *, mode: int) -> Iterator[Path]`. Today's literal `0o600`
  becomes the `mode` argument, `DOTENV_FILE_MODE` for `HiddenDotFileGem`. `GAL-remember` and
  `GAL-secure-cache` import the module.
- **New names:**
  - `candidate_key(reference: GemReference) -> str` in `abstraction.py`: `reference.instance_id`, then
    `CANDIDATE_KEY_SEPARATOR`, then the location as JSON with sorted keys.
  - The property `LookupResult.candidates -> dict[str, GemReference]`.
  - `resolve_gem` and `dig_gem` gain `choice: str | None = None`, a key of `candidates` that reads exactly that
    location, and `remember: bool = False`, which saves the choice through `save_preference`.
  - `StaleGemPreferenceError.__init__(self, name: str, result: LookupResult | None = None)`, so it carries the
    result like the other two errors.
  - `save_preference(path: Path, name: str, preference: Mapping[str, Any]) -> None` in `config.py`. It writes
    through `atomic_file.rewrite_file(path, contents, mode=CONFIG_FILE_MODE)` under `exclusive_write_lock`.
  - `terminal_chooser(result: LookupResult) -> str | None` in `chooser.py`.
- **Move:** the `print` and `input` loop in `resolve_gem` moves to `terminal_chooser`. `HiddenGems` with
  `interactive=True` keeps working by calling it.
- **Rule:** an unknown `choice` raises `ValueError` naming the key.
- **Existing tests changed:** none; new tests in `tests/test_hidden_gems_routing.py`.
- **Blocked by:** `GAL-scope`; the Tie path by `GAL-return-both`.

### GAL-explain

- **Files:** `~ abstraction.py`, `~ hidden_gems.py`.
- **New names:**
  - `ScopeRule(StrEnum)` with `TARGET`, `PROVIDER`, `PREFERENCE`, `ROUTE`, and `ALL`.
  - Frozen dataclass `RoutingExplanation(name: str, rule: ScopeRule, targets: tuple[str, ...],
    selector: Mapping[str, Any], providers: tuple[ProviderObservation, ...],
    capabilities: tuple[CapabilityObservation, ...])`.
  - `HiddenGems.explain(self, name: str) -> RoutingExplanation`. It calls no `find_gem` and reads no value.
- **Blocked by:** `GAL-scope` and `GAL-plugin`.

### GAL-write-target

- **Files:** `~ hidden_gems.py`.
- **Change:**
  - The signature becomes `hide_gem(self, name, value, *, provider=None, target=None, criteria=None,
    dry_run=False)`. With `target`, it writes to that target.
  - With `provider` alone and more than one instance, `AmbiguousGemError` gains the attribute
    `targets: tuple[str, ...]`, listing the candidates, instead of carrying an empty result.
- **Read-only providers,** as delivered by `GAL-plugin`: under option two, `hide_gem(target=...)` applies the
  same `write_capability` check and raises `ProviderNotWritableError(NotImplementedError)` before any call;
  under option one, a read-only provider raises `NotImplementedError` from `put_gem`, as today.
- **Existing tests changed:** none; new tests in `tests/test_hidden_gems_dotenv.py`.
- **Blocked by:** `GAL-targets` and `GAL-plugin`.

### GAL-parallel

Specified in [GAL-parallel.md](gal/GAL-parallel.md), revision 4. It replaces the earlier entry here, which
bounded only the caller's wait: `Future.result(timeout=...)` does not stop a running call, and
`cancel_futures=True` does not cancel a started one. Each built-in provider honors a per-lookup `Deadline`,
a timeout becomes a `LookupIssue`, matches merge in record order, and shutdown waits only for calls that are
themselves bounded.

- **Blocked by:** `GAL-plugin` and `GAL-settings`.

### GAL-cli-detect

- **Files:** `+ src/hiddengems/__main__.py`, `+ src/hiddengems/cli.py`, `+ src/hiddengems/constants/cli.py`,
  `+ tests/test_hidden_gems_cli.py`.
- **New names:**
  - `main(argv: Sequence[str] | None = None) -> int` and `build_parser() -> argparse.ArgumentParser` in
    `cli.py`.
  - `run_detect(args: argparse.Namespace) -> int`, which prints the records of `GemProvider.detect()` as JSON
    (`json.dumps(..., sort_keys=True)`), with non-secret fields only.
  - `__main__.py` holds only `if __name__ == "__main__": raise SystemExit(main())`.
- **Help:** each subcommand's help shows Summary, Description, Examples, Options, Output modes, and Usage, as
  `documentation.md` requires.
- **Blocked by:** nothing.

### GAL-cli-targets

- **New name:** `run_targets(args: argparse.Namespace) -> int` prints each target with its state and
  capabilities.
- **Blocked by:** `GAL-targets` and `GAL-plugin`.

### GAL-cli-explain

- **New name:** `run_explain(args: argparse.Namespace) -> int` prints `HiddenGems.explain(args.name)`.
- **Blocked by:** `GAL-explain`.

### GAL-cli-validate

- **New name:** `run_config_validate(args: argparse.Namespace) -> int` runs `load_config()` and the JSON Schema
  check. It returns `EXIT_OK`, or `EXIT_BLOCKED` with the first error.
- **Blocked by:** `GAL-targets`.

### GAL-cli-init

- **New name:** `run_config_init(args: argparse.Namespace) -> int` builds a version 2 document from detection and
  prints it.
  - `--write` writes it to `default_config_path()` only when no file exists there.
  - `--force` replaces an existing file.
- **Blocked by:** `GAL-targets`.

### GAL-remember

Specified in [GAL-remember.md](gal/GAL-remember.md), revision 4. `AbstractGemProvider` gains `invalidate()`,
a no-op by default, and `CachingGemProvider` implements the cache lifecycle once for providers that keep
state. `HiddenGems.invalidate()` calls the declared hook on every instance. Detection is remembered in the
config file and reused only while its fingerprint is current and every record revalidates.

- **Blocked by:** `GAL-plugin`, whose `tests/contract/` kit this feature extends.

### GAL-verify

- **Files:** `~ hidden_gems.py`.
- **New name:** `HiddenGems.verify(self, target: str) -> VerificationResult`. A provider that is not a
  `VerifiableGemProvider` gives `ok=False` with the reason. Verification may contact the service; detection
  never calls it.
- **Blocked by:** `GAL-targets`.

### GAL-cli-verify

- **New name:** `run_verify(args: argparse.Namespace) -> int` prints `HiddenGems.verify(args.target)`.
- **Blocked by:** `GAL-verify`.

### GAL-masked-value

- **Files:** `+ src/hiddengems/secret_value.py`, `~ hidden_gems.py`.
- **New names:**
  - Frozen dataclass `SecretValue` with the field `_value: str | bytes` and `repr=False`.
  - `SecretValue.reveal(self) -> str | bytes`.
  - `__repr__` and `__str__` return `MASK_TEXT`.
  - Under the recommended default of `GAL-masked-value-api`, `dig_gem` gains `masked: bool = False`, and with
    `True` each value comes back wrapped.
- **Blocked by:** `GAL-masked-value-api`.

### GAL-secure-cache

Specified in [GAL-secure-cache.md](gal/GAL-secure-cache.md), revision 3. An opt-in `HiddenGems(cache=...)` holds
resolved values encrypted for a fixed period, so repeated `dig_gem` calls for an unchanged request make no
`find_gem` or `get_gem` call. `dig_gem(refresh=True)`, `hide_gem`, and `HiddenGems.invalidate()` end a cached
choice. The cache is a separate contract; `AbstractGemProvider`, the factory, and the providers are unchanged.

- **Blocked by:** `GAL-plugin`, `GAL-parallel`, `GAL-chooser`, `GAL-remember`, `GAL-masked-value`, and decision
  `GAL-secure-cache`.

### New providers

Every new provider follows the same rules:

- It subclasses `AbstractGemProvider` and implements the MUST set of `GAL-plugin`.
- Its module is `gems/<name>_provider.py`, and it adds `gems/<name>_constants.py`.
- It registers an entry point under `ENTRY_POINT_GROUP`, and its extra, in `pyproject.toml`.
- It imports its library lazily.
- It passes `tests/contract/test_provider_contract.py` and adds `tests/test_hidden_gems_<name>.py`.
- Detection never contacts the service.
- Its proposal lists the library calls and the version bound it uses, checked against the library's
  documentation, before the owner approves it.
- It is blocked by `GAL-plugin` and `GAL-discovery`.

| Feature | Class | `name` | Library (extra) |
| --- | --- | --- | --- |
| `GAL-secret-service` | `SecretServiceProvider` | `secretservice` | `secretstorage` (`secretservice`) |
| `GAL-wincred` | `WindowsCredentialProvider` | `wincred` | `pywin32` (`wincred`) |
| `GAL-vault` | `VaultProvider` | `vault` | `hvac` (`vault`) |
| `GAL-aws-secrets` | `AwsSecretsProvider` | `awssecrets` | `boto3` (`aws`) |
| `GAL-aws-ssm` | `AwsSsmProvider` | `awsssm` | `boto3` (`aws`) |
| `GAL-gcp-secrets` | `GcpSecretsProvider` | `gcpsecrets` | `google-cloud-secret-manager` (`gcp`) |
| `GAL-azure-keyvault` | `AzureKeyVaultProvider` | `azurekeyvault` | `azure-keyvault-secrets` (`azure`) |
| `GAL-sops` | `SopsProvider` | `sops` | executable `sops` |
| `GAL-encrypted-store` | `EncryptedStoreProvider` | `encryptedstore` | `cryptography` (`cache`) |
| `GAL-pass` | `PassProvider` | `pass` | executable `pass` or `gopass` |
| `GAL-bitwarden` | `BitwardenProvider` | `bitwarden` | executable `bw` |

Passive detection evidence and target settings. A file named here is checked for existence and never read
during detection.

- **`GAL-secret-service`:** Linux only. Evidence: `DBUS_SESSION_BUS_ADDRESS` is set and `secretstorage` can be
  imported. Settings: `collection`, optional.
- **`GAL-wincred`:** Windows 11 only. Evidence: `win32cred` can be imported. Settings: none.
- **`GAL-vault`:** evidence: `VAULT_ADDR` is set, `~/.vault-token` exists, or `vault` is on `PATH`. Settings:
  `address` and `mount`, the KV version 2 mount point.
- **`GAL-aws-secrets` and `GAL-aws-ssm`:** evidence: `AWS_PROFILE` or `AWS_REGION` is set, or `~/.aws/config` or
  `~/.aws/credentials` exists. Settings: `profile` and `region`; `GAL-aws-ssm` adds `path_prefix`.
- **`GAL-gcp-secrets`:** evidence: `GOOGLE_APPLICATION_CREDENTIALS` is set, or
  `~/.config/gcloud/application_default_credentials.json` exists. Settings: `project`, required.
- **`GAL-azure-keyvault`:** the `azure` extra also installs `azure-identity`. Evidence: `~/.azure` exists or
  `az` is on `PATH`. Settings: `vault_url`, required;
  without it there is no instance.
- **`GAL-sops`:** evidence: `sops` is on `PATH`, and declared files or a `.sops.yaml` in the working directory
  exist. Settings: `paths`.
- **`GAL-encrypted-store`:** evidence: the store file under `~/.hiddengem` exists. Also blocked by
  `GAL-secure-cache`, whose key handling it shares.
- **`GAL-pass`:** evidence: `PASSWORD_STORE_DIR` is set or `~/.password-store` exists, and `pass` or `gopass` is
  on `PATH`. Settings: `store_dir`.
- **`GAL-bitwarden`:** evidence: `bw` is on `PATH`; whether `BW_SESSION` is set, never its value. Settings: none.
