# Hidden Gems Storage

Small useful library to store and fetch secrete aka gems in different storage
provider i.e. k8s secret, 1password, dotfile etc.

## Package

`hidden-gems` is built through `../../pyproject.toml` with scikit-build-core.
A wheel contains the `demos.hiddengems` Python package on every supported
platform. On macOS, CMake also builds and installs
`libkeychainorpasswordread.dylib` beside
the Keychain provider so the `ctypes` binding can load it as a package resource.
The 1Password SDK remains optional; the CLI and SDK are detected independently.

The package contract is validated without network access by building a wheel,
inspecting its metadata and files, installing it into an isolated directory,
and importing it outside the source tree. Native macOS validation loads the
bundled library and checks its ABI without reading a Keychain item.

## Abstraction

(1) Gems - API tokens | secretes | File with api , thus String | List | Dict | IO
GemProvide either remote or local where store Gems.
GemProvider provide abstraction , to detect locally install provider AND from
read config ~/.gem_provider.json
HiddenGem main interface from abstraction point that caller call.

## Current design

Default behavior

- MRO should land on correct provider when store.
- Caller should be explicit ask for provider "1password" "k8s" "dotnev" ...
  where .. is other integration.

Lookup - i.e. dig_gem , if we detected 3 local provider

- if detected N provider and same gem defined (1) resolved in all N provider or
  N - y (i.e, detected 4 provider same Gem in 2)

caller resolve via preference, i.e.

- gem_provider.json store mapping Gem name - to preferred provider.
- if preference not resolved via preference in gem_provider.json , we ask caller
  to resolve iteratively.

Example:

        API key my_openai_api.key is symbolic name resolved in N or N -y, we 
        need resolve preferred, thus, we promote.
        Provider and it name, my_openai_api , if available a date where caller 
        stored, if given provider store creation date if not that entry 
        is empty.

More Concretely

Let:

    P = {p₁, p₂, ..., pₙ}

be the set of detected and configured providers, where:

    |P| = N

For a gem named `g`, define:

    R(g) = {p ∈ P | p contains g}

Therefore:

    R(g) ⊆ P

If the gem exists in only some providers, then:

    |R(g)| = N - y

where `y` is the number of available providers that do not contain `g`, and:

    0 ≤ y ≤ N

The library does not need to expose `N - y` directly. It is notation describing
that the lookup result is a subset of all available providers.

## Storage behavior

Storage requires an explicit provider:

    hide_gem("openai_api_key", value, provider="onepassword")

The library must not choose a storage provider based on inheritance order or
provider registration order.

If no provider is specified, `hide_gem` raises `ProviderRequiredError`.

## Union

If we know the full reference is complete SET ()
i.e., we know a cluster, namespace, file name i.e. each provider specific,
if we don't, it means we have subset () hence detect logic detects for a missing
piece
for each provider to make SET complete.

note it does mean it resolves duplicate or if the same x is present.
matches (X) = 1Password (X) ∪ Kubernetes (X) ∪ Keychain (X) ∪
dotenv-file-1 (X) ∪ dotenv-file-2 (X) ...

detect technically is tractable, but we need bound.

- We don't scan the entire system for files or pieces.
- Have some time bound.
- Example:
  dotenv we scan from the parent dir to all children for all dot files.
  for k8s we check env, know location, i.e. we do not use some
  random location and scan the entire disk. (note that can yield positive
  lookup)

Basic and more advanced use cases.

Example caller needs an API key we define it as x_api_key_name,
and users have 1password account, k8s, dotenv, and keyring.

The most basic use case if x_api_key_name defines only in one of the providers.
Note during detecting and get if 1password, for example, have no SDK or disabled
we have know prior to know if x_api_key_name is there or not, hence
our tool reports this uncertainty.

similar case for our tool to fetch from a keyring user needs to provider
his password in macOS UI keychain UI, if that call is rejected or canceled
by the user.

i.e. tool can only return what it can access and read, i.e.
tool cannot know which value is correct or most recent.

## Look up behavior

For:

    dig_gem(g)

the library discovers `R(g)` and applies the following rules.

### No result

If:

    |R(g)| = 0

then no provider contains the requested gem. Raise `GemNotFoundError`.

### One concrete result

If:

    |R(g)| = 1

then the lookup resolves to exactly one concrete gem. Retrieve and return it
without prompting the caller.

### Multiple results

If:

    |R(g)| > 1

then the name is ambiguous because multiple providers contain a gem with that
symbolic name.

Resolution proceeds in this order:

1. Check whether the caller explicitly selected a provider.
2. Check the preferred provider mapped to the gem in
   `~/.gem_provider.json`.
3. If interactive resolution is enabled, ask the caller to select one of the
   providers in `R(g)`.
4. Otherwise, raise `AmbiguousGemError` containing candidate metadata.

A configured preference is valid only when:

    preferred_provider(g) ∈ R(g)

A stale preference pointing to a provider that does not contain the gem must
not silently fall back to a different provider.

## Interactive resolution

The prompt should display only non-secret metadata:

- Provider name
- Symbolic gem name
- Creation date, when supported by the provider
- Modification date, when supported
- Optional provider-specific location or label

The gem value must never be displayed.

If a provider does not expose a creation date, the field remains empty.

After the caller selects a provider, the library may optionally save that
selection as the preference for future lookups.

## Behavioral specification: provider detection

We target 3 operating system i.e set OS is set ("linux", "macos", "window11")

1) Each SET OS provide subset of available provider, so set provider condition on
   a availability SDK in python , retrieval capability etc, this set OS each element
   set can provider subset of provider.

2) each provider has N way where we can define which defined and deterministic.

    - location of provider in local storage, since we're using same uid, min
      expectation that we can detect presence of file.
    - hence for each provider we have fix combination we can define we can detect.

### Supported operating systems

Let the supported operating-system set be:

    O = {linux, macos, windows11}

The current operating system is:

    o ∈ O

Let `P` be the set of all provider implementations known to Hidden Gems:

    P = {p₁, p₂, ..., pₙ}

Each operating system supports a subset of[118;1:3u those providers:

    P(o) = {p ∈ P | p supports o}

Therefore:

    P(o) ⊆ P

For example, macOS Keychain may belong to `P(macos)` but not to
`P(linux)` or `P(windows11)`.

Detection evaluates only providers in `P(o)`.

### Provider detection methods

Each provider `p` defines a finite, deterministic set of local detection
methods:

    D(p) = {d₁, d₂, ..., dₘ}

where each `dᵢ` examines one possible source of evidence.

Possible evidence includes:

- Explicit configuration supplied by the caller.
- Provider configuration in `~/.gem_provider.json`.
- Environment variables.
- Executables found through `PATH`.
- Executables in operating-system-specific locations.
- Importable Python SDKs.
- Known configuration files or directories.
- Operating-system services or native credential APIs.
- Previously remembered discovery information.

The number and combination of methods are provider-specific:

    |D(p)| = mₚ

A provider must explicitly define:

1. Which detection methods it supports.
2. The order in which those methods are evaluated.
3. Which combination of evidence establishes availability.
4. Which evidence identifies separate provider instances.
5. Whether a check is passive, local, or requires active verification.

Detection must not perform an unrestricted filesystem search. Each provider
must declare the finite paths, environment variables, commands, SDKs, and
operating-system facilities that it knows how to inspect.

### Detection evidence

Each detection method returns zero or more evidence records.

An evidence record contains non-secret information such as:

- Provider type.
- Provider instance identifier.
- Evidence source.
- Executable path.
- Configuration path.
- SDK name and version.
- Account, vault, context, or profile name when locally available.
- Time at which the evidence was observed.
- Whether the referenced path still exists.
- Whether the provider is readable by the current user.

Secret values, authentication tokens, passwords, and private-key contents
must never be stored as detection evidence.

Because Hidden Gems runs under the caller's user identity, the minimum file
check is:

    exists(path) ∧ readable_by_current_uid(path)

On Windows, the equivalent check uses the current user's access token rather
than a POSIX UID.

File existence alone is evidence of configuration; it does not prove that
the provider is authenticated or remotely reachable.

### Provider states

Detection classifies each provider instance into one of the following states.

#### Unsupported

The provider does not support the current operating system:

    p ∉ P(o)

Unsupported providers are not evaluated further.

#### Absent

The provider supports the operating system, but no current local evidence was
found:

    p ∈ P(o) ∧ E(p) = ∅

where `E(p)` is the current evidence set for provider `p`.

#### Detected

At least one valid local indication of the provider was found:

    E(p) ≠ ∅

This may mean that an executable, SDK, configuration file, or native facility
exists. It does not necessarily mean that gems can already be retrieved.

#### Configured

The minimum provider-specific configuration required to identify a storage
target is available.

For example:

- Kubernetes has at least one readable kubeconfig.
- 1Password has a locally discoverable account or explicit account setting.
- Dotenv has a specific readable file.
- A system keyring has a usable local backend.

#### Ready

The provider appears capable of retrieval using only permitted local checks.

`Ready` does not guarantee that a later remote operation will succeed.
Credentials may expire and remote services may become unavailable after
detection.

Default detection must not:

- Connect to Kubernetes.
- Contact 1Password services.
- Retrieve a secret.
- Unlock a keychain.
- Display an authentication prompt.
- Execute a kubeconfig credential plugin.
- Ask for a password or biometric confirmation.

Remote reachability and authentication verification belong to a separate,
explicit verification operation.

### Availability predicate

Each provider defines a deterministic availability predicate:

    available(p) = Fₚ(E(p))

where:

- `E(p)` is the accumulated evidence.
- `Fₚ` is the provider-specific rule.
- The same evidence must produce the same result.

Availability must not be inferred from provider registration order or Python
inheritance order.

Examples:

    available(dotenv)
        = readable configured dotenv file exists

    available(kubernetes)
        = readable kubeconfig exists
          OR explicit Kubernetes configuration exists

    available(onepassword)
        = `op` executable exists
          OR supported 1Password SDK is installed

    available(keyring)
        = Python keyring SDK exists
          AND a usable keyring backend can be identified

Finding `kubectl` without a kubeconfig is still useful evidence, but does not
by itself identify a configured Kubernetes storage target.

### Detection source order

Detection examines sources in the following deterministic order:

1. Explicit caller input.
2. Configuration in `~/.gem_provider.json`.
3. Environment variables.
4. Operating-system facilities.
5. Executables and Python SDKs.
6. Known provider-specific paths.
7. Remembered discovery evidence.

This order defines precedence when two sources provide conflicting values for
the same provider field.

It does not stop discovery after the first match. All declared detection
methods are evaluated so that all available provider instances can be
returned.

For a conflicting value:

    caller input
        > configuration file
        > environment
        > live system discovery
        > remembered evidence

Remembered evidence is always the weakest source. It may suggest where to
look, but it cannot prove that a provider remains installed or configured.

### Provider instances

One provider implementation may produce multiple configured instances.

For provider `p`, let:

    I(p) = {i₁, i₂, ..., iₖ}

be the set of detected instances of that provider.

Examples include:

- Multiple kubeconfig files.
- Multiple Kubernetes contexts.
- Multiple 1Password accounts.
- Multiple dotenv files.
- Multiple encrypted gem stores.

The complete detection result is:

    A(o) = {(p, i) | p ∈ P(o) ∧ i ∈ I(p) ∧ available(i)}

Each returned instance must have a stable identity derived from non-secret
configuration, such as:

    provider type + canonical configuration path + account/context identifier

Equivalent instances found through multiple detection methods must be
canonicalized and returned only once.

### Remembered detection

`~/.gem_provider.json` may remember successfully discovered provider
instances so later detection can use the same locations and identities.

Remembered information may include:

- Provider type.
- Stable instance identifier.
- Canonical configuration path.
- Account, profile, or context name.
- Last observed timestamp.
- Last known detection state.
- Non-secret provider settings.

It must not include:

- Gem values.
- Authentication tokens.
- Passwords.
- Private-key contents.
- Values copied from environment variables containing credentials.

A remembered provider is a detection candidate, not automatically an
available provider.

For every remembered provider `r`, detection performs a local revalidation:

    valid(r) = provider-specific local evidence still exists

If:

    valid(r) = true

the provider can be included in the current result.

If:

    valid(r) = false

the remembered entry may remain as historical configuration, but it must not
be returned as currently available.

### Detection cache invalidation

Remembered detection must be refreshed when any of the following occurs:

- The caller requests `refresh=True`.
- The operating system changes.
- The current user changes.
- A remembered executable or configuration path disappears.
- Relevant explicit configuration changes.
- Relevant environment variables change.
- The provider detector version changes.
- The remembered result exceeds its configured lifetime.

File modification time may be recorded as evidence, but it must not be treated
as proof that the file contents or credentials remain valid.

### Preferences and detection

Provider preferences and provider detection solve different problems.

Detection determines:

    which provider instances are currently available

Preference determines:

    which available provider should resolve a particular gem

For a gem `g`, a remembered preference is valid only if:

    preferred_provider(g) ∈ R(g)

A preference must not cause an absent provider to be reported as detected.

However, a configured preference may cause its referenced provider location
to be examined as an additional detection candidate.

### Detection result

Detection returns every currently available provider instance together with
its non-secret evidence:

    GemProvider.detect() → tuple[DetectedProvider, ...]

The result order must be deterministic:

1. Explicit caller order, when provided.
2. Configured provider order.
3. Provider type name.
4. Stable provider instance identifier.

The ordering exists for reproducibility and display. It must not implicitly
select a provider for gem storage or retrieval.

That show that any Any future provider can now be added without changing this
contract; it only needs to define \(D (p)\), \(F_p (E (p))\), instance
identity, and supported operating systems.
