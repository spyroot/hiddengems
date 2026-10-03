# GAL-parallel: bounded parallel lookup

Status: proposal, revision 6. Not approved for implementation. The owner decides the choices in
[Alternatives](#alternatives-for-the-owners-decision). The overview of all features is
[provider-routing-design.md](../provider-routing-design.md).

Revision 6 answers review findings GPA3 and GPA4. GPA3: under non-cooperative option (b), a value that pickle
cannot send from the child, such as an IO stream, makes `_get_in_child` raise
`ProviderLookupError(CHILD_TRANSPORT_REASON, CHILD_TRANSPORT_NEXT_ACTION)`, and the owner decides between that
declared rejection and a mode-0600 temporary file. GPA4, Kubernetes: the known limit is replaced by a bounded
read, `_read_secret_within`, which reads the body in chunks and checks the deadline before each one. GPA4, dotenv:
the read of a declared dotenv file on an unresponsive mount, which cannot be bounded in a thread, becomes an owner
decision between a weaker contract for that case and a read in a bounded child for a file on another device.

Revision 5 aligns this proposal with the second round of decisions. `_resolve_within` takes no `remember`;
`resolve_gem` and `dig_gem` apply it after `_resolve_within` returns. `LOOKUP_TIMEOUT_SECONDS` comes from
`GAL-settings`, which also moves `KEYCHAIN_ABI_VERSION`; Keychain option (a) raises it to 2, and `keyring_provider.py`
keeps the names the keyring tests read. The 1Password SDK fallback uses its own `ONEPASSWORD_SDK_NO_DEADLINE_*`
constants, both declarations of `KEYCHAIN_INTERACTION_NOT_ALLOWED` give the value 3, and under Keychain option (a)
no lookup shows a Keychain prompt. `GAL-scope` and `GAL-chooser` are listed as requirements, and the router's gate
entries name their acceptance tests.

Revision 4 aligns names, modules, and dependencies with the other feature proposals:

- `LEGACY_DEADLINE_WARNING` and the new `deadline_capability` reason constants live in
  `constants/capability.py`, which `GAL-plugin` creates.
- `deadline_capability` takes `provider_type: type`. `UNKNOWN` requires both `find_gem_within` and
  `get_gem_within` to resolve. The reused `DeprecationWarning` is listed under [Exceptions](#exceptions).
- One deadline per lookup: `inspect_gem` and `resolve_gem` delegate to the new private `_inspect_within` and
  `_resolve_within`, and `dig_gem` shares one deadline between resolution and the read. The read chooses by
  `deadline_capability`.
- A lookup with no records skips the thread pool.
- Non-cooperative option (b) is specified as `_find_in_child` and `_get_in_child`, which build the instance through
  `GAL-discovery`.
- Keychain option (a) names its native files and constants and raises `KEYCHAIN_ABI_VERSION` from 1 to 2. Under
  Keychain option (b), `KeyringProvider` is `UNSUPPORTED`.
- If the SDK test fails, an SDK-only 1Password instance raises `ONEPASSWORD_SDK_NO_DEADLINE_REASON` under a finite
  deadline.
- The Purpose states the two known limits, `GAL-secure-cache` is listed as a dependent, and the baseline trace notes
  that patched class limits keep working.

Revision 3 corrects revision 2, whose claim that no baseline test changes did not hold:

- `deadline_capability` raises `TypeError` only for an input that is not a class. Any class, including the
  routing suite's `FakeProvider`, which does not subclass `AbstractGemProvider`, is classified.
- An `UNKNOWN` provider, one that defines `find_gem_within` without the base class, runs in process with a
  `DeprecationWarning`, mirroring the legacy writer path of `GAL-plugin`. Only an `UNSUPPORTED` provider takes
  the non-cooperative path.
- With an unbounded deadline, every built-in makes exactly today's calls. Arguments that exist only for a
  bound are passed only when the deadline is finite. Two baseline tests fake those calls with exact
  signatures.
- Each baseline routing test is traced through the new path under
  [Baseline tests whose expectations change](#4-baseline-tests-whose-expectations-change).
- The Kubernetes and SDK limits are stated as facts and acceptance conditions.

Revision 2 added three things:

- the [Abstraction-extension gate](#abstraction-extension-gate) section;
- the [Exceptions](#exceptions) section required by the overview's
  [exception rules](../provider-routing-design.md#exception-consistency);
- an injected clock in `Deadline`, so a test can control time without sleeping. `GAL-secure-cache` uses the
  same `Deadline`.

## Purpose and observable capability

A lookup checks its providers in parallel, never runs more calls at once than `LOOKUP_WORKERS`, and finishes
within `LOOKUP_TIMEOUT_SECONDS` plus `LOOKUP_SHUTDOWN_GRACE_SECONDS`. Every provider call it starts also stops
within that bound, except, under option (a) of the [dotenv decision](#alternatives-for-the-owners-decision), the one
known limit under Built-in deadline handling: the read of a declared dotenv file on an unresponsive mount, which the
lookup waits for. Under option (b), a declared file whose device differs from the home directory's is read in a
bounded child, and the lookup returns at the deadline plus grace. A provider that runs out of time becomes a
`LookupIssue`, exactly like a provider that could not be checked today. The merged result does not depend on which
call finishes first.

- **Today:** `HiddenGems.inspect_gem` calls `find_gem` on each record in turn (`hidden_gems.py:237-260`), with
  no overall deadline.
  - The 1Password CLI call has its own 20-second limit (`onepassword_provider.py:295-300`).
  - The 1Password SDK runs through `asyncio.run` with no limit (`onepassword_provider.py:443,477`).
  - Kubernetes reads a Secret per namespace with no request timeout (`k8s_provider.py:552,596`).
  - The Keychain query can wait on an authorization prompt for as long as the user leaves it open
    (`keychain_reader.cpp:61`).
- **Why the bound must be on execution, not on waiting:**
  - `Future.result(timeout=...)` limits how long the caller waits, not how long the call runs.
  - `Executor.shutdown(cancel_futures=True)` cancels only calls that have not started.
  - A call that is already running keeps running.

  So each provider call must stop by itself at its deadline, or run where it can be stopped.

## Scope

In scope:

- a per-lookup deadline;
- a deadline-aware provider interface, and the capability that reports it;
- deadline handling in the four built-in providers;
- the parallel router and its shutdown;
- the acceptance cases, as extensions of the existing test suites.

Out of scope:

- the explicit-versus-implicit and unchecked-scope decisions (`GAL-explicit-implicit`, `GAL-unchecked-scope`);
- the runtime check of provider returns (proposed `GAL-return-gate`).

## Layout

```text
~ src/hiddengems/abstraction.py                  Deadline; Capability.DEADLINE
~ src/hiddengems/abstract_provider.py            DeadlineAwareGemProvider; deadline_capability
+ src/hiddengems/child_runner.py                 run_in_child, under non-cooperative option (b) or dotenv option (b)
~ src/hiddengems/constants/capability.py         deadline reason constants; LEGACY_DEADLINE_WARNING
~ src/hiddengems/constants/lookup.py             the lookup constants below
~ src/hiddengems/hidden_gems.py                  _inspect_within, _resolve_within; one deadline per lookup;
                                                 bounded dig_gem read; _find_in_child, _get_in_child, which wrap
                                                 run_in_child, under non-cooperative option (b)
~ src/hiddengems/gems/dotenv_provider.py         find_gem_within, get_gem_within; _read_dotenv_file; per the dotenv
                                                 decision
~ src/hiddengems/gems/k8s_provider.py            find_gem_within, get_gem_within; bounded _read_secret_within
~ src/hiddengems/gems/kubernetes_constants.py    KUBE_READ_SLICE_SECONDS, KUBE_READ_CHUNK_BYTES
~ src/hiddengems/gems/onepassword_provider.py    find_gem_within, get_gem_within; CLI and SDK budgets
~ src/hiddengems/gems/onepassword_constants.py   ONEPASSWORD_SDK_NO_DEADLINE_* constants
~ src/hiddengems/gems/keyring_provider.py        find_gem_within, get_gem_within; per the Keychain decision
~ src/hiddengems/gems/keychain_constants.py      Keychain option (a): KEYCHAIN_* constants
~ src/hiddengems/gems/keychain_bridge.h          Keychain option (a): interaction-not-allowed status
~ src/hiddengems/gems/keychain_bridge.cpp        Keychain option (a)
~ src/hiddengems/gems/keychain_reader.cpp        Keychain option (a): query without interactive UI
~ tests/test_hidden_gems_routing.py              FakeProvider gains delay and cooperation; new cases
~ tests/contract/example_providers.py            a provider that ignores deadlines
~ tests/contract/test_provider_contract.py       deadline contract cases
```

## Names, signatures, errors, and call sites

`src/hiddengems/abstraction.py`:

- **New: frozen dataclass `Deadline(expires_at: float, clock: Callable[[], float] = time.monotonic)`,** an
  absolute instant on `clock`, which must be monotonic. Tests inject a controlled clock.
  - `@classmethod after(cls, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> Deadline`,
    with `expires_at = clock() + seconds`.
  - `@classmethod unbounded(cls) -> Deadline`, with `expires_at = math.inf`. Never `None`.
  - `remaining(self) -> float`: `max(0.0, expires_at - clock())`, never negative.
  - `expired(self) -> bool`: `clock() >= expires_at`.
- **New member: `Capability.DEADLINE = "deadline"`,** added to the enum from `GAL-plugin`.

`src/hiddengems/abstract_provider.py`:

- **New: `class DeadlineAwareGemProvider(AbstractGemProvider)`,** with two abstract methods. They are separate
  names, so the signatures of `find_gem` and `get_gem` stay unchanged:
  - `find_gem_within(self, name: str, *, criteria: Mapping[str, Any] | None = None, deadline: Deadline)
    -> tuple[GemReference, ...]`;
  - `get_gem_within(self, reference: GemReference, *, deadline: Deadline) -> list[Gem]`.

  Contract: the call returns, or raises `ProviderLookupError` with `TIMEOUT_REASON` and its partial matches,
  within `deadline.remaining()` plus `LOOKUP_SHUTDOWN_GRACE_SECONDS`. Every operation it starts, including a
  process or a request, ends within that time. The one exception, under option (a) of the
  [dotenv decision](#alternatives-for-the-owners-decision), is the read of a declared dotenv file on an unresponsive
  mount, which the lookup waits for.
- **Built-ins:** each implements both methods, except `KeyringProvider` under Keychain option (b). Its existing
  `find_gem` and `get_gem` delegate to them with `Deadline.unbounded()`, so direct callers keep today's behavior,
  except `KeyringProvider` under Keychain option (a), whose native read never shows a prompt.
- **Unbounded means today's calls.** When `deadline.expires_at` is `math.inf`, a built-in makes exactly the
  calls it makes at `4438234`, with today's arguments. `_preload_content` and `_request_timeout`, a `deadline=`
  argument to `_run_cli`, and the `asyncio.wait_for` wrapper are added only for a finite deadline.
- **New: `deadline_capability(provider_type: type) -> CapabilityObservation`,** with source
  `EvidenceSource.PROVIDER_CLASS`:
  - an input that is not a class raises `TypeError(DEADLINE_CAPABILITY_INPUT_ERROR.format(value=...))`;
  - `SUPPORTED`, with `DEADLINE_SUPPORTED_REASON`, for a subclass of `DeadlineAwareGemProvider`;
  - `UNKNOWN`, with `DEADLINE_LEGACY_REASON`, for any other class on which `inspect.getattr_static` finds both a
    callable `find_gem_within` and a callable `get_gem_within` through the method resolution order;
  - `UNSUPPORTED`, with `DEADLINE_UNSUPPORTED_REASON`, otherwise.
- **New constants in `constants/capability.py`,** which `GAL-plugin` creates (this feature creates it under
  `GAL-plugin` option one): `DEADLINE_SUPPORTED_REASON`,
  `DEADLINE_LEGACY_REASON`, `DEADLINE_UNSUPPORTED_REASON`, `DEADLINE_CAPABILITY_INPUT_ERROR`, and
  `LEGACY_DEADLINE_WARNING`, with the values listed under [Constants](#constants).

`src/hiddengems/hidden_gems.py`:

- **`HiddenGems.inspect_gem` and `HiddenGems.resolve_gem`** keep their signatures and return types. Each creates
  `Deadline.after(LOOKUP_TIMEOUT_SECONDS)` once and passes it to `_inspect_within` or `_resolve_within`.
- **New: `HiddenGems._inspect_within(self, name: str, *, provider: str | None, target: str | None, criteria:
  Mapping[str, Any] | None, deadline: Deadline) -> LookupResult`,** private. `target` is the parameter
  `GAL-scope` (plan 4.1) adds to `inspect_gem`. It holds today's body of `inspect_gem`, which selects `records`,
  and runs these steps:
  1. Use the `deadline` it receives; it creates none, so one lookup has one deadline. When `records` is empty,
     steps 2 to 5 are skipped and the result holds only the unavailable-provider issues, as today, because
     `ThreadPoolExecutor(max_workers=0)` raises `ValueError`.
  2. Open
     `ThreadPoolExecutor(max_workers=min(LOOKUP_WORKERS, len(records)), thread_name_prefix=LOOKUP_THREAD_PREFIX)`.
  3. For each record in record order:
     - a `SUPPORTED` provider gets `find_gem_within(name, criteria=..., deadline=deadline)` submitted;
     - an `UNKNOWN` provider gets the same call submitted, in process, after
       `warnings.warn(LEGACY_DEADLINE_WARNING.format(provider_class=...), DeprecationWarning)`. This is the
       recommended option of the [unknown provider decision](#alternatives-for-the-owners-decision);
     - an `UNSUPPORTED` provider is handled per the
       [non-cooperative provider decision](#alternatives-for-the-owners-decision).
  4. Collect the futures in record order, not completion order, each with
     `future.result(timeout=deadline.remaining() + LOOKUP_SHUTDOWN_GRACE_SECONDS)`. `ProviderNotApplicable` and
     `ProviderLookupError` are handled exactly as in today's loop. A `TimeoutError` from the wait becomes
     `LookupIssue(provider, instance_id, TIMEOUT_REASON, TIMEOUT_NEXT_ACTION)`.
  5. Shut down with `executor.shutdown(wait=True, cancel_futures=True)`. Because every running call is bounded,
     except the one dotenv read the Purpose names under dotenv option (a), this wait is bounded too. A provider
     that breaks its contract and overruns is caught by the acceptance cases, not hidden by a non-waiting shutdown.
- **New: `HiddenGems._resolve_within(self, name: str, *, provider: str | None, target: str | None, criteria:
  Mapping[str, Any] | None, choice: str | None, deadline: Deadline) -> GemReference`,** private. `choice` is the
  parameter `GAL-chooser` (plan 4.3) adds to `resolve_gem`. It holds today's body of `resolve_gem` and calls
  `_inspect_within` with the same deadline. It takes no `remember`: `resolve_gem` and `dig_gem` apply `remember` of
  `GAL-chooser` themselves, through `save_preference`, after `_resolve_within` returns.
- **`HiddenGems.dig_gem`** creates one `deadline = Deadline.after(LOOKUP_TIMEOUT_SECONDS)` and passes it to
  `_resolve_within` and to the read of the selected reference. It applies `remember` of `GAL-chooser` itself,
  through `save_preference`, after `_resolve_within` returns. The read chooses by `deadline_capability`:
  - `SUPPORTED` and `UNKNOWN` call `get_gem_within(reference, deadline=deadline)`;
  - `UNSUPPORTED` follows the [non-cooperative provider decision](#alternatives-for-the-owners-decision).

Built-in deadline handling:

- **Kubernetes:** before each namespace request, if `deadline.expired()`, it stops and raises
  `ProviderLookupError(TIMEOUT_REASON, TIMEOUT_NEXT_ACTION, matches)` with the matches so far. Otherwise it reads
  the Secret with the new private method `_read_secret_within(self, name: str, namespace: str, deadline: Deadline)
  -> V1Secret`, which `find_gem_within` and `get_gem_within` both use. It:
  - calls `read_namespaced_secret(..., _preload_content=False, _request_timeout=(slice, slice))`, with
    `slice = min(deadline.remaining(), KUBE_READ_SLICE_SECONDS)`;
  - reads the body with `response.read1(KUBE_READ_CHUNK_BYTES)`, which returns after at most one socket read,
    checking `deadline.expired()` before each chunk;
  - on expiry, closes the response and raises `ProviderLookupError(TIMEOUT_REASON, TIMEOUT_NEXT_ACTION)`;
    `find_gem_within` catches it and raises it again with the matches found in earlier namespaces, as its loop
    keeps partial matches today;
  - deserializes the body into `V1Secret` with the client's `ApiClient.deserialize`.

  Total bound: the deadline plus one slice. Several namespaces share one deadline; none gets a fresh one. With an
  unbounded deadline, the call stays exactly as today, with no `_preload_content` and no `_request_timeout`.
- **1Password CLI:** `subprocess.run(..., timeout=min(CLI_TIMEOUT_SECONDS, deadline.remaining()))`. On timeout,
  `run` kills the `op` process and waits for it, so the process has ended when the call returns.
- **1Password SDK:** `asyncio.run(asyncio.wait_for(self._find_sdk(...), deadline.remaining()))`. The SDK
  (`onepassword` 0.4.1) is installed in the `hiddengems` environment, but whether cancelling stops its native work
  has not been measured. Rule:
  the SDK path is bounded only if `test_onepassword_sdk_work_ends_at_the_deadline`, run with the SDK
  installed, finds no SDK task or thread alive after the call returns. If it does not pass,
  `OnePasswordProvider.find_gem_within` and `get_gem_within` raise
  `ProviderLookupError(ONEPASSWORD_SDK_NO_DEADLINE_REASON, ONEPASSWORD_SDK_NO_DEADLINE_NEXT_ACTION)` for an
  SDK-only instance under a finite deadline, and the CLI path stays bounded. Both are `Final[str]` in
  `gems/onepassword_constants.py` (created by `GAL-settings`): `ONEPASSWORD_SDK_NO_DEADLINE_REASON = "The
  1Password SDK cannot stop at a lookup deadline"` and `ONEPASSWORD_SDK_NO_DEADLINE_NEXT_ACTION = "Install or
  enable the 1Password CLI for this account, then retry"`.
- **Keychain:** per the [Keychain decision](#alternatives-for-the-owners-decision).
- **dotenv:** reads one local file and checks the deadline before reading. Known limit: the read of a declared
  file on an unresponsive network mount can block inside the operating system and cannot be bounded in a thread.
  The walk guard of `GAL-expand` keeps discovered and expanded files off mounts, but a plain path the user declares
  on a share is their choice. The owner's [dotenv decision](#alternatives-for-the-owners-decision) settles this
  case: under option (a), the lookup waits for that read; under option (b), `DotEnvProvider.find_gem_within` and
  `get_gem_within` read a declared file whose device differs from the home directory's through
  `run_in_child(_read_dotenv_file, (path,), deadline)` under a finite deadline, and the lookup returns at the
  deadline plus grace. `_read_dotenv_file(path: Path) -> dict[str, str]` is a module-level function of
  `dotenv_provider.py`, so the child can import it by name.

## Abstraction-extension gate

This section follows the [gate](../provider-routing-design.md#abstraction-extension-gate) of the overview. The
baseline is commit `4438234`.

### 1. Extensions

#### `Deadline`

- **Signature:** as listed under `src/hiddengems/abstraction.py` above.
- **Status:** MUST ADD.
- **Behavior:** an absolute deadline on an injected monotonic clock; a value, with no side effects.
- **Implementation owner:** `abstraction.py`.
- **Actual caller:** `HiddenGems.inspect_gem`, `HiddenGems.resolve_gem`, and `HiddenGems.dig_gem` each create one
  per lookup; every `*_within` method, including `_inspect_within` and `_resolve_within`, reads it.
- **Acceptance tests:** `test_timed_out_call_stops_and_lookup_shuts_down` and
  `test_builtin_providers_stop_at_the_deadline`.

#### `DeadlineAwareGemProvider`

- **Signature:** `class DeadlineAwareGemProvider(AbstractGemProvider)` in `abstract_provider.py`, with the two
  abstract methods `find_gem_within` and `get_gem_within` listed above. It defines no `__init__` and holds no
  state.
- **Status:** optional base class. A provider that subclasses it MUST implement both methods.
- **Behavior:** each call returns, or raises `ProviderLookupError` with `TIMEOUT_REASON`, within
  `deadline.remaining()` plus `LOOKUP_SHUTDOWN_GRACE_SECONDS`, and every operation it starts ends within that
  time, except, under option (a) of the [dotenv decision](#alternatives-for-the-owners-decision), the read of a
  declared dotenv file on an unresponsive mount.
- **Why it is not a parallel contract:** it subclasses `AbstractGemProvider` and adds two methods under new
  names. `find_gem` and `get_gem` stay the MUST members with their signatures unchanged, and a built-in's
  `find_gem` and `get_gem` delegate to the new methods with `Deadline.unbounded()`. A provider that does not
  subclass it keeps working: in process if it defines `find_gem_within` and `get_gem_within`, otherwise through
  the non-cooperative decision.
- **Implementation owner:** `abstract_provider.py`; all four built-ins subclass it, `KeyringProvider` only under
  Keychain option (a).
- **Actual caller:** `HiddenGems._inspect_within`, step 3, and the `HiddenGems.dig_gem` read.
- **Acceptance tests:** `test_builtin_providers_stop_at_the_deadline`,
  `test_kubernetes_slow_steady_response_stops_at_the_deadline`,
  `test_dotenv_read_on_another_device_runs_in_a_bounded_child` under dotenv option (b),
  `test_parallel_lookup_never_exceeds_lookup_workers`, and `test_parallel_merge_ignores_completion_order`.

#### `deadline_capability` and `Capability.DEADLINE`

- **Signature:** `def deadline_capability(provider_type: type) -> CapabilityObservation` in
  `abstract_provider.py`, and the enum member `Capability.DEADLINE = "deadline"`.
- **Status:** MUST ADD.
- **Behavior:** the same rules and order as `write_capability` of `GAL-plugin`, with
  `DeadlineAwareGemProvider` in place of `WritableGemProvider` and both `find_gem_within` and `get_gem_within` in
  place of `put_gem`: an input that is not a class raises `TypeError` with `DEADLINE_CAPABILITY_INPUT_ERROR`; the
  subclass gives `SUPPORTED` with `DEADLINE_SUPPORTED_REASON`; a `find_gem_within` and a `get_gem_within` both
  resolved through the method resolution order, on any other class, give `UNKNOWN` with `DEADLINE_LEGACY_REASON`;
  anything else gives `UNSUPPORTED` with `DEADLINE_UNSUPPORTED_REASON`. Source:
  `EvidenceSource.PROVIDER_CLASS`. Never `None`.
- **Implementation owner:** `abstract_provider.py`, with the constants in `constants/capability.py`.
- **Actual caller:** `HiddenGems._inspect_within`, step 3, and the `HiddenGems.dig_gem` read.
- **Acceptance tests:** `test_deadline_capability_reports_each_state`.

#### `HiddenGems.inspect_gem` and `HiddenGems.dig_gem`

- **Signature:** unchanged, for these two and for `HiddenGems.resolve_gem`.
- **Status:** behavior change.
- **Behavior:** the steps listed under `src/hiddengems/hidden_gems.py` above. `inspect_gem` and `resolve_gem`
  delegate with `Deadline.after(LOOKUP_TIMEOUT_SECONDS)`; `dig_gem` creates one deadline for `_resolve_within` and
  the read. `_resolve_within` takes no `remember`: `resolve_gem` and `dig_gem` apply `remember` of `GAL-chooser`
  themselves, through `save_preference`, after `_resolve_within` returns.
- **Implementation owner:** `hidden_gems.py`.
- **Actual caller:** the caller of the library.
- **Acceptance tests:** `test_unchecked_onepassword_does_not_silently_return_old_keyring` (both parameter values),
  `test_parallel_lookup_never_exceeds_lookup_workers`, `test_parallel_merge_ignores_completion_order`,
  `test_timed_out_call_stops_and_lookup_shuts_down`, `test_non_cooperative_provider_never_runs_unbounded`,
  `test_lookup_with_no_records_skips_the_pool`, and, unchanged,
  `test_detection_runs_once_and_list_valued_gem_stays_nested`,
  `test_namespace_only_preference_resolves_unique_cluster`, and
  `test_bounded_scan_reports_incomplete_but_explicit_path_is_checked`.

#### `HiddenGems._inspect_within` and `HiddenGems._resolve_within`

- **Signature:** `_inspect_within(self, name: str, *, provider: str | None, target: str | None, criteria:
  Mapping[str, Any] | None, deadline: Deadline) -> LookupResult` and `_resolve_within(self, name: str, *,
  provider: str | None, target: str | None, criteria: Mapping[str, Any] | None, choice: str | None, deadline:
  Deadline) -> GemReference`, private methods of `HiddenGems`. `None` means not given, as in the public methods.
- **Status:** MUST ADD.
- **Behavior:** today's bodies of `inspect_gem` and `resolve_gem`, plus the parallel steps listed under
  `src/hiddengems/hidden_gems.py` above, under the one deadline they receive. They create no deadline.
- **Implementation owner:** `hidden_gems.py`.
- **Actual caller:** `HiddenGems.inspect_gem` calls `_inspect_within`; `HiddenGems.resolve_gem` and
  `HiddenGems.dig_gem` call `_resolve_within`, which calls `_inspect_within`.
- **Acceptance tests:** `test_timed_out_call_stops_and_lookup_shuts_down`,
  `test_lookup_with_no_records_skips_the_pool`, and, unchanged,
  `test_detection_runs_once_and_list_valued_gem_stays_nested`,
  `test_namespace_only_preference_resolves_unique_cluster`, and
  `test_bounded_scan_reports_incomplete_but_explicit_path_is_checked`.

#### `run_in_child`, `HiddenGems._find_in_child`, and `HiddenGems._get_in_child`

- **Signature:** `run_in_child(target: Callable[..., T], args: tuple[Any, ...], deadline: Deadline) -> T`, in the
  new module `src/hiddengems/child_runner.py`: it runs `target(*args)` in a child process and returns its
  result over a pipe. `target` must be a module-level function that the child can import by name. The router's
  wrappers are `_find_in_child(self, record: DetectedProvider, provider_type: type, name: str, criteria:
  Mapping[str, Any] | None, deadline: Deadline) -> tuple[GemReference, ...]` and `_get_in_child(self, record:
  DetectedProvider, provider_type: type, reference: GemReference, deadline: Deadline) -> list[Gem]`, private
  methods of `HiddenGems`.
- **Status:** MUST ADD under non-cooperative option (b) or dotenv option (b) only.
- **Behavior:** `run_in_child` starts a child from `multiprocessing.get_context(CHILD_START_METHOD)`, and the two
  wrappers call it. The child builds the
  instance with `GemProvider.create(record, types=(provider_type,))` from `GAL-discovery`; the class crosses the
  pipe by module and qualified name. At `deadline.remaining() + LOOKUP_SHUTDOWN_GRACE_SECONDS` the child is
  terminated and joined, and `ProviderLookupError(TIMEOUT_REASON, TIMEOUT_NEXT_ACTION)` is raised. A class that
  pickle cannot reference by name is handled as under option (a). `_get_in_child` sends the values over the pipe.
  A value that pickle cannot send, an IO stream or any other unpicklable value, is never materialized: the child
  sends nothing for it, and `_get_in_child` raises
  `ProviderLookupError(CHILD_TRANSPORT_REASON, CHILD_TRANSPORT_NEXT_ACTION)`. This is the recommended option of the
  [child read decision](#alternatives-for-the-owners-decision). Under dotenv option (b), `DotEnvProvider` calls
  `run_in_child` itself for a declared file on another device; no router code branches on a provider.
- **Implementation owner:** `child_runner.py` for `run_in_child`; `hidden_gems.py` for the two wrappers.
- **Actual caller:** `HiddenGems._inspect_within`, step 3, calls `_find_in_child`, and the `HiddenGems.dig_gem`
  read calls `_get_in_child`, both for an `UNSUPPORTED` provider; under dotenv option (b),
  `DotEnvProvider.find_gem_within` and `get_gem_within` call `run_in_child` for a declared file on another
  device.
- **Acceptance tests:** `test_non_cooperative_provider_never_runs_unbounded` and
  `test_child_read_reports_declared_transport_rejection`.

#### Keychain interaction-not-allowed status

- **Signature:** in `gems/keychain_constants.py`, which `GAL-settings` creates:
  `KEYCHAIN_INTERACTION_NOT_ALLOWED: Final[int] = 3`, mirroring the new `HG_KEYCHAIN_INTERACTION_NOT_ALLOWED = 3`
  after `HG_KEYCHAIN_ERROR = 2` in `keychain_bridge.h`; `KEYCHAIN_ABI_VERSION: Final[int] = 2`, moved by
  `GAL-settings` from `_HG_KEYCHAIN_ABI_VERSION = 1` at `keyring_provider.py:29`, and raised here from 1 to 2; and
  `KEYCHAIN_LOCKED_NEXT_ACTION: Final[str] = "Unlock the Keychain or allow access, then re-run"`.
  `keyring_provider.py` keeps `_HG_KEYCHAIN_ABI_VERSION` and `_HG_KEYCHAIN_OK` bound to `KEYCHAIN_ABI_VERSION` and
  `KEYCHAIN_OK`, because `tests/test_hidden_gems_keyring.py:32,55` reads them. The status is added to
  `keychain_bridge.h`, and `keychain_bridge.cpp` and `keychain_reader.cpp` return it.
- **Status:** MUST ADD under Keychain option (a) only.
- **Behavior:** the native read asks the Keychain not to prompt. A locked or authorization-gated item returns
  `KEYCHAIN_INTERACTION_NOT_ALLOWED` at once, and `KeyringProvider` reports it as UNKNOWN with
  `KEYCHAIN_LOCKED_NEXT_ACTION`.
- **Implementation owner:** `keychain_reader.cpp` and `keyring_provider.py`.
- **Actual caller:** `KeyringProvider.find_gem_within` and `KeyringProvider.get_gem_within`.
- **Acceptance tests:** the Keychain case of `test_builtin_providers_stop_at_the_deadline`.

### 2. What each extension extends

`DeadlineAwareGemProvider` is a subclass of `AbstractGemProvider`, awaiting the owner's approval with this
proposal. `B` loses no member and changes no signature. The factory is unchanged.

### 3. Providers in P

| Provider | `deadline_capability` | How it stops at the deadline |
| --- | --- | --- |
| `OnePasswordProvider` | `SUPPORTED` | CLI timeout kills `op`; SDK under `wait_for` |
| `DotEnvProvider`, dotenv option (a) | `SUPPORTED` | checks the deadline; waits on an unresponsive mount |
| `DotEnvProvider`, dotenv option (b) | `SUPPORTED` | checks the deadline; reads a file on another device in a child |
| `KeyringProvider`, Keychain option (a) | `SUPPORTED` | a non-interactive query |
| `KeyringProvider`, Keychain option (b) | `UNSUPPORTED` | the non-cooperative path |
| `KubernetesProvider` | `SUPPORTED` | `_read_secret_within`: sliced timeouts and a chunked body read |

- **Applicable contract,** `C` restricted to the classes each provider subclasses:
  - `OnePasswordProvider` and `KubernetesProvider`: `AbstractGemProvider`, `DeadlineAwareGemProvider`;
  - `DotEnvProvider`: `AbstractGemProvider`, `WritableGemProvider`, `DeadlineAwareGemProvider`;
  - `KeyringProvider`: `AbstractGemProvider`, and `DeadlineAwareGemProvider` under Keychain option (a).

  Under `GAL-plugin` option one, `WritableGemProvider` does not exist, and no provider subclasses it.

### 4. Baseline tests whose expectations change

None. Each of the nine baseline routing tests, traced through the new code:

- **Through `HiddenGems` over `FakeProvider`:** `test_unchecked_onepassword_does_not_silently_return_old_keyring`,
  `test_detection_runs_once_and_list_valued_gem_stays_nested`, and
  `test_namespace_only_preference_resolves_unique_cluster`. Its `ClusterProvider` subclass overrides only
  `find_gem`. `FakeProvider` gains `find_gem_within` and `get_gem_within`, which delegate to `self.find_gem`
  and `self.get_gem`, so `deadline_capability` is `UNKNOWN`. Step 3 of `_inspect_within` then calls it in
  process with one `DeprecationWarning`. The matches, issues, and values are the same, and the warning does not
  fail the suite, because `pyproject.toml` sets no `filterwarnings`.
- **Through `HiddenGems` over real Kubernetes records:**
  `test_bounded_scan_reports_incomplete_but_explicit_path_is_checked`. The provider is `SUPPORTED`, and its
  records and pseudo-instances are unchanged. The test patches the class limits `_MAX_AUTO_CONFIG_BYTES` and
  `_MAX_DIRECTORY_ENTRIES` of `KubernetesProvider`; it keeps working because `GAL-settings` keeps the class
  attributes as the read path, initialized from the new module constants.
- **Calling a provider directly, with an unbounded deadline:**
  - `test_kubernetes_keeps_confirmed_match_when_other_namespace_is_unchecked`: its fake
    `read_secret(*, name, namespace)` accepts neither `_preload_content` nor `_request_timeout`. With an unbounded
    deadline neither is passed;
  - `test_onepassword_preserves_match_when_another_account_is_unchecked`: its fake `_run_cli(*args)` accepts no
    keyword. With an unbounded deadline none is passed;
  - `test_sdk_vault_preference_filters_duplicate_titles`: with an unbounded deadline, `_find_sdk` runs under
    `asyncio.run` without `wait_for`, as today.
- **No lookup at all:** `test_kubeconfig_environment_and_default_are_both_candidates` and
  `test_onepassword_unknown_cli_source_is_explicit` call `_paths`, `_find_cli`, and `detect`, which this
  feature does not change.

`test_unchecked_onepassword_does_not_silently_return_old_keyring` also gains a second parameter value with the
same assertions.

The keyring tests at `tests/test_hidden_gems_keyring.py:32,55` read `_HG_KEYCHAIN_ABI_VERSION` and
`_HG_KEYCHAIN_OK`. They keep working, because `keyring_provider.py` keeps both names bound to `KEYCHAIN_ABI_VERSION`
and `KEYCHAIN_OK`. Their fake library returns `keyring._HG_KEYCHAIN_ABI_VERSION` as its ABI version, so raising it
to 2 under Keychain option (a) does not break them.

### 5. Baseline

The baseline stays commit `4438234`. This proposal does not redefine `B`.

## Exceptions

No new class. Each situation reuses an existing one, under the overview's exception rules:

- **A provider that runs out of time** raises `ProviderLookupError(TIMEOUT_REASON, TIMEOUT_NEXT_ACTION,
  matches)`. That class already means "this instance could not be checked; no absence is implied", which is
  exactly a timeout, and the router already turns it into a `LookupIssue`.
- **A wait that times out in the router** (`TimeoutError` from `Future.result`) becomes the same `LookupIssue`,
  so `dig_gem` raises `IncompleteGemLookupError`, as for any unchecked provider.
- **A non-cooperative provider under option (a)**, or under option (b) one whose class pickle cannot reference by
  name, becomes a `LookupIssue` with `NO_DEADLINE_REASON`, not an exception.
- **A value that cannot cross the child-process boundary**, under non-cooperative option (b), makes
  `_get_in_child` raise `ProviderLookupError(CHILD_TRANSPORT_REASON, CHILD_TRANSPORT_NEXT_ACTION)`; the child sends
  nothing for it. This is the recommended option of the child read decision.
- **`deadline_capability` with an input that is not a class** raises `TypeError`, as `write_capability` does.
- **The 1Password CLI's `subprocess.TimeoutExpired`** is translated to `ProviderLookupError`, as
  `onepassword_provider.py` already does today.
- **Warning reused: `DeprecationWarning`** with `LEGACY_DEADLINE_WARNING`, for an `UNKNOWN` provider: a one-time
  migration, as `GAL-plugin` does for a legacy writer.

`test_exception_classes_match_the_register` of `GAL-plugin` therefore needs no new entry for this feature.

## Constants

In `src/hiddengems/constants/lookup.py`, beside the constants the overview already lists:

- `LOOKUP_WORKERS: Final[int] = 8`, added by this feature. `LOOKUP_TIMEOUT_SECONDS: Final[float] = 20.0` is added by
  `GAL-settings` with `CLI_TIMEOUT_SECONDS`; this feature uses it.
- `LOOKUP_SHUTDOWN_GRACE_SECONDS: Final[float] = 1.0`: the time a cooperative call has to notice its deadline and
  return. The acceptance bound is the timeout plus this grace.
- `LOOKUP_THREAD_PREFIX: Final[str] = "hiddengems-lookup"`: names the worker threads, so a test can show that
  none outlives the lookup.
- `TIMEOUT_REASON` and `TIMEOUT_NEXT_ACTION`, as in the overview.
- `NO_DEADLINE_REASON: Final[str] = "Provider cannot honor a lookup deadline"` and
  `NO_DEADLINE_NEXT_ACTION: Final[str] = "Subclass DeadlineAwareGemProvider"`, used under non-cooperative
  option (a) and, under option (b), for a class that pickle cannot reference by name.
- `CHILD_START_METHOD: Final[str] = "spawn"`, used only under non-cooperative option (b) or dotenv option (b).
- `CHILD_TRANSPORT_REASON: Final[str] = "The value cannot cross the child-process boundary"` and
  `CHILD_TRANSPORT_NEXT_ACTION: Final[str] = "Ask the provider's author to subclass DeadlineAwareGemProvider"`,
  used under non-cooperative option (b) for a value that pickle cannot send, per the child read decision.

In `src/hiddengems/constants/capability.py`, which `GAL-plugin` creates, beside its write constants. Under
`GAL-plugin` option one, which adds no write constants, this feature creates the module:

- `DEADLINE_SUPPORTED_REASON: Final[str] = "Subclasses DeadlineAwareGemProvider"`.
- `DEADLINE_LEGACY_REASON: Final[str] = "Resolves find_gem_within and get_gem_within without subclassing
  DeadlineAwareGemProvider"`.
- `DEADLINE_UNSUPPORTED_REASON: Final[str] = "Does not subclass DeadlineAwareGemProvider and resolves no deadline
  methods"`.
- `DEADLINE_CAPABILITY_INPUT_ERROR: Final[str] = "deadline_capability needs a class, not {value!r}"`.
- `LEGACY_DEADLINE_WARNING: Final[str] = "{provider_class} defines find_gem_within without subclassing
  DeadlineAwareGemProvider; subclass it to keep parallel lookup"`.

In `gems/onepassword_constants.py`, which `GAL-settings` creates:

- `ONEPASSWORD_SDK_NO_DEADLINE_REASON: Final[str] = "The 1Password SDK cannot stop at a lookup deadline"` and
  `ONEPASSWORD_SDK_NO_DEADLINE_NEXT_ACTION: Final[str] = "Install or enable the 1Password CLI for this account, then
  retry"`, used by `OnePasswordProvider` for an SDK-only instance under a finite deadline if
  `test_onepassword_sdk_work_ends_at_the_deadline` does not pass.

In `gems/kubernetes_constants.py`, which `GAL-settings` creates:

- `KUBE_READ_SLICE_SECONDS: Final[float] = 1.0` and `KUBE_READ_CHUNK_BYTES: Final[int] = 65_536`, used by
  `_read_secret_within` under a finite deadline: the slice caps each connect and socket read, and each
  `response.read1` call reads at most one chunk.

In `src/hiddengems/gems/keychain_constants.py`, which `GAL-settings` creates, under Keychain option (a) only:

- `KEYCHAIN_INTERACTION_NOT_ALLOWED: Final[int] = 3`: the new bridge status, mirroring
  `HG_KEYCHAIN_INTERACTION_NOT_ALLOWED = 3`.
- `KEYCHAIN_ABI_VERSION: Final[int] = 2`: moved by `GAL-settings` from `_HG_KEYCHAIN_ABI_VERSION = 1` at
  `keyring_provider.py:29`, and raised here from 1 to 2. `keyring_provider.py` keeps `_HG_KEYCHAIN_ABI_VERSION` and
  `_HG_KEYCHAIN_OK` bound to `KEYCHAIN_ABI_VERSION` and `KEYCHAIN_OK`, because
  `tests/test_hidden_gems_keyring.py:32,55` reads them.
- `KEYCHAIN_LOCKED_NEXT_ACTION: Final[str] = "Unlock the Keychain or allow access, then re-run"`.

## Libraries

None. `concurrent.futures`, `asyncio`, `subprocess`, `multiprocessing`, `threading`, and `time` are in the
standard library.

## Behavior and compatibility

- **Lookups run in parallel and are bounded,** except, under dotenv option (a), the read of a declared file on an
  unresponsive mount. Behavior change: a lookup that hangs today now ends with an Incomplete result naming the
  provider that ran out of time.
- **The result is unchanged in shape and order.** `LookupResult` is the same, and matches stay in record order.
- **`AbstractGemProvider` gains no required member.** `DeadlineAwareGemProvider` is an additional interface, and
  `find_gem` and `get_gem` keep their signatures.
- **Third-party providers** that do not subclass `DeadlineAwareGemProvider` are handled by the non-cooperative
  decision below. That decision is a compatibility choice for the owner.
- **Gates:** unchanged.

## Alternatives for the owner's decision

An `UNKNOWN` provider, one that defines `find_gem_within` and `get_gem_within` without subclassing
`DeadlineAwareGemProvider`:

- **(a) Call it in process, with a `DeprecationWarning` (recommended).** It declares the methods, so it is
  trusted to honor the deadline, exactly as `GAL-plugin`'s legacy writer path trusts a `put_gem`. The baseline
  routing fakes keep working unchanged in their assertions.
- **(b) Treat it as non-cooperative.** Then `FakeProvider` must subclass `DeadlineAwareGemProvider`, which
  also requires it to implement `detect` and, under `GAL-plugin` option one, `put_gem`.

A non-cooperative provider, one whose `deadline_capability` is `UNSUPPORTED`:

- **(a) Refuse it in parallel lookup.** It is never called. Its lookups become a `LookupIssue` with
  `NO_DEADLINE_REASON` and `NO_DEADLINE_NEXT_ACTION`. Simple and bounded, but a provider registered by replacing
  `GemProvider.provider_types` stops being checked until its author adopts the interface.
- **(b) Run it in a child process** started with `CHILD_START_METHOD`, through `HiddenGems._find_in_child` and
  `HiddenGems._get_in_child`. The child is terminated when the deadline plus grace passes. The child builds the
  instance with `GemProvider.create(record, types=(provider_type,))` from `GAL-discovery`; the class crosses the
  pipe by module and qualified name; a class that pickle cannot reference by name is handled as under option (a).
  Bounded and compatible, at the cost of a process per call, and values for `dig_gem` crossing a local pipe
  between the user's own processes; a value that pickle cannot send is handled per the child read decision below.
  Recommended: it keeps every registered provider usable.
- A third option, running it without a bound, fails the gate, so it is not offered.

A child read, under non-cooperative option (b), of a value that cannot cross the pipe, such as an IO stream.
Rejecting it narrows what an `UNSUPPORTED` provider may return under that option, so the owner decides:

- **(a) Reject the value with the declared error.** The child sends nothing for the value, and `_get_in_child`
  raises `ProviderLookupError(CHILD_TRANSPORT_REASON, CHILD_TRANSPORT_NEXT_ACTION)`. Recommended: nothing secret is
  written anywhere.
- **(b) Hand the stream over in a temporary file.** The child writes the stream to a mode-0600 temporary file, and
  the parent returns an open file object the caller owns, at the cost of the secret at rest on disk. Any other
  value that cannot cross the pipe still gets the declared error of (a).

The Keychain, whose query can wait on an authorization prompt:

- **(a) Query without interactive UI.** The native read asks the Keychain not to prompt. A locked or
  authorization-gated item then returns at once and is reported UNKNOWN, with the next action
  `KEYCHAIN_LOCKED_NEXT_ACTION`. No lookup then shows a Keychain prompt; an item that needs one is reported UNKNOWN
  with `KEYCHAIN_LOCKED_NEXT_ACTION` and is never cached by `GAL-secure-cache`. This changes the native bridge and
  raises `KEYCHAIN_ABI_VERSION` from 1 to 2; the proposed Keychain existence feature builds on version 2.
  Recommended: a parallel lookup never waits on a dialog.
- **(b) Treat the Keychain as non-cooperative.** `KeyringProvider` does not subclass `DeadlineAwareGemProvider`
  and is `UNSUPPORTED`. It goes through the chosen non-cooperative path, and its prompt can still appear until the
  deadline ends it.

The read of a declared dotenv file on an unresponsive mount, which cannot be bounded in a thread:

- **(a) Accept a weaker contract for that named case.** `SUPPORTED` means bounded, except a read of a declared
  file on an unresponsive mount, which the lookup waits for.
- **(b) Read a file on another device in a bounded child.** A declared dotenv file whose device differs from the
  home directory's (`os.stat(path).st_dev != os.stat(Path.home()).st_dev`) is read through the child runner of
  non-cooperative option (b) under a finite deadline, so the lookup returns at the deadline plus grace. A child
  blocked inside the kernel is terminated, and reaped when the operating system releases it. `SUPPORTED` then
  means bounded, with no exception. Recommended: it keeps the owner's gate, with no provider execution unbounded
  after timeout.

## Acceptance cases

Each case extends an existing suite; there is no new test framework. Every timing case patches
`LOOKUP_TIMEOUT_SECONDS` to 0.2 and imports `LOOKUP_SHUTDOWN_GRACE_SECONDS`, so the bound under test is
0.2 seconds plus the grace.

In `tests/test_hidden_gems_routing.py`:

- **`FakeProvider` gains one keyword argument and two methods,** keeping every current test unchanged:
  - `delay: float = 0.0`;
  - `find_gem_within(self, name, *, criteria=None, deadline)`: waits `delay` in steps of
    `LOOKUP_SHUTDOWN_GRACE_SECONDS / 10`, raising `ProviderLookupError(TIMEOUT_REASON, TIMEOUT_NEXT_ACTION)`
    when the deadline expires, then returns `self.find_gem(name, criteria=criteria)`;
  - `get_gem_within(self, reference, *, deadline)`: returns `self.get_gem(reference)`.

  A provider that ignores deadlines is `BlockingExampleProvider`, below, not a flag on `FakeProvider`.

  It records when each call starts and ends, and the number of calls active at once.
- **`test_unchecked_onepassword_does_not_silently_return_old_keyring` is parametrized** by how 1Password goes
  unchecked:
  - today's `ProviderLookupError`;
  - a cooperative fake with `delay` 5.0.

  Every existing assertion holds for both: one match, one issue, `keyring` ready, `onepassword` detected, no
  secret in the report, `IncompleteGemLookupError` from `dig_gem`, and the explicit `provider="keyring"` read.
  The timeout variant also asserts that the issue's reason is `TIMEOUT_REASON`.
- `test_parallel_lookup_never_exceeds_lookup_workers`.
  - Input: `LOOKUP_WORKERS` patched to 2; five cooperative fakes with `delay` 0.05.
  - Expected: the highest number of calls active at once is 2; all five matches are present.
- `test_parallel_merge_ignores_completion_order`.
  - Input: four fakes whose delays make them finish in reverse record order.
  - Expected: matches in record order.
- `test_timed_out_call_stops_and_lookup_shuts_down`.
  - Input: one cooperative fake with `delay` 5.0.
  - Expected:
    - the fake recorded its own end within 0.2 seconds plus the grace of its start;
    - `inspect_gem` returned within the same bound;
    - afterwards, no thread whose name starts with `LOOKUP_THREAD_PREFIX` is alive.
- `test_non_cooperative_provider_never_runs_unbounded`.
  - Input: `BlockingExampleProvider` from `tests/contract/example_providers.py`, which ignores deadlines and
    blocks until released.
  - Expected under option (a): it is never called; its `LookupIssue` carries `NO_DEADLINE_REASON`.
  - Expected under option (b): its child process has ended (`exitcode` is set) when `inspect_gem` returns, within
    0.2 seconds plus the grace.
  - Under either option, no lookup thread is left alive.
- `test_child_read_reports_declared_transport_rejection`, under non-cooperative option (b) and child read
  option (a).
  - Input: an `UNSUPPORTED` provider whose `get_gem` returns an open stream.
  - Expected: `_get_in_child` raises `ProviderLookupError` with `CHILD_TRANSPORT_REASON`, and the child process has
    ended.
- `test_lookup_with_no_records_skips_the_pool`.
  - Input: `HiddenGems(providers=(), config_path=tmp_path / "missing.json")`.
  - Expected: `dig_gem` raises `GemNotFoundError`, as today, and no thread whose name starts with
    `LOOKUP_THREAD_PREFIX` is started.

In `tests/contract/test_provider_contract.py`:

- `test_deadline_capability_reports_each_state`.
  - Input: the four built-ins, `BlockingExampleProvider`, and a class that defines `find_gem_within` and
    `get_gem_within` without the base.
  - Expected: `SUPPORTED`, `UNSUPPORTED`, and `UNKNOWN` respectively, each with source `PROVIDER_CLASS`, never
    `None`. Under Keychain option (b), `KeyringProvider` gives `UNSUPPORTED`.
- `test_onepassword_sdk_work_ends_at_the_deadline`, skipped when the SDK is not installed.
  - Input: the real SDK client patched to a coroutine that awaits a native call which never returns.
  - Expected: the call ends within the bound, and no SDK task or thread is alive afterwards.
- `test_builtin_providers_stop_at_the_deadline`. Each built-in uses a fake of the I/O it performs, and the test
  measures when the work ends, not whether a timeout argument was passed:
  - **Kubernetes:** a fake API whose `read_namespaced_secret` sleeps for the slice it receives in
    `_request_timeout`, over three namespaces. Expected: no request starts after the deadline; total time is within
    the bound; the `ProviderLookupError` carries the matches found before it.
  - **1Password CLI:** a fake `op` script in `tmp_path` that sleeps for 5 seconds and records its process id.
    Expected: the call ends within the bound, and that process no longer exists.
  - **1Password SDK:** a fake client whose coroutine waits on an event that is never set. Expected: the call ends
    within the bound.
  - **Keychain, under option (a):** the existing fake library from `tests/test_hidden_gems_keyring.py`, extended
    to return `KEYCHAIN_INTERACTION_NOT_ALLOWED`. Expected: the call returns at once, UNKNOWN, with
    `KEYCHAIN_LOCKED_NEXT_ACTION`.
- `test_kubernetes_slow_steady_response_stops_at_the_deadline`.
  - Input: a fake response whose `read1` returns one byte every 0.05 seconds for 10 seconds.
  - Expected: the call ends within the deadline plus one slice.
- `test_dotenv_read_on_another_device_runs_in_a_bounded_child`, under dotenv option (b).
  - Input: a declared dotenv file with a patched `st_dev` that differs from the home directory's, and a fake read
    that blocks.
  - Expected: the lookup returns within the bound.

## Dependencies on other features

- **Requires:**
  - `GAL-plugin`, for `Capability`, `CapabilityObservation`, `EvidenceSource.PROVIDER_CLASS`, and
    `constants/capability.py`;
  - `GAL-settings`, which introduces `constants/lookup.py` with `LOOKUP_TIMEOUT_SECONDS`,
    `gems/keychain_constants.py`, `gems/kubernetes_constants.py`, and `gems/onepassword_constants.py`;
  - `GAL-scope` (plan 4.1) and `GAL-chooser` (plan 4.3), whose `target` and `choice` parameters `_inspect_within` and
    `_resolve_within` take;
  - `GAL-discovery` under non-cooperative option (b) or dotenv option (b), for
    `GemProvider.create(record, types=(provider_type,))`.
- **Required by:** `GAL-secure-cache`, which uses `Deadline`, its injected clock, `_resolve_within`,
  `get_gem_within`, and `TIMEOUT_NEXT_ACTION`.
- **Overlaps:** Keychain option (a) changes the native bridge, as the proposed Keychain existence feature does.
  Under Keychain option (a), this feature raises `KEYCHAIN_ABI_VERSION` from 1 to 2; the proposed Keychain
  existence feature builds on version 2.
- **Answers:** the overview's earlier `GAL-parallel` entry, which bounded only the caller's wait.
