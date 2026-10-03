# GAL-plugin: provider contract and writable capability

Status: proposal, revision 6. Not approved for implementation. The owner decides the choices in
[Alternatives](#alternatives-for-the-owners-decision); this document recommends option two with the legacy
writer path. The overview of all features is [provider-routing-design.md](../provider-routing-design.md).

Revision 6 adds the [Exceptions](#exceptions) section required by the overview's
[exception rules](../provider-routing-design.md#exception-consistency), and the check that enforces them,
`test_exception_classes_match_the_register`.

Revision 5 added three things:

- the [Abstraction-extension gate](#abstraction-extension-gate) section, declaring each extension's signature,
  behavior, implementation owner, actual caller, and acceptance tests;
- `get_gem` keeps the error behavior of the baseline. A uniform error for a location that is gone is the
  proposed feature `GAL-read-errors`, presented under [Alternatives](#alternatives-for-the-owners-decision);
- `write_capability` resolves `put_gem` through the method resolution order, so an inherited legacy writer is
  `UNKNOWN`, with its own acceptance case.

Revision 4 added the contributor contract table, the worked example, write capability with its evidence, and
the before-and-after list.

It names, with their owners, the obligations that belong to other features: a runtime check of provider
returns, keeping every found location as a candidate, and the caller's exact choice. Revision 3's correction
stands: a third-party provider can reach `hide_gem` today by replacing `GemProvider.provider_types`.

## Purpose and observable capability

A contributor who adds a provider can see, from the classes alone, what to implement. The library reports
whether a provider can store gems, with evidence, and refuses a read-only provider before calling into it.

- **Observable capability (option two):**
  - `HiddenGems.hide_gem(name, value, provider=...)` against a read-only provider raises
    `ProviderNotWritableError`, a subclass of `NotImplementedError`, without calling the provider;
  - `write_capability(provider_type)` returns a `CapabilityObservation` naming the state and its evidence.
- **Today:** `AbstractGemProvider.put_gem` is abstract (`abstract_provider.py:51-65`). The Kubernetes,
  1Password, and Keychain providers implement it only to raise `NotImplementedError` (`k8s_provider.py:610-625`,
  `onepassword_provider.py:489-498`, `keyring_provider.py:216-232`). `python-standards-contract.md` says a
  concrete class must not present an incomplete implementation as supported.

## Contributor contract

What a provider implements, today and under option two:

| Member | Today | Option two | When absent | Called by |
| --- | --- | --- | --- | --- |
| `name` | MUST | MUST | registry lookup fails | `GemProvider.detect`, `GemProvider.create` |
| `__init__` | MUST, abstract | MUST, abstract | class cannot be instantiated | `GemProvider.create` |
| `detect` | MUST, abstract | MUST, abstract | class cannot be instantiated | `GemProvider.detect` |
| `find_gem` | MUST, abstract | MUST, abstract | class cannot be instantiated | `HiddenGems.inspect_gem` |
| `get_gem` | MUST, abstract | MUST, abstract | class cannot be instantiated | `HiddenGems.dig_gem` |
| `put_gem` | MUST; stubs raise | MUST if `WritableGemProvider` | refused before any call | `HiddenGems.hide_gem` |

An implementation inherited from a concrete parent satisfies a MUST. No member needs an empty override or a
stub.

Signatures, results, errors, and lifecycle:

- **`name: ClassVar[str]`.** A stable, non-secret provider kind, unique across registered providers. Every
  record and reference the provider returns carries it in its `provider` field.
- **`__init__(self, **settings: Any) -> None`.** Validates and stores the settings of one detected record,
  never authenticating and never using the network. `GemProvider.create` calls it once per detected record,
  with the record's settings plus `instance_id`.
- **`@classmethod detect(cls, **options: Any) -> tuple[DetectedProvider, ...]`.**
  - Passive and bounded: no network, no prompt, and no secret read.
  - Returns one record per instance found. An empty tuple means this provider has no instance here.
- **`find_gem(self, name: str, *, criteria: Mapping[str, Any] | None = None) -> tuple[GemReference, ...]`.**
  Returns non-secret references and never `None`. Two errors have defined meanings:
  - `ProviderNotApplicable`: the criteria are outside this instance.
  - `ProviderLookupError(reason, next_action, matches)`: the instance could not be checked. Partial matches
    are kept in `matches`; this is never a claim of absence.
- **`get_gem(self, reference: GemReference) -> list[Gem]`.** Returns the values at one reference, a single value
  as a one-item list. `B` defines no error for a location that is gone, and this feature adds none. At
  `4438234` the built-ins differ:
  - `DotEnvProvider` and `KeyringProvider` raise `ProviderLookupError` (`dotenv_provider.py:661-680`,
    `keyring_provider.py:197-215`);
  - `KubernetesProvider` lets `KeyError` escape for a removed key, and the client's exception for a removed
    Secret (`k8s_provider.py:596-602`);
  - `OnePasswordProvider` raises `ProviderLookupError` on the CLI path and lets the SDK's exception escape on
    the SDK path (`onepassword_provider.py:457-488`).

  The worked example below raises `ProviderLookupError`, which `B` allows and does not require.
- **`put_gem(self, name: str, value: Gem, *, criteria: Mapping[str, Any] | None = None, dry_run: bool = False)
  -> GemReference`.** Stores `value` as `name` and returns non-secret metadata. With `dry_run`, it validates and
  plans the write without persisting it. It never prints the value or puts it in the reference.

## Worked example

This example is illustrative, not a proposed provider. It lives in `tests/contract/example_providers.py`, so
the acceptance cases execute it. It reads gems from the current process environment.

```python
"""Expose the process environment as a provider; an example of the provider contract."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, ClassVar

from hiddengems.abstract_provider import AbstractGemProvider, WritableGemProvider
from hiddengems.abstraction import (
    DetectedProvider,
    DetectionEvidence,
    EvidenceSource,
    Gem,
    GemReference,
    ProviderLookupError,
    ProviderNotApplicable,
    ProviderState,
)

EXAMPLE_INSTANCE_ID = "environ:process"


class EnvironmentProvider(AbstractGemProvider):
    """Read gems from the current process environment."""

    name: ClassVar[str] = "environ"

    def __init__(self, **settings: Any) -> None:
        """:param settings: The detected record's settings; only ``instance_id`` is used."""
        self.instance_id = settings["instance_id"]

    @classmethod
    def detect(cls, **options: Any) -> tuple[DetectedProvider, ...]:
        """Report the process environment as one ready instance.

        :param options: Unused; the environment needs no configuration.
        :returns: One record, because the environment always exists.
        """
        evidence = DetectionEvidence(EvidenceSource.ENVIRONMENT, "Process environment")
        return (DetectedProvider(cls.name, EXAMPLE_INSTANCE_ID, ProviderState.READY, (evidence,), {}),)

    def find_gem(
        self, name: str, *, criteria: Mapping[str, Any] | None = None
    ) -> tuple[GemReference, ...]:
        """Return a reference when ``name`` is set; never read its value.

        :raises ProviderNotApplicable: When criteria are given; this provider has none.
        """
        if criteria:
            raise ProviderNotApplicable
        if name not in os.environ:
            return ()
        return (GemReference(name=name, provider=self.name, instance_id=self.instance_id),)

    def get_gem(self, reference: GemReference) -> list[Gem]:
        """Return the value at ``reference`` as a one-item list.

        :raises ProviderLookupError: When the variable was removed after the lookup.
        """
        try:
            return [os.environ[reference.name]]
        except KeyError as error:
            raise ProviderLookupError(
                "The environment variable is no longer set", "Set it again, then retry"
            ) from error


class WritableEnvironmentProvider(EnvironmentProvider, WritableGemProvider):
    """The same provider, declaring that it can store gems."""

    name: ClassVar[str] = "environ_writable"

    def put_gem(
        self,
        name: str,
        value: Gem,
        *,
        criteria: Mapping[str, Any] | None = None,
        dry_run: bool = False,
    ) -> GemReference:
        """Set ``name`` in the process environment, unless ``dry_run``.

        :raises ProviderNotApplicable: When criteria are given.
        :raises TypeError: When ``value`` is not a string; the environment stores strings only.
        """
        if criteria:
            raise ProviderNotApplicable
        if not isinstance(value, str):
            raise TypeError("The process environment stores strings only")
        if not dry_run:
            os.environ[name] = value
        return GemReference(name=name, provider=self.name, instance_id=self.instance_id)
```

- **What the example shows:**
  - detection evidence becomes a `DetectedProvider`;
  - `find_gem` returns references without reading values;
  - `get_gem` reads the values.
- **Inheritance:** the writable variant inherits `__init__`, `detect`, `find_gem`, and `get_gem`; it implements
  only `put_gem`. Its method resolution order is `WritableEnvironmentProvider`, `EnvironmentProvider`,
  `WritableGemProvider`, `AbstractGemProvider`.
- **Registration in this delivery:** a built-in provider is added to the `GemProvider.provider_types` tuple in
  `gem_provider.py`. That is the only path until `GAL-discovery`. Replacing the tuple, as
  `tests/test_hidden_gems_dotenv.py:328` does, works for tests and is what makes revision 3's legacy writer
  case reachable.
- **Libraries:** none. `os` is the only import beyond `hiddengems` itself.

## Scope

In scope:

- moving `put_gem` out of the base class;
- removing the three stubs;
- write capability as an observation with evidence;
- the check in `hide_gem` and the new error;
- the worked example;
- the acceptance tests for all of the above.

Out of scope. Each item ships with the feature that first calls it, because a hook declared without a caller
is an extension point with no consumer, which `software-design.md` forbids:

- `supported_os()`, `required_modules()`, and `OsName`: `GAL-sdk-optional`.
- `settings_type()`, `ProviderSettings`, and `UnknownSettingError`: `GAL-settings`.
- `selector_type()` and `ProviderSelector`: `GAL-selector`.
- `invalidate()`: `GAL-remember`.
- `VerifiableGemProvider` and `VerificationResult`: `GAL-verify`.
- `expand()`: `GAL-expand`.
- `target=` and the candidate target list in `hide_gem`: `GAL-write-target`.
- Removing `abstract_provier.py`: a separate owner decision under
  [Alternatives](#alternatives-for-the-owners-decision).
- The obligations under [Obligations owned by other features](#obligations-owned-by-other-features).

None of these hooks is available from this feature. A contributor implements only the members in the table
above.

## Layout

Option two changes these files. Option one changes none.

```text
~ src/hiddengems/abstract_provider.py            put_gem leaves the base; WritableGemProvider; write_capability
~ src/hiddengems/abstraction.py                  Capability, CapabilityState, CapabilityObservation; EvidenceSource.PROVIDER_CLASS
~ src/hiddengems/hidden_gems.py                  ProviderNotWritableError; capability check in hide_gem
~ src/hiddengems/gems/dotenv_provider.py         DotEnvProvider subclasses WritableGemProvider
~ src/hiddengems/gems/k8s_provider.py            put_gem stub removed
~ src/hiddengems/gems/onepassword_provider.py    put_gem stub removed
~ src/hiddengems/gems/keyring_provider.py        put_gem stub removed
+ tests/contract/example_providers.py            the worked example
+ tests/contract/test_provider_contract.py       acceptance cases
```

## Names, signatures, errors, and call sites

`src/hiddengems/abstract_provider.py`:

- **`AbstractGemProvider(ABC)`** keeps `name`, `__init__`, `detect`, `find_gem`, and `get_gem` exactly as in the
  table. `put_gem` is removed from it. Its docstring states that a provider that stores gems also subclasses
  `WritableGemProvider`.
- **New: `class WritableGemProvider(AbstractGemProvider)`.** It defines no `__init__` and holds no state. Its one
  abstract method, decorated with `@abstractmethod`, has the exact `put_gem` signature removed from the base.
  Errors are unchanged: this moves the method and changes no implementation of it.
- **New: `write_capability(provider_type: type[AbstractGemProvider]) -> CapabilityObservation`.** Never returns
  `None`. Its source is always `EvidenceSource.PROVIDER_CLASS`, because it reads only the class. The rules
  apply in this order:
  1. `provider_type` that is not a class, or not a subclass of `AbstractGemProvider`, raises `TypeError` with
     `WRITE_CAPABILITY_INPUT_ERROR`;
  2. a subclass of `WritableGemProvider` gives `SUPPORTED`, with `WRITE_SUPPORTED_REASON`;
  3. a class on which `inspect.getattr_static(provider_type, "put_gem", _MISSING)` finds a callable gives
     `UNKNOWN`, with `WRITE_LEGACY_REASON`. The lookup follows the method resolution order, so a `put_gem`
     the class inherits counts the same as one it defines. A method alone does not show that the class stores
     anything;
  4. any other class gives `UNSUPPORTED`, with `WRITE_UNSUPPORTED_REASON`.

  `_MISSING` is a module-private `object()` sentinel, so the lookup never uses `None`. The reasons are
  `Final[str]` constants in `abstract_provider.py`:
  - `WRITE_SUPPORTED_REASON = "Subclasses WritableGemProvider"`;
  - `WRITE_LEGACY_REASON = "Resolves put_gem without subclassing WritableGemProvider"`;
  - `WRITE_UNSUPPORTED_REASON = "Does not subclass WritableGemProvider and resolves no put_gem"`;
  - `WRITE_CAPABILITY_INPUT_ERROR = "write_capability needs an AbstractGemProvider subclass, not {value!r}"`.

  A structural `typing.Protocol` with `runtime_checkable` is rejected, because it reports any object with a
  `put_gem` attribute as writable, including a stub that only raises.

`src/hiddengems/abstraction.py`:

- **New: `class Capability(StrEnum)`** with `WRITE = "write"`. The features that add a capability add its
  member.
- **New: `class CapabilityState(StrEnum)`** with `SUPPORTED = "supported"`, `UNSUPPORTED = "unsupported"`, and
  `UNKNOWN = "unknown"`.
- **New: frozen dataclass `CapabilityObservation`**, with the fields `capability: Capability`,
  `state: CapabilityState`, `source: EvidenceSource`, and `reason: str`. The reason names classes only, never a
  gem or a value.
- **New: `EvidenceSource.PROVIDER_CLASS = "provider_class"`**, meaning that the provider's class declares it.
  The nine existing members are unchanged.

`src/hiddengems/hidden_gems.py`:

- **New: `class ProviderNotWritableError(NotImplementedError)`.**
  - `__init__(self, provider: str, instance_id: str, observation: CapabilityObservation) -> None`, storing all
    three as attributes.
  - Message: `f"Provider {provider!r} instance {instance_id!r} cannot store gems"`. Neither the gem name nor the
    value appears in it.
  - Subclassing `NotImplementedError` keeps existing `except NotImplementedError` handlers working.
- **New constant, legacy writer path only:** `LEGACY_WRITER_WARNING: Final[str]`, with the value
  `"{provider_class} defines put_gem without subclassing WritableGemProvider; subclass WritableGemProvider to
  keep writing"`, formatted with the class name.
- **Call site, `HiddenGems.hide_gem`:**
  1. Select the record exactly as today: `ProviderRequiredError` when no provider is given,
     `ProviderNotAvailableError` when none matches, and `AmbiguousGemError` when more than one matches.
  2. Look up that record's instance, and compute `write_capability(type(instance))`.
  3. `SUPPORTED`: call `put_gem` exactly as today.
  4. `UNKNOWN`: with the legacy writer path, emit `DeprecationWarning` with `LEGACY_WRITER_WARNING`, then call it.
     Without the path, raise `ProviderNotWritableError` carrying the observation.
  5. `UNSUPPORTED`: raise `ProviderNotWritableError` carrying the observation.
- **Callers of `put_gem`:** the only production caller is `hidden_gems.py:351`. The only test caller is
  `tests/test_hidden_gems_dotenv.py:442`, on `DotEnvProvider` directly.
- **Built-in transitions:**
  - `DotEnvProvider` subclasses `WritableGemProvider`; `put_gem` is unchanged.
  - `KubernetesProvider`, `OnePasswordProvider`, and `KeyringProvider` lose their stubs and stay read-only.

## Abstraction-extension gate

This section follows the [gate](../provider-routing-design.md#abstraction-extension-gate) of the overview. The
baseline is commit `4438234`.

### 1. Extensions

#### Removal of `AbstractGemProvider.put_gem`

- **Signature:** `put_gem(self, name: str, value: Gem, *, criteria: Mapping[str, Any] | None = None,
  dry_run: bool = False) -> GemReference` leaves `AbstractGemProvider` and becomes the one abstract member of
  `WritableGemProvider`, with the same signature and the same docstring obligation.
- **Status:** MUST for a provider that stores gems; absent from every other provider.
- **Behavior:** unchanged for every writer. `DotEnvProvider.put_gem` (`dotenv_provider.py:681`) is not changed.
- **Implementation owner:** `abstract_provider.py`. The stubs at `k8s_provider.py:610-625`,
  `onepassword_provider.py:489-498`, and `keyring_provider.py:216-232` are removed.
- **Actual caller:** `HiddenGems.hide_gem` (`hidden_gems.py:351`). The one test caller is
  `tests/test_hidden_gems_dotenv.py:442`, on `DotEnvProvider`.
- **Acceptance tests:** `test_only_dotenv_is_writable`, `test_read_only_providers_have_no_put_gem`,
  `test_writable_provider_without_put_gem_is_abstract`, and the three existing dotenv write tests.

#### `WritableGemProvider`

- **Signature:** `class WritableGemProvider(AbstractGemProvider)` in `abstract_provider.py`, with the abstract
  `put_gem` above. It defines no `__init__` and holds no state.
- **Status:** optional base class; a provider that stores gems MUST subclass it.
- **Behavior:** declares that a provider stores gems. It adds no behavior.
- **Implementation owner:** `abstract_provider.py`; `DotEnvProvider` subclasses it.
- **Actual caller:** `write_capability`, rule 2.
- **Acceptance tests:** `test_only_dotenv_is_writable`, `test_writable_provider_without_put_gem_is_abstract`, and
  `test_example_providers_follow_the_contract`.

#### `write_capability`

- **Signature:** `def write_capability(provider_type: type[AbstractGemProvider]) -> CapabilityObservation` in
  `abstract_provider.py`.
- **Status:** MUST ADD.
- **Behavior:** the four rules under [Names, signatures, errors, and call sites](#names-signatures-errors-and-call-sites).
  It returns `CapabilityObservation(capability=Capability.WRITE, state=<the rule's state>,
  source=EvidenceSource.PROVIDER_CLASS, reason=<the rule's constant>)`. It reads only the class, never an
  instance, and never returns `None`.
- **Implementation owner:** `abstract_provider.py`.
- **Actual caller:** `HiddenGems.hide_gem`, step 2.
- **Acceptance tests:** `test_write_capability_reports_each_state`,
  `test_write_capability_rejects_a_non_provider_class`, and
  `test_inherited_legacy_writer_registered_through_provider_types`.

#### `Capability`, `CapabilityState`, `CapabilityObservation`, and `EvidenceSource.PROVIDER_CLASS`

- **Signature:** as listed under `src/hiddengems/abstraction.py` above.
- **Status:** MUST ADD.
- **Behavior:** the typed result of a capability check; values only, no behavior.
- **Implementation owner:** `abstraction.py`.
- **Actual caller:** `write_capability` builds them; `ProviderNotWritableError` carries one.
- **Acceptance tests:** `test_write_capability_reports_each_state`.

#### `ProviderNotWritableError` and `LEGACY_WRITER_WARNING`

- **Signature:** `class ProviderNotWritableError(NotImplementedError)` with
  `__init__(self, provider: str, instance_id: str, observation: CapabilityObservation) -> None`, and
  `LEGACY_WRITER_WARNING: Final[str]`, both in `hidden_gems.py`.
- **Status:** MUST ADD; the constant only with the legacy writer path.
- **Behavior:** as listed under `src/hiddengems/hidden_gems.py` above.
- **Implementation owner:** `hidden_gems.py`.
- **Actual caller:** `HiddenGems.hide_gem`, steps 4 and 5.
- **Acceptance tests:** `test_hide_gem_rejects_read_only_provider_before_any_call`,
  `test_legacy_writer_registered_through_provider_types`, and
  `test_inherited_legacy_writer_registered_through_provider_types`.

#### `HiddenGems.hide_gem`

- **Signature:** unchanged.
- **Status:** behavior change.
- **Behavior:** the five steps listed under `src/hiddengems/hidden_gems.py` above.
- **Implementation owner:** `hidden_gems.py`.
- **Actual caller:** the caller of the library.
- **Acceptance tests:** the cases of the two entries above, and the three existing dotenv write tests.

### 2. What each extension extends

`WritableGemProvider` is a subclass of `AbstractGemProvider`, awaiting the owner's approval with this proposal.
Everything else is a function, a type, or an error that uses `AbstractGemProvider` without changing it. `Δ`
removes one member from `B`, `put_gem`, and adds it to that subclass unchanged. The factory is unchanged.

### 3. Providers in P

| Provider | Applicable contract | `write_capability` | Change |
| --- | --- | --- | --- |
| `OnePasswordProvider` | `AbstractGemProvider` | `UNSUPPORTED` | stub removed |
| `DotEnvProvider` | `WritableGemProvider` | `SUPPORTED` | base class changes |
| `KeyringProvider` | `AbstractGemProvider` | `UNSUPPORTED` | stub removed |
| `KubernetesProvider` | `AbstractGemProvider` | `UNSUPPORTED` | stub removed |

`get_gem` of each keeps its baseline error behavior, as listed in the contributor contract.

### 4. Baseline tests whose expectations change

None. The only baseline uses of `put_gem` and `hide_gem` are in `tests/test_hidden_gems_dotenv.py`, all on
`DotEnvProvider` or `HiddenDotFileGem`, which stay writable. No baseline test calls the three removed stubs.

### 5. Baseline

The baseline stays commit `4438234`. This proposal does not redefine `B`.

## Exceptions

One new class and one reuse, under the overview's exception rules:

- **New: `ProviderNotWritableError(NotImplementedError)`,** in `hidden_gems.py`.
  - Needed because a caller of `hide_gem` must tell "this provider cannot store gems" apart from "no provider
    matched" and from a failure inside a writer.
  - Closest existing class: `ProviderNotAvailableError(LookupError)` (`abstraction.py:12`), which means that no
    provider matched the selection. Here exactly one matched, and it cannot store.
  - Base: `NotImplementedError`, because the three stubs raise it today (`k8s_provider.py:610-625`,
    `onepassword_provider.py:489-498`, `keyring_provider.py:216-232`). An existing `except NotImplementedError`
    still catches it.
  - Attributes `provider`, `instance_id`, and `observation`; the message names the provider and instance only.
  - Cases: `test_hide_gem_rejects_read_only_provider_before_any_call` asserts that it is a `NotImplementedError`
    and that neither the gem name nor the value is in its message.
- **Reused: `TypeError`** from `write_capability` for an input that is not an `AbstractGemProvider` subclass.
  This is a programming error in the caller, which is what `TypeError` already means.
- **Warning reused: `DeprecationWarning`** with `LEGACY_WRITER_WARNING`, under the legacy writer path. A legacy
  writer is a deprecated way to declare writing, which is what `DeprecationWarning` already means.

The check that enforces the rules for every feature is delivered here, because this is the first feature that
adds a class:

- **`test_exception_classes_match_the_register`,** in `tests/contract/test_provider_contract.py`.
  - Input: every module under `hiddengems`, imported with `pkgutil.walk_packages`.
  - It collects each class derived from `BaseException` whose `__module__` starts with `hiddengems`, warnings
    included, as `(module, name, direct bases)`.
  - Expected: equal to `EXPECTED_EXCEPTIONS`, a literal set in the test. It holds the nine classes of `E_B` with
    their bases, plus `("hiddengems.hidden_gems", "ProviderNotWritableError", ("NotImplementedError",))`.
  - An undeclared class, a changed base, or a missing baseline class fails. Each later feature that adds a class
    adds its entry to this set in its own pull request.

## Before and after

What this feature adds, against today's contract:

- **`AbstractGemProvider.put_gem`:** before, an abstract MUST. After, not in the base class.
- **`WritableGemProvider`:** before, none. After, a new class whose only member is the abstract `put_gem`, with
  the unchanged signature.
- **`EvidenceSource`:** before, nine members. After, the same nine, plus `PROVIDER_CLASS`.
- **New names:** `Capability`, `CapabilityState`, `CapabilityObservation`, `write_capability`,
  `ProviderNotWritableError`, and, with the legacy writer path, `LEGACY_WRITER_WARNING`.
- **`HiddenGems.hide_gem`:** before, it calls `put_gem` unconditionally. After, it checks `write_capability`
  first. Its signature and its `GemReference` result are unchanged.
- **Unchanged:**
  - the records `LookupResult`, `GemReference`, and `DetectedProvider`;
  - the result shapes of `detect`, `find_gem`, `get_gem`, `put_gem`, `inspect_gem`, `resolve_gem`, `dig_gem`, and
    `hide_gem`.

## Obligations owned by other features

These are required, but not delivered here. This feature changes no lookup or read path.

- **Runtime check of provider returns:** proposed as a new feature, `GAL-return-gate`.
  - It checks what `detect`, `find_gem`, `get_gem`, and `put_gem` return before the library uses or returns it:
    container and record types, provider and instance identity, and declared metadata fields. It also checks the
    partial matches carried by `ProviderLookupError`.
  - Proposed names, to be fully specified in its own proposal:
    - module `src/hiddengems/contract_check.py`;
    - `check_detected(provider_type, records) -> tuple[DetectedProvider, ...]`;
    - `check_references(instance, references) -> tuple[GemReference, ...]`;
    - `check_values(instance, values) -> list[Gem]`;
    - `check_reference(instance, reference) -> GemReference`;
    - `ProviderContractError(RuntimeError)`, with `provider`, `instance_id`, `member`, and a secret-free
      `reason`.
  - Today nothing checks these returns. `GemProvider.detect` (`gem_provider.py:55-59`), `inspect_gem`
    (`hidden_gems.py:248,260`), `dig_gem` (`320-322`), and `hide_gem` (`351-353`) use them directly.
- **Every found location stays a candidate:** owned by `GAL-scope`.
  - When x is found in three places, whether in one instance or across providers, all three references stay in
    `LookupResult.matches` and in `AmbiguousGemError.result`, in deterministic order.
  - An unchecked scope keeps the confirmed matches alongside its issue.
  - Today's code already keeps them (`hidden_gems.py:248,260,272,303`).
- **The caller's exact choice:** owned by `GAL-chooser`. `choice=` does not exist in today's facade.

## Libraries

None. `abc`, `enum`, `dataclasses`, and `warnings` are in the standard library.

## Behavior and compatibility

- **`hide_gem` against a read-only provider.** Old: it calls `put_gem`, which raises `NotImplementedError` with a
  provider-specific message. New: it raises `ProviderNotWritableError`, still a `NotImplementedError`, before any
  provider call, carrying the capability observation. Behavior change; a strengthening for callers.
- **The provider contract.** The MUST set of `AbstractGemProvider` loses `put_gem`; a provider that stores gems
  declares `WritableGemProvider`. Contract change: a relaxation for implementers, who stop writing stubs, and a
  strengthening for callers.
- **Direct calls to a read-only provider's `put_gem`,** outside `hide_gem`, now raise `AttributeError` instead of
  `NotImplementedError`. No such call exists in the repository.
- **Third-party providers can be affected today.** `GemProvider.provider_types` (`gem_provider.py:22`) is a
  public class attribute that a caller can replace, and `tests/test_hidden_gems_dotenv.py:328` replaces it.
  `GemProvider.create` (`gem_provider.py:73-84`) builds whatever class that tuple holds, and `HiddenGems` builds
  every instance through it (`hidden_gems.py:146`). So a class derived from today's `AbstractGemProvider` with a
  real `put_gem`, registered that way, is written to by `hide_gem` today. `write_capability` reports it
  `UNKNOWN`.
  - With the legacy writer path, it keeps working and gets a `DeprecationWarning`. Its migration is a one-line
    change: subclass `WritableGemProvider` instead of `AbstractGemProvider`.
  - Without it, `hide_gem` raises `ProviderNotWritableError` for that class until it makes the same change.
    That is a compatibility break, disclosed here for the owner's decision.
- **`EvidenceSource` gains a member.** An additive change to a shared enum; code that compares against the
  existing members is unaffected.
- **Gates:** unchanged.

## Alternatives for the owner's decision

The `put_gem` decision:

- **Option one: keep `put_gem` in `AbstractGemProvider`.**
  - Files: none. Behavior: unchanged. The three read-only providers keep their stubs, which stay non-compliant
    with `python-standards-contract.md`.
  - Consequence: this feature has no production capability left, and tests alone cannot land, so `GAL-plugin`
    dissolves. `test_every_registered_provider_is_concrete` moves to `GAL-discovery`, and `GAL-write-target`
    keeps today's stub behavior.
- **Option two: move `put_gem` to `WritableGemProvider`,** as specified above. Recommended: it removes the
  incomplete implementations, and the check in `hide_gem` rests on declared evidence, not on what a stub does.

Legacy writers, a decision that applies only with option two:

- **With the legacy writer path (recommended).** A provider that `write_capability` reports as `UNKNOWN` is
  still written to, with a `DeprecationWarning`. Existing behavior is kept for every caller. The built-in
  read-only providers report `UNSUPPORTED` and are refused. A third-party stub that only raises
  `NotImplementedError` still raises it from the call, as it does today. Removing the path later is its own
  decision.
- **Without it.** Only `SUPPORTED` providers are written to. A legacy writer registered through
  `provider_types` raises `ProviderNotWritableError` until its author changes the base class.

Read errors of `get_gem` (finding GP-14), a decision that applies to either option:

- **(a) Keep the baseline here (recommended).** `B` defines no error for a location that is gone, and this
  feature changes no read path, so its providers match `C`. The uniform error becomes the proposed feature
  `GAL-read-errors`, listed under [Dependencies on other features](#dependencies-on-other-features).
- **(b) Add the uniform error to this feature's `Δ`.** `get_gem` MUST raise `ProviderLookupError` when the
  location or key is gone.
  - Owners: `KubernetesProvider.get_gem` translates `KeyError` and the client's `ApiException` with status
    404; `OnePasswordProvider.get_gem` translates the SDK's exception on the SDK path.
  - Acceptance cases: `test_kubernetes_get_gem_reports_a_removed_key_as_lookup_error` and
    `test_onepassword_sdk_get_gem_reports_a_failure_as_lookup_error`.
  - Consequence: two read paths change in a feature about writing.

Removing `src/hiddengems/abstract_provier.py`, a separate decision:

- The file defines a second `AbstractGemProvider` with older signatures: `find_gem` returns
  `GemReference | None`, and `get_gem` returns one `Gem`. Nothing in the repository imports it. That does not
  prove that nothing outside the repository does.
- Options:
  - (a) delete it in this feature;
  - (b) delete it in a separate cleanup feature;
  - (c) keep it, and make importing it emit a `DeprecationWarning` naming `hiddengems.abstract_provider`.
- Recommendation: (b). This capability does not need the removal, and one pull request delivers one
  capability.

## Acceptance cases

All cases are in `tests/contract/test_provider_contract.py` and apply to option two.

- **Enumeration:** `GemProvider.provider_types`. Importing it imports the four provider modules. `kubernetes` is a
  required dependency imported at the top of `k8s_provider.py`; the 1Password SDK is imported lazily inside
  `OnePasswordProvider`; the Keychain library loads only when `KeyringProvider` is instantiated. No unavailable
  SDK is imported.
- **Class-level checks:** checks on classes, not instances, so no provider is instantiated and the Keychain
  library never loads.
- **Test providers:** registered with the `GemProvider.create` monkeypatch pattern already used in
  `tests/test_hidden_gems_routing.py`.

Cases:

- `test_every_registered_provider_is_concrete`.
  - Input: each class in `GemProvider.provider_types`.
  - Expected: `inspect.isabstract(cls)` is false, and `cls.name` is a non-empty string, unique across the tuple.
- `test_only_dotenv_is_writable`.
  - Input: the four classes.
  - Expected: `issubclass(cls, WritableGemProvider)` is true for `DotEnvProvider` and false for the other three.
- `test_read_only_providers_have_no_put_gem`.
  - Input: the three read-only classes.
  - Expected: `hasattr(cls, "put_gem")` is false.
- `test_writable_provider_without_put_gem_is_abstract`.
  - Input: a test class that subclasses `WritableGemProvider` and implements everything except `put_gem`.
  - Expected: `inspect.isabstract` is true, and instantiating it raises `TypeError`.
- `test_write_capability_reports_each_state`.
  - Input: `DotEnvProvider`, `KubernetesProvider`, and a legacy test class that defines `put_gem` without
    `WritableGemProvider`.
  - Expected:
    - `SUPPORTED`, `UNSUPPORTED`, and `UNKNOWN` respectively;
    - each with `source == EvidenceSource.PROVIDER_CLASS` and a non-empty reason naming no gem;
    - none is `None`.
- `test_hide_gem_rejects_read_only_provider_before_any_call`.
  - Input: `HiddenGems` holding one record of a read-only test provider that records every method call;
    `hide_gem("X", "fake-value", provider=...)` naming that provider.
  - Expected:
    - it raises `ProviderNotWritableError`, also a `NotImplementedError`;
    - its observation has state `UNSUPPORTED`;
    - the provider recorded no call;
    - neither `"X"` nor `"fake-value"` appears in the message.
- `test_legacy_writer_registered_through_provider_types`.
  - Input: a test class derived from `AbstractGemProvider`, not `WritableGemProvider`, whose `put_gem` records its
    call and returns a reference; `GemProvider.provider_types` replaced with a tuple holding it, through
    `monkeypatch.setattr` as in `tests/test_hidden_gems_dotenv.py:328`; `HiddenGems` holding one record of it;
    `hide_gem("X", "fake-value", provider=...)`.
  - Expected with the legacy writer path: `write_capability` reports `UNKNOWN`; one `DeprecationWarning` whose
    message names the class and contains neither `"X"` nor `"fake-value"`; `put_gem` called once; its reference
    returned.
  - Expected without it: `ProviderNotWritableError` with state `UNKNOWN`; `put_gem` never called.
- `test_inherited_legacy_writer_registered_through_provider_types`.
  - Input: a legacy test class as in the previous case, and a concrete subclass of it that defines no
    `put_gem` of its own. `GemProvider.provider_types` is replaced with a tuple holding the subclass, and
    `HiddenGems` builds its one instance through `GemProvider.create`. Then
    `hide_gem("X", "fake-value", provider=...)`.
  - Expected with the legacy writer path: `write_capability(subclass)` reports `UNKNOWN` with
    `WRITE_LEGACY_REASON`; one `DeprecationWarning` naming the subclass; the inherited `put_gem` called once;
    its `GemReference` returned.
  - Expected without it: `ProviderNotWritableError` with state `UNKNOWN`; `put_gem` never called.
- `test_exception_classes_match_the_register`, as specified under [Exceptions](#exceptions).
- `test_write_capability_rejects_a_non_provider_class`.
  - Input: `object`, the string `"dotenv"`, and a class with a `put_gem` that does not subclass
    `AbstractGemProvider`.
  - Expected: each raises `TypeError` with `WRITE_CAPABILITY_INPUT_ERROR`.
- `test_example_providers_follow_the_contract`.
  - Input: `tests/contract/example_providers.py`, with `monkeypatch.setenv("EXAMPLE_GEM", "fake-value")`.
  - Expected:
    - both classes are concrete;
    - `EnvironmentProvider` gives one ready record, one reference for `EXAMPLE_GEM`, and `["fake-value"]` from
      `get_gem`;
    - its write capability is `UNSUPPORTED`, and the writable variant's is `SUPPORTED`;
    - the variant's `put_gem` with `dry_run=True` leaves the environment unchanged, and without it sets the
      variable;
    - neither returned reference contains the value.
- **Writes through dotenv still work.** Input: the existing write tests, unchanged:
  `test_dotenv_provider_put_returns_only_reference_metadata`,
  `test_add_and_overwrite_preserve_other_entries_and_comments`, and
  `test_dry_run_neither_creates_nor_changes_files`. Expected: they pass.

Existing tests changed: none. No existing test exercises the three stubs.

Cases that check that detection is passive, deterministic, and free of secrets belong to `GAL-discovery`, where
loaded providers are validated. There, record equality is well defined: no built-in sets
`DetectionEvidence.observed_at` (`abstraction.py:48`, default `None`), and a provider that does set it is
compared with `observed_at` normalized to `None`.

## Dependencies on other features

- **Requires:** nothing.
- **Required by:**
  - `GAL-write-target` uses `WritableGemProvider`, `write_capability`, and `ProviderNotWritableError` unchanged,
    and adds `target=` and the candidate target list.
  - `GAL-discovery` validates loaded classes against `AbstractGemProvider` as defined here.
  - `GAL-explain` and `GAL-cli-targets` report `CapabilityObservation` values per target.
- **Hooks:** the features listed under [Scope](#scope) define their own hooks when their proposals are revised.

Proposed new features. They are not in the feature index and need the owner's approval before they are added:

- **`GAL-return-gate`:** the runtime check of provider returns, under
  [Obligations owned by other features](#obligations-owned-by-other-features).
- **Keychain existence check:** `find_gem` tests whether an item exists without reading its value, so one
  `dig_gem` reads the Keychain once instead of twice.
- **Keychain duplicate items:** enumerate same-name items, give each a distinct reference, and read by that
  reference. The existence check alone does not provide this.
- **`GAL-read-errors`:** `get_gem` raises `ProviderLookupError` when the location or key is gone, in every
  built-in provider. Its owners and cases are option (b) of the read-error decision above. It extends `B`,
  so it is locked until its proposal declares the five parts of the gate.
- **Implicit preference records:** a typed record that providers return for native-tool defaults such as
  `KUBECONFIG`, consumed by `GAL-scope`, kept separate from the open precedence decision
  `GAL-explicit-implicit`.
