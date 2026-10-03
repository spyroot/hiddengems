# GAL-plugin: writable capability for providers

Status: proposal, revision 2. Not approved for implementation. The owner chooses between the two alternatives
in [Alternatives](#alternatives-for-the-owners-decision); this document recommends option two. The overview of
all features is [provider-routing-design.md](../provider-routing-design.md).

## Purpose and observable capability

A provider declares through its class whether it can store gems, and `hide_gem` refuses a read-only provider
before calling into it.

- **Observable capability (option two):** `HiddenGems.hide_gem(name, value, provider=...)` against a read-only
  provider raises `ProviderNotWritableError`, a subclass of `NotImplementedError`, without calling the provider.
- **For a provider author:** the classes alone state what to implement. Every provider implements
  `AbstractGemProvider`; a provider that stores gems also subclasses `WritableGemProvider`.
- **Today:** `AbstractGemProvider.put_gem` is abstract (`abstract_provider.py:51-65`). The Kubernetes, 1Password,
  and Keychain providers implement it only to raise `NotImplementedError` (`k8s_provider.py:610-625`,
  `onepassword_provider.py:489-498`, `keyring_provider.py:216-232`). `python-standards-contract.md` says a
  concrete class must not present an incomplete implementation as supported.

## Scope

In scope: moving `put_gem` out of the base class, removing the three stubs, the check in `hide_gem`, the new
error, and the acceptance tests for them.

Out of scope. Revision 1 declared these in this feature; revision 2 moves each one to the feature that first
calls it, because a hook declared without a caller is an extension point with no consumer, which
`software-design.md` forbids, and one pull request delivers one capability:

- `supported_os()`, `required_modules()`, and `OsName` with its platform mapping: `GAL-sdk-optional`.
- `settings_type()`, `ProviderSettings`, and `UnknownSettingError`: `GAL-settings`.
- `selector_type()` and `ProviderSelector`: `GAL-selector`.
- `invalidate()`: `GAL-remember`.
- `VerifiableGemProvider` and `VerificationResult`: `GAL-verify`.
- `expand()`: dropped. `GAL-kube-contexts` expands contexts inside `KubernetesProvider` and calls no hook.
- `target=` and the list of candidate targets in `hide_gem`: `GAL-write-target`.
- Implicit preference records, a Keychain existence check, and Keychain duplicate items: proposed as separate
  features under [Dependencies](#dependencies-on-other-features); not part of this delivery.
- Removing `abstract_provier.py`: a separate owner decision under
  [Alternatives](#alternatives-for-the-owners-decision); this capability does not need it.

## Layout

Option two changes these files. Option one changes none.

```text
~ src/hiddengems/abstract_provider.py            put_gem leaves AbstractGemProvider; WritableGemProvider added
~ src/hiddengems/hidden_gems.py                  ProviderNotWritableError; capability check in hide_gem
~ src/hiddengems/gems/dotenv_provider.py         DotEnvProvider subclasses WritableGemProvider
~ src/hiddengems/gems/k8s_provider.py            put_gem stub removed
~ src/hiddengems/gems/onepassword_provider.py    put_gem stub removed
~ src/hiddengems/gems/keyring_provider.py        put_gem stub removed
+ tests/contract/test_provider_contract.py       acceptance cases
```

## Names, signatures, errors, and call sites

`src/hiddengems/abstract_provider.py`:

- **`AbstractGemProvider(ABC)` keeps, unchanged:**
  - `name: ClassVar[str]`;
  - abstract `__init__(self, **settings: Any) -> None`;
  - abstract classmethod `detect(cls, **options: Any) -> Tuple[DetectedProvider, ...]`;
  - abstract `find_gem(self, name: str, *, criteria: Mapping[str, Any] | None = None) -> tuple[GemReference, ...]`;
  - abstract `get_gem(self, reference: GemReference) -> List[Gem]`.
- **Changed:** `AbstractGemProvider.put_gem` is removed. The class docstring states that a provider that stores
  gems also subclasses `WritableGemProvider`.
- **New:** `class WritableGemProvider(AbstractGemProvider)`. It defines no `__init__` and holds no state. Its one
  abstract method, decorated with `@abstractmethod`, has the exact signature removed from the base:
  `put_gem(self, name: str, value: Gem, *, criteria: Mapping[str, Any] | None = None, dry_run: bool = False)
  -> GemReference`.
  - Contract, carried over from today's docstring: store `value` as `name` and return non-secret metadata;
    `dry_run` validates and plans the write without persisting it; never print the value or include it in the
    reference.
  - Errors: unchanged. This feature moves the method; it does not change any implementation of it.
- **Capability test:** `isinstance(instance, WritableGemProvider)`. A structural `typing.Protocol` with
  `runtime_checkable` is rejected: it passes for any object that has a `put_gem` attribute, including a stub that
  only raises.

`src/hiddengems/hidden_gems.py`:

- **New:** `class ProviderNotWritableError(NotImplementedError)`.
  - `__init__(self, provider: str, instance_id: str) -> None`, storing the attributes `provider: str` and
    `instance_id: str`.
  - Message: `f"Provider {provider!r} instance {instance_id!r} cannot store gems"`. Neither the gem name nor the
    value appears in it.
  - Subclassing `NotImplementedError` keeps existing `except NotImplementedError` handlers working. This
    replaces the `TypeError` base that the overview proposed earlier.
- **Call site, `HiddenGems.hide_gem`:**
  1. Select the record exactly as today: `ProviderRequiredError` when no provider is given,
     `ProviderNotAvailableError` when none matches, `AmbiguousGemError` when more than one matches.
  2. Look up that record's instance.
  3. If `not isinstance(instance, WritableGemProvider)`, raise
     `ProviderNotWritableError(record.provider, record.instance_id)`.
  4. Otherwise call `put_gem` exactly as today.
- **Callers of `put_gem`:** the only production caller is `hidden_gems.py:351`, found by searching the
  repository for `.put_gem(`. The only test caller is `tests/test_hidden_gems_dotenv.py:442`, on `DotEnvProvider`
  directly.

Built-in transitions under option two:

- `DotEnvProvider`: subclasses `WritableGemProvider` instead of `AbstractGemProvider`; `put_gem` unchanged.
- `KubernetesProvider`, `OnePasswordProvider`, and `KeyringProvider`: the `put_gem` stub is removed; each stays a
  direct subclass of `AbstractGemProvider` and is read-only.

## Libraries

None. `abc` is already in use.

## Behavior and compatibility

- **`hide_gem` against a read-only provider.** Old: it calls `put_gem`, which raises `NotImplementedError` with a
  provider-specific message. New: it raises `ProviderNotWritableError`, still a `NotImplementedError`, before
  any provider call. Behavior change; a strengthening for callers.
- **The provider contract.** The MUST set of `AbstractGemProvider` loses `put_gem`; a provider that stores gems
  declares `WritableGemProvider`. Contract change: a relaxation for implementers, who stop writing stubs, and a
  strengthening for callers.
- **Direct calls to a read-only provider's `put_gem`,** outside `hide_gem`, now raise `AttributeError` instead of
  `NotImplementedError`. No such call exists in the repository.
- **Third-party providers:** none is affected today. `GemProvider.create` (`gem_provider.py:73-82`) builds only
  the four types in the closed `provider_types` tuple and raises `ValueError` for any other name, and
  `HiddenGems` builds every instance through it (`hidden_gems.py:146`). A third-party provider can be registered
  only after `GAL-discovery`, which lands later and documents this contract for authors.
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
  incomplete implementations, and the check in `hide_gem` no longer depends on what a stub does.

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
- **The read-only test provider:** registered with the `GemProvider.create` monkeypatch pattern already used in
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
- `test_hide_gem_rejects_read_only_provider_before_any_call`.
  - Input: `HiddenGems` holding one record of a read-only test provider that records every method call;
    `hide_gem("X", "fake-value", provider=...)` naming that provider.
  - Expected:
    - it raises `ProviderNotWritableError`, which is also a `NotImplementedError`;
    - the provider recorded no call;
    - neither `"X"` nor `"fake-value"` appears in the message.
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
  - `GAL-write-target` uses `WritableGemProvider` and `ProviderNotWritableError` unchanged, and adds `target=` and
    the candidate target list.
  - `GAL-discovery` validates loaded classes against `AbstractGemProvider` as defined here.
- **Hooks:** the features listed under [Scope](#scope) define their own hooks when their proposals are revised.

Proposed new features. They are not in the feature index and need the owner's approval before they are added:

- **Keychain existence check:** `find_gem` tests whether an item exists without reading its value, so one
  `dig_gem` reads the Keychain once instead of twice.
- **Keychain duplicate items:** enumerate same-name items, give each a distinct reference, and read by that
  reference. The existence check alone does not provide this.
- **Implicit preference records:** a typed record that providers return for native-tool defaults such as
  `KUBECONFIG`, consumed by `GAL-scope`, kept separate from the open precedence decision
  `GAL-explicit-implicit`.
