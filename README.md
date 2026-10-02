# Hidden Gems Storage

Small useful library to store and fetch secrete aka gems in different storage
provider i.e. k8s secret, 1password, dotfile etc.

## Package

`hidden-gems` is built through `pyproject.toml` with scikit-build-core.
A wheel contains the `demos.hiddengems` Python package on every supported
platform. On macOS, CMake also builds and installs `libkeychainorpasswordread.dylib` 
beside the Keychain provider so the `ctypes` binding can load it as a package resource.
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

docs indicate detail design

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
