# Provider abstraction decisions

The provider interface is `AbstractGemProvider` in
[`src/hiddengems/abstract_provider.py`](../src/hiddengems/abstract_provider.py).
The factory is `GemProvider` in
[`src/hiddengems/gem_provider.py`](../src/hiddengems/gem_provider.py), and callers use
`HiddenGems` in [`src/hiddengems/hidden_gems.py`](../src/hiddengems/hidden_gems.py).
This document records the abstraction and typing decisions accepted before plugin implementation.
The original design remains in [README.md](README.md).

## Extend the existing interface

A provider MUST extend `AbstractGemProvider` or an explicitly approved subclass and be constructed through
`GemProvider.create()`. Adding another provider must not introduce a parallel provider contract or make the
router construct a concrete implementation directly.

The existing interface keeps these members:

| Member | Required implementation | Input and result | Caller |
| --- | --- | --- | --- |
| `name: ClassVar[str]` | MUST | Stable provider kind shared by its records and references | Factory |
| `__init__(**settings: Any)` | MUST | Settings for one detected instance; returns `None` | `GemProvider.create` |
| `detect(**options: Any)` | MUST, class method | Detection options; returns `tuple[DetectedProvider, ...]` | `GemProvider.detect` |
| `find_gem(name, *, criteria=None)` | MUST | Name and read-only criteria; returns `tuple[GemReference, ...]` | `HiddenGems.inspect_gem` |
| `get_gem(reference)` | MUST | One selected reference; returns `Sequence[Gem]` | `HiddenGems.dig_gem` |
| `put_gem(name, value, *, criteria=None, dry_run=False)` | MUST | Name, value and write options; returns `GemReference` | `HiddenGems.hide_gem` |

An inherited concrete implementation satisfies a MUST. A provider does not need an empty override just to
repeat its parent's method. Detection describes concrete instances; references describe locations without
containing their secret values.

### Keep `put_gem` in the base

The accepted choice is option one in [GAL-plugin](gal/GAL-plugin.md#alternatives-for-the-owners-decision):
`put_gem` stays abstract in `AbstractGemProvider`. There is no new `WritableGemProvider` requirement.
Read and write operations belong to the same provider interface; adding plugin loading does not justify
replacing that interface or requiring an existing writer to change its base class.

Implementing the method and supporting storage are distinct. Dotenv currently implements storage; the
Kubernetes, 1Password, and Keychain implementations explicitly raise `NotImplementedError` for writes.
Retaining those failures preserves current behavior and does not advertise working writes for those providers.
`dry_run=True` on a provider that supports writing plans and validates the write without persisting it.

The proposed `target=` selector belongs on `HiddenGems.hide_gem` under `GAL-write-target`, as described in
[the delivery specification](provider-routing-design.md#gal-write-target). It is not an extra `put_gem`
parameter in this typing refactor. Optional verification, deadlines, refresh, and cache hooks also need their
own declared extensions; they are not additional MUST methods of today's base class.

## Read-only input, mutable working result

Import `Mapping`, `MutableMapping`, and `Sequence` from `collections.abc`. Use built-in generic types such as
`tuple[T, ...]`, `list[T]`, `dict[K, V]`, and `type[T]` instead of the deprecated aliases in `typing`.
Syntax must continue to support the Python 3.11 minimum declared in `pyproject.toml`; the Python 3.12
`type Alias = ...` syntax is therefore not used here.

Use `Mapping[str, Any]` where an operation only reads supplied entries. This allows an ordinary dictionary,
`MappingProxyType`, or another mapping implementation without requiring the caller to provide a mutable object.
Factory and router settings accept that interface. `Any` at this boundary represents provider-specific options;
it does not replace the named result records defined in
[`abstraction.py`](../src/hiddengems/abstraction.py).

`HiddenGems._selection()` has this fixed return contract:

```python
def _selection(
    selected: str | Mapping[str, Any] | None,
) -> tuple[str | None, str | None, MutableMapping[str, Any]]:
    ...
```

Every successful branch returns exactly three elements:

| Input | Provider | Instance ID | Criteria |
| --- | --- | --- | --- |
| `None` | `None` | `None` | Fresh `{}` |
| Provider name | That name | `None` | Fresh `{}` |
| Preference mapping | Its `provider` | Its `instance_id` | Fresh dictionary excluding those two keys |

The third element is always mutable and never `None` or a provider name. The caller can therefore use it
without branch-dependent checks:

```python
selected_type, selected_id, selected_criteria = self._selection(preferred)
selected_criteria.update(criteria or {})
```

`MutableMapping` declares the mutation the caller needs. `Mapping` would incorrectly hide `.update()` from
the interface. Building a fresh dictionary prevents that update from modifying the caller's preference mapping.
Explicit call criteria replace matching top-level preference criteria. This is a shallow copy and merge;
nested objects are not recursively copied or merged.

Annotations are not runtime validation. `_selection()` currently validates the provider name but does not
validate the type of `instance_id`. The proposed provider return-validation work remains separate; this
refactor does not claim to reject every malformed record or unexpected key at runtime.

## Return a sequence; preserve gem values

`AbstractGemProvider.get_gem(reference: GemReference) -> Sequence[Gem]` exposes what the consumer needs:
an ordered collection of values. A provider may return a list or tuple. Existing providers return lists and
remain compatible. A single gem is wrapped as one element; a bare string is not the intended result container.

`HiddenGems.dig_gem()` retains its public `list[Gem]` result by applying `list(...)` only to the outer sequence.
For example, a provider result `(["a", "b"],)` becomes `[["a", "b"]`. The inner list is one gem; it must
not be flattened or split into separate candidates.

## Register classes; detect instances

The factory registry has this annotation:

```python
provider_types: ClassVar[tuple[type[AbstractGemProvider], ...]]
```

Each entry is a provider class. `tuple[T, ...]` permits any number of entries of the same declared type;
it does not describe a nested tuple or a collection of provider instances. One registered class can detect
several configured instances. `GemProvider.create(record)` selects the class by the record's provider name
and builds the instance using its settings and instance ID.

The registry currently contains four built-in classes. Runtime entry-point loading is the separate
[GAL-discovery delivery](provider-routing-design.md#gal-discovery). It must feed the existing factory and
validate loaded classes against this same abstraction. This typing change does not implement that loader.

## Preserve caller choice and evidence

`HiddenGems.inspect_gem()` returns `LookupResult`, whose `matches` and `issues` contain confirmed references
and explanations of unchecked instances. All matching locations remain available for caller inspection.

- An explicit provider or configured preference limits the lookup as it does today.
- One confirmed match with no unchecked instance is sufficient for automatic resolution.
- Multiple matches require caller selection; non-interactive resolution raises `AmbiguousGemError` carrying
  the result. Interactive resolution asks the caller to choose.
- An unchecked applicable instance prevents complete resolution, even when another instance has a match.

A detected provider that cannot be queried, for example because the required SDK or executable is unavailable,
has an unknown lookup outcome, not a confirmed absence. Current code represents that through
`ProviderLookupError` and `LookupIssue`. `EvidenceSource`, defined in `abstraction.py`, still records how the
provider was detected; a lookup failure does not erase known configuration or environment evidence.
`ProviderState` currently has `DETECTED`, `CONFIGURED`, and `READY`; this decision adds no new enum member.

## Extension boundary and acceptance

The [abstraction-extension gate](gal/GAL-plugin.md#abstraction-extension-gate) retains its approved baseline
`443823457ea17d503add3e7c08e6331cfd7ccf39`. The approved typing changes widen provider reads to `Sequence[Gem]`,
accept read-only mapping settings, and declare the mutable normalized criteria. They preserve the existing
factory and write interface. They do not redefine that baseline.

Every later extension must identify its concrete signature, behavior, implementation file, actual caller,
and acceptance tests before implementation. Its scope must say what is added, extended, or modified.
Incompatible requirements in two specification sections must be presented as a conflict before the affected
change proceeds; an implementation must not silently choose one or rewrite the contract to pass its own gate.

[`tests/test_hidden_gems_typing.py`](../tests/test_hidden_gems_typing.py) covers fresh mutable criteria,
read-only and custom mapping inputs, and outer-sequence normalization. Existing routing tests retain the
preference, ambiguity, and unchecked-provider behavior. Passing those tests is execution evidence; the
annotations and this document alone are not.
