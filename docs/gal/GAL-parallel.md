# GAL-parallel: bounded parallel lookup

Status: proposal, revision 2. Not approved for implementation. The owner decides the choices in
[Alternatives](#alternatives-for-the-owners-decision). The overview of all features is
[provider-routing-design.md](../provider-routing-design.md).

Revision 2 adds three things:

- the [Abstraction-extension gate](#abstraction-extension-gate) section;
- the [Exceptions](#exceptions) section required by the overview's
  [exception rules](../provider-routing-design.md#exception-consistency);
- an injected clock in `Deadline`, so a test can control time without sleeping. `GAL-secure-cache` uses the
  same `Deadline`.

## Purpose and observable capability

A lookup checks its providers in parallel, never runs more calls at once than `LOOKUP_WORKERS`, and finishes
within `LOOKUP_TIMEOUT_SECONDS` plus `LOOKUP_SHUTDOWN_GRACE_SECONDS`. Every provider call it starts also stops
within that bound. A provider that runs out of time becomes a `LookupIssue`, exactly like a provider that could
not be checked today. The merged result does not depend on which call finishes first.

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
~ src/hiddengems/constants/lookup.py             the lookup constants below
~ src/hiddengems/hidden_gems.py                  parallel inspect_gem with one deadline; bounded dig_gem read
~ src/hiddengems/gems/dotenv_provider.py         find_gem_within, get_gem_within
~ src/hiddengems/gems/k8s_provider.py            find_gem_within, get_gem_within; per-request budget
~ src/hiddengems/gems/onepassword_provider.py    find_gem_within, get_gem_within; CLI and SDK budgets
~ src/hiddengems/gems/keyring_provider.py        find_gem_within, get_gem_within; per the Keychain decision
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
  process or a request, ends within that time.
- **Built-ins:** each implements both methods. Its existing `find_gem` and `get_gem` delegate to them with
  `Deadline.unbounded()`, so direct callers keep today's behavior.
- **New: `deadline_capability(provider_type: type[AbstractGemProvider]) -> CapabilityObservation`,** with source
  `EvidenceSource.PROVIDER_CLASS`:
  - `SUPPORTED` for a subclass of `DeadlineAwareGemProvider`;
  - `UNKNOWN` for a class that defines `find_gem_within` without that base;
  - `UNSUPPORTED` otherwise.

`src/hiddengems/hidden_gems.py`:

- **`HiddenGems.inspect_gem`** keeps its signature and its `LookupResult`. It now runs these steps:
  1. Create `deadline = Deadline.after(LOOKUP_TIMEOUT_SECONDS)` once per lookup.
  2. Open
     `ThreadPoolExecutor(max_workers=min(LOOKUP_WORKERS, len(records)), thread_name_prefix=LOOKUP_THREAD_PREFIX)`.
  3. For each record in record order:
     - a `SUPPORTED` provider gets `find_gem_within(name, criteria=..., deadline=deadline)` submitted;
     - any other provider is handled per the
       [non-cooperative provider decision](#alternatives-for-the-owners-decision).
  4. Collect the futures in record order, not completion order, each with
     `future.result(timeout=deadline.remaining() + LOOKUP_SHUTDOWN_GRACE_SECONDS)`. `ProviderNotApplicable` and
     `ProviderLookupError` are handled exactly as in today's loop. A `TimeoutError` from the wait becomes
     `LookupIssue(provider, instance_id, TIMEOUT_REASON, TIMEOUT_NEXT_ACTION)`.
  5. Shut down with `executor.shutdown(wait=True, cancel_futures=True)`. Because every running call is bounded,
     this wait is bounded too. A provider that breaks its contract and overruns is caught by the acceptance cases,
     not hidden by a non-waiting shutdown.
- **`HiddenGems.dig_gem`** reads the selected reference with
  `get_gem_within(reference, deadline=Deadline.after(LOOKUP_TIMEOUT_SECONDS))`.

Built-in deadline handling:

- **Kubernetes:** before each namespace request, if `deadline.expired()`, it stops and raises
  `ProviderLookupError(TIMEOUT_REASON, TIMEOUT_NEXT_ACTION, matches)` with the matches so far. Otherwise it calls
  `read_namespaced_secret(..., _request_timeout=deadline.remaining())`. Several namespaces share one deadline;
  none gets a fresh one. The pull request records how the client applies `_request_timeout`. If it limits each
  socket read rather than the whole request, the remaining gap is listed as a known limit.
- **1Password CLI:** `subprocess.run(..., timeout=min(CLI_TIMEOUT_SECONDS, deadline.remaining()))`. On timeout,
  `run` kills the `op` process and waits for it, so the process has ended when the call returns.
- **1Password SDK:** `asyncio.run(asyncio.wait_for(self._find_sdk(...), deadline.remaining()))`. The pull request
  records whether cancelling stops the SDK's native work. If it does not, the SDK path counts as non-cooperative.
- **Keychain:** per the [Keychain decision](#alternatives-for-the-owners-decision).
- **dotenv:** reads one local file and checks the deadline before reading. Known limit: a declared file on an
  unresponsive network mount can block inside the operating system. The walk guard of `GAL-expand` keeps
  discovered and expanded files off mounts, but a plain path the user declares on a share is their choice.

## Abstraction-extension gate

This section follows the [gate](../provider-routing-design.md#abstraction-extension-gate) of the overview. The
baseline is commit `4438234`.

### 1. Extensions

#### `Deadline`

- **Signature:** as listed under `src/hiddengems/abstraction.py` above.
- **Status:** MUST ADD.
- **Behavior:** an absolute deadline on an injected monotonic clock; a value, with no side effects.
- **Implementation owner:** `abstraction.py`.
- **Actual caller:** `HiddenGems.inspect_gem` and `HiddenGems.dig_gem` create it; every `*_within` method reads
  it.
- **Acceptance tests:** `test_timed_out_call_stops_and_lookup_shuts_down` and
  `test_builtin_providers_stop_at_the_deadline`.

#### `DeadlineAwareGemProvider`

- **Signature:** `class DeadlineAwareGemProvider(AbstractGemProvider)` in `abstract_provider.py`, with the two
  abstract methods `find_gem_within` and `get_gem_within` listed above. It defines no `__init__` and holds no
  state.
- **Status:** optional base class. A provider that subclasses it MUST implement both methods.
- **Behavior:** each call returns, or raises `ProviderLookupError` with `TIMEOUT_REASON`, within
  `deadline.remaining()` plus `LOOKUP_SHUTDOWN_GRACE_SECONDS`, and every operation it starts ends within that
  time.
- **Why it is not a parallel contract:** it subclasses `AbstractGemProvider` and adds two methods under new
  names. `find_gem` and `get_gem` stay the MUST members with their signatures unchanged, and a built-in's
  `find_gem` and `get_gem` delegate to the new methods with `Deadline.unbounded()`. A provider that does not
  subclass it keeps working, through the non-cooperative decision.
- **Implementation owner:** `abstract_provider.py`; all four built-ins subclass it.
- **Actual caller:** `HiddenGems.inspect_gem`, step 3, and `HiddenGems.dig_gem`.
- **Acceptance tests:** `test_builtin_providers_stop_at_the_deadline`,
  `test_parallel_lookup_never_exceeds_lookup_workers`, and `test_parallel_merge_ignores_completion_order`.

#### `deadline_capability` and `Capability.DEADLINE`

- **Signature:** `def deadline_capability(provider_type: type[AbstractGemProvider]) -> CapabilityObservation` in
  `abstract_provider.py`, and the enum member `Capability.DEADLINE = "deadline"`.
- **Status:** MUST ADD.
- **Behavior:** the same rules and order as `write_capability` of `GAL-plugin`, with
  `DeadlineAwareGemProvider` in place of `WritableGemProvider` and `find_gem_within` in place of `put_gem`: a
  non-provider input raises `TypeError`; the subclass gives `SUPPORTED`; a `find_gem_within` resolved through
  the method resolution order gives `UNKNOWN`; anything else gives `UNSUPPORTED`. Source:
  `EvidenceSource.PROVIDER_CLASS`. Never `None`.
- **Implementation owner:** `abstract_provider.py`.
- **Actual caller:** `HiddenGems.inspect_gem`, step 3.
- **Acceptance tests:** `test_deadline_capability_reports_each_state`.

#### `HiddenGems.inspect_gem` and `HiddenGems.dig_gem`

- **Signature:** unchanged.
- **Status:** behavior change.
- **Behavior:** the steps listed under `src/hiddengems/hidden_gems.py` above.
- **Implementation owner:** `hidden_gems.py`.
- **Actual caller:** the caller of the library.
- **Acceptance tests:** every case in `tests/test_hidden_gems_routing.py` below, and the existing routing tests,
  unchanged.

### 2. What each extension extends

`DeadlineAwareGemProvider` is a subclass of `AbstractGemProvider`, awaiting the owner's approval with this
proposal. `B` loses no member and changes no signature. The factory is unchanged.

### 3. Providers in P

| Provider | Applicable contract | `deadline_capability` | How it stops at the deadline |
| --- | --- | --- | --- |
| `OnePasswordProvider` | `DeadlineAwareGemProvider` | `SUPPORTED` | CLI timeout kills `op`; SDK under `wait_for` |
| `DotEnvProvider` | `DeadlineAwareGemProvider` | `SUPPORTED` | checks the deadline before reading |
| `KeyringProvider` | `DeadlineAwareGemProvider` | `SUPPORTED` | per the Keychain decision |
| `KubernetesProvider` | `DeadlineAwareGemProvider` | `SUPPORTED` | `_request_timeout` from the remaining time |

### 4. Baseline tests whose expectations change

None. `test_unchecked_onepassword_does_not_silently_return_old_keyring` gains a second parameter value with the
same assertions, and `FakeProvider` gains keyword arguments whose defaults keep every current test unchanged.

### 5. Baseline

The baseline stays commit `4438234`. This proposal does not redefine `B`.

## Exceptions

No new class. Each situation reuses an existing one, under the overview's exception rules:

- **A provider that runs out of time** raises `ProviderLookupError(TIMEOUT_REASON, TIMEOUT_NEXT_ACTION,
  matches)`. That class already means "this instance could not be checked; no absence is implied", which is
  exactly a timeout, and the router already turns it into a `LookupIssue`.
- **A wait that times out in the router** (`TimeoutError` from `Future.result`) becomes the same `LookupIssue`,
  so `dig_gem` raises `IncompleteGemLookupError`, as for any unchecked provider.
- **A non-cooperative provider under option (a)** becomes a `LookupIssue` with `NO_DEADLINE_REASON`, not an
  exception.
- **`deadline_capability` with a non-provider input** raises `TypeError`, as `write_capability` does.
- **The 1Password CLI's `subprocess.TimeoutExpired`** is translated to `ProviderLookupError`, as
  `onepassword_provider.py` already does today.

`test_exception_classes_match_the_register` of `GAL-plugin` therefore needs no new entry for this feature.

## Constants

In `src/hiddengems/constants/lookup.py`, beside the constants the overview already lists:

- `LOOKUP_WORKERS: Final[int] = 8` and `LOOKUP_TIMEOUT_SECONDS: Final[float] = 20.0`, as in the overview.
- `LOOKUP_SHUTDOWN_GRACE_SECONDS: Final[float] = 1.0`: the time a cooperative call has to notice its deadline and
  return. The acceptance bound is the timeout plus this grace.
- `LOOKUP_THREAD_PREFIX: Final[str] = "hiddengems-lookup"`: names the worker threads, so a test can show that
  none outlives the lookup.
- `TIMEOUT_REASON` and `TIMEOUT_NEXT_ACTION`, as in the overview.
- `NO_DEADLINE_REASON: Final[str] = "Provider cannot honor a lookup deadline"` and
  `NO_DEADLINE_NEXT_ACTION: Final[str] = "Subclass DeadlineAwareGemProvider"`, used only under non-cooperative
  option (a).
- `CHILD_START_METHOD: Final[str] = "spawn"`, used only under non-cooperative option (b).

## Libraries

None. `concurrent.futures`, `asyncio`, `subprocess`, `multiprocessing`, `threading`, and `time` are in the
standard library.

## Behavior and compatibility

- **Lookups run in parallel and are bounded.** Behavior change: a lookup that hangs today now ends with an
  Incomplete result naming the provider that ran out of time.
- **The result is unchanged in shape and order.** `LookupResult` is the same, and matches stay in record order.
- **`AbstractGemProvider` gains no required member.** `DeadlineAwareGemProvider` is an additional interface, and
  `find_gem` and `get_gem` keep their signatures.
- **Third-party providers** that do not subclass `DeadlineAwareGemProvider` are handled by the non-cooperative
  decision below. That decision is a compatibility choice for the owner.
- **Gates:** unchanged.

## Alternatives for the owner's decision

A non-cooperative provider, one whose `deadline_capability` is not `SUPPORTED`:

- **(a) Refuse it in parallel lookup.** It is never called. Its lookups become a `LookupIssue` with
  `NO_DEADLINE_REASON` and `NO_DEADLINE_NEXT_ACTION`. Simple and bounded, but a provider registered by replacing
  `GemProvider.provider_types` stops being checked until its author adopts the interface.
- **(b) Run it in a child process** started with `CHILD_START_METHOD`. The child is terminated when the deadline
  plus grace passes; the instance is rebuilt in the child from its record. Bounded and compatible, at the cost of
  a process per call, and values for `dig_gem` crossing a local pipe between the user's own processes.
  Recommended: it keeps every registered provider usable.
- A third option, running it without a bound, fails the gate, so it is not offered.

The Keychain, whose query can wait on an authorization prompt:

- **(a) Query without interactive UI.** The native read asks the Keychain not to prompt. A locked or
  authorization-gated item then returns at once and is reported UNKNOWN, with the next action "unlock the
  Keychain or allow access, then re-run". This changes the native bridge and raises its ABI version, overlapping
  the proposed Keychain existence feature. Recommended: a parallel lookup never waits on a dialog.
- **(b) Treat the Keychain as non-cooperative.** It goes through the chosen non-cooperative path, and its prompt
  can still appear until the deadline ends it.

## Acceptance cases

Each case extends an existing suite; there is no new test framework. Every timing case patches
`LOOKUP_TIMEOUT_SECONDS` to 0.2 and imports `LOOKUP_SHUTDOWN_GRACE_SECONDS`, so the bound under test is
0.2 seconds plus the grace.

In `tests/test_hidden_gems_routing.py`:

- **`FakeProvider` gains two keyword arguments,** whose defaults keep every current test unchanged:
  - `delay: float = 0.0`;
  - `cooperative: bool = True`. A cooperative fake implements `find_gem_within`, sleeps in small steps, and
    raises `ProviderLookupError(TIMEOUT_REASON, ...)` when its deadline expires.

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

In `tests/contract/test_provider_contract.py`:

- `test_deadline_capability_reports_each_state`.
  - Input: the four built-ins, `BlockingExampleProvider`, and a class that defines `find_gem_within` without the
    base.
  - Expected: `SUPPORTED`, `UNSUPPORTED`, and `UNKNOWN` respectively, each with source `PROVIDER_CLASS`, never
    `None`.
- `test_builtin_providers_stop_at_the_deadline`. Each built-in uses a fake of the I/O it performs, and the test
  measures when the work ends, not whether a timeout argument was passed:
  - **Kubernetes:** a fake API whose `read_namespaced_secret` sleeps for the `_request_timeout` it receives, over
    three namespaces. Expected: no request starts after the deadline; total time is within the bound; the
    `ProviderLookupError` carries the matches found before it.
  - **1Password CLI:** a fake `op` script in `tmp_path` that sleeps for 5 seconds and records its process id.
    Expected: the call ends within the bound, and that process no longer exists.
  - **1Password SDK:** a fake client whose coroutine waits on an event that is never set. Expected: the call ends
    within the bound.
  - **Keychain, under option (a):** the existing fake library from `tests/test_hidden_gems_keyring.py`, extended
    to return the interaction-not-allowed status. Expected: the call returns at once, UNKNOWN, with the unlock
    next action.

## Dependencies on other features

- **Requires:**
  - `GAL-plugin`, for `Capability`, `CapabilityObservation`, and `EvidenceSource.PROVIDER_CLASS`;
  - `GAL-settings`, which introduces `constants/lookup.py`.
- **Overlaps:** Keychain option (a) changes the native bridge, as the proposed Keychain existence feature does.
  Whichever lands first raises the ABI version; the other builds on it.
- **Answers:** the overview's earlier `GAL-parallel` entry, which bounded only the caller's wait.
