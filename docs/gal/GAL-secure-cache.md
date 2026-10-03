# GAL-secure-cache: encrypted value cache in front of provider reads

Status: proposal, revision 4. Not approved for implementation. The owner decides the choices in
[Alternatives](#alternatives-for-the-owners-decision). The overview of all features is
[provider-routing-design.md](../provider-routing-design.md). This proposal is written to its
[Abstraction-extension gate](../provider-routing-design.md#abstraction-extension-gate) and its exception rules.
The cache surface below is the owner's; this document gives every part its exact signature, behavior, owner,
caller, and acceptance test.

Revision 4 applies the second round of decisions. With `remember=True` and a `choice`, `dig_gem` saves the choice
after `get_or_load` returns, on a hit as on a miss. Providers in P states each applicable contract as `C`
restricted to the classes the provider subclasses. Cases S20 and S21 cover `HiddenGems.invalidate` and the profile
builders, and the async gate row names the cases that run on `AsyncSecretCache`. Every hold of the generation
fence has a deadline and a stated result when it expires. The `selection` keys are named constants, and a
`UserWarning` filter still catches `SecretCacheWarning`. The Keychain prompts once per period only under Keychain
option (b) of `GAL-parallel`. `canonical_json` comes from `GAL-chooser`, correcting revision 3.

Revision 3 aligns this proposal with the other feature proposals. It is delivered at plan 6.5, after
`GAL-masked-value`, and `dig_gem` has its final signature. `selection` adds `target` and `choice`, and
`routing_revision` adds `GemConfig.targets`, `GemConfig.routes`, and `GemConfig.discovery_local`. The loader
calls `_resolve_within` and reads by `deadline_capability`, so the cache wait, the resolution, and the read
share one deadline. `Deadline` is imported from `GAL-parallel`, not declared here. `canonical_json` comes from
`GAL-remember`, and `atomic_file.rewrite_file` from `GAL-chooser`. The gate lists the profile builders, both
exception classes, and one row per provider. `SecretCacheWarning` has a constructor and a message constant,
both exceptions have their register entries, and the key bits, `NO_EPOCH`, and the codec tags are named
constants. A new alternative covers what one authentication unlocks, `AbstractAsyncSecretCache` is marked an
owner-mandated exception, and S15 uses a deadline-aware writer. `GAL-encrypted-store` now requires this feature.

Revision 2 adds three rules from the owner, and corrects one definition:

- **Per-call refresh.** `get_or_load` and `dig_gem` take `refresh: bool = False`. A caller that expects the
  value to change outside the library passes `refresh=True`, on every call if it wants. That skips the hit,
  reads the provider, and replaces the cached record.
- **The caller's choice holds for the period.** A record keeps the reference that served it. The next
  identical request gets that reference's cached value until the period ends, without resolving again.
- **A new duplicate does not invalidate.** If the same gem appears in another provider during the period,
  the cached choice stands. See [Choice within a period](#choice-within-a-period).
- **Correction:** `routing_revision` covered the detected records in revision 1. A newly detected duplicate
  would then have changed every request and ended the cached choice. It now covers only the explicit routing
  configuration.

## Purpose and observable capability

A gem read 200 times in one period costs one provider lookup and one value read, so it prompts for a 1Password
unlock once, not 200 times. The Keychain prompts once only under Keychain option (b) of `GAL-parallel`; under
option (a), the recommended one, an item that needs a prompt is reported UNKNOWN with `KEYCHAIN_LOCKED_NEXT_ACTION`
and is never cached.

- **Observable capability:**
  - with a cache given to `HiddenGems`, 200 `dig_gem("X")` calls with an unchanged request produce one
    `find_gem` and one `get_gem`;
  - `HiddenGems.invalidate()`, a write through `hide_gem`, or the end of the period makes the next read go to
    the provider again;
  - without a cache, every read goes to the provider, exactly as today.
- **Today:**
  - every `dig_gem` runs `resolve_gem`, which calls `find_gem` on every in-scope instance, and then `get_gem`
    (`hidden_gems.py:305-322`);
  - the Keychain reads a value in `find_gem` and again in `get_gem` (`keyring_provider.py:168-215`), so 200
    reads of one item can prompt 400 times. This is `GAL-case-repeated-reads`. The cache cuts these to one prompt
    only under Keychain option (b); under option (a), no lookup shows a Keychain prompt;
  - nothing caches a value. "No caching of gem values" is a non-goal, and this feature relaxes it only when the
    caller passes a cache.

## Flow

```text
HiddenGems.dig_gem
    └── AbstractSecretCache.get_or_load
          ├── valid hit → open and decode the cached result
          └── miss → _resolve_within + the read by deadline_capability, on the same deadline
                         │
                         └── commit the successful result, if its generation is still current
```

`inspect_gem` and `resolve_gem` never use the cache. A failed lookup is never cached: `AmbiguousGemError`,
`IncompleteGemLookupError`, `StaleGemPreferenceError`, and `GemNotFoundError` reach the caller as today.

## Choice within a period

The rule is generic: it holds for any providers `p₁` and `p₂`, built-in or third-party.

```text
t₀  dig_gem("x")                    R(x) = {p₁}        miss → resolves to p₁, reads, caches (x → p₁)
t₁  dig_gem("x")                    R(x) = {p₁}        hit → p₁'s cached value; no find_gem, no get_gem
t₂  x also appears in p₂            R(x) = {p₁, p₂}    nothing happens in the cache
t₃  dig_gem("x")                    R(x) = {p₁, p₂}    hit → p₁'s cached value; the caller's choice holds
t₄  dig_gem("x", refresh=True)      R(x) = {p₁, p₂}    resolves again → AmbiguousGemError with both candidates;
                                                       the caller decides
```

- **Why the choice holds:** the record was made by a successful resolution and belongs to the caller's period.
  Choosing between `p₁` and `p₂` is the caller's decision, and the caller has already made one for this period.
- **What ends it:** `refresh=True` on a call, `invalidate()`, a write through `hide_gem`, a change to the
  explicit routing configuration, or the end of the period.
- **Relation to `docs/README.md`:** it forbids a silent choice among duplicates. Within a period, an earlier
  choice outlives a duplicate that appeared later. This happens only when the caller passes a cache. It is
  bounded by the period, and `refresh=True` brings the ambiguity back at once. Contract impact lists it as a
  relaxation.
- **Example names:** with `p₁` the 1Password instance and `p₂` a dotenv file, x keeps coming from 1Password
  until one of the events above.

## Scope

In scope:

- the typed records, the public cache contract, and the six replaceable dependencies, all declared below;
- the concrete `SecretCache`, `AsyncSecretCache`, and `PassThroughSecretCache`;
- adapters for AES-256-GCM and ChaCha20-Poly1305, the JSON codec, memory and file stores, memory and file key
  stores, the fixed-period lifetime, and thread and file coordinators;
- the `cache=` parameter of `HiddenGems`, and the cache step in `dig_gem`, `hide_gem`, and `invalidate`;
- the blocking contract suite, `tests/contract/test_secret_cache_contract.py`.

Out of scope:

- any change to `AbstractGemProvider`, the factory, or a provider. No provider is edited;
- remembered detection. Values and keys are never written to `~/.gem_provider.json`, which `docs/README.md`
  reserves for non-secret metadata, and this feature shares no storage with `GAL-remember`;
- deleting an expired key while no process runs. See [Expiry and deletion](#expiry-and-deletion).

## Libraries

No cryptographic code is written here. Every primitive comes from a library:

Both AEAD classes are in `cryptography.hazmat.primitives.ciphers.aead`.

| Need | Library and call |
| --- | --- |
| AES-256-GCM | `AESGCM`: `generate_key(bit_length=CACHE_KEY_BITS)`, `encrypt`, `decrypt` |
| ChaCha20-Poly1305 | `ChaCha20Poly1305`: `generate_key`, `encrypt`, `decrypt` |
| Tamper detection | `cryptography.exceptions.InvalidTag`, raised by `decrypt` on altered data or metadata |
| Nonces and ids | `secrets.token_bytes`, `secrets.token_hex` |
| Request identity | `hmac.new(key, message, hashlib.sha256)` |
| Atomic files | `atomic_file.rewrite_file` from `GAL-chooser`: temporary file, `mode`, `os.replace` |
| File locks | `fcntl.flock` with `LOCK_EX` and `LOCK_NB`, polled until the deadline |
| Async | `asyncio.Lock`, `asyncio.shield`, `asyncio.timeout`, `asyncio.to_thread` |

- **Checked:** in the `hiddengems` environment, `cryptography` 50.0.2 provides both AEAD classes with
  `generate_key`, `encrypt`, and `decrypt`. The tag adds 16 bytes, a ChaCha20-Poly1305 key is 32 bytes, and
  wrong associated data raises `InvalidTag`.
- **Packaging:** `cryptography>=50.0` in a new extra `cache`, imported lazily inside the two adapters. Without
  the extra, building either adapter raises `ModuleNotFoundError` naming `hiddengems[cache]`.

## Records

In `src/hiddengems/secret_cache.py`, all frozen dataclasses:

- **`CacheRequest(name: str, selection: str, routing_revision: str)`.**
  - `selection` is `canonical_json` of the mapping from the `SELECTION_*_KEY` constants:
    `{SELECTION_PROVIDER_KEY: provider, SELECTION_TARGET_KEY: target, SELECTION_CRITERIA_KEY: criteria,
    SELECTION_PREFERENCE_KEY: HiddenGems.preferences.get(name), SELECTION_CHOICE_KEY: choice}`.
  - `routing_revision` is the SHA-256 hex digest of `canonical_json` of the explicit routing configuration:
    the merged `providers` settings, `HiddenGems.preferences`, `GemConfig.targets`, `GemConfig.routes`, and
    `GemConfig.discovery_local`, all delivered earlier. These are the inputs the caller and the config file
    control. It does not cover the detected records, so detection finding a new instance or a new copy of a
    gem changes no request.
  - `GAL-chooser`'s `choice`, delivered earlier, is part of `selection`, so a request made with a choice is a
    different request from one made without it.
  - `canonical(self) -> bytes` returns `canonical_json` of the three fields, encoded as UTF-8.
  - `canonical_json` is the shared function in `src/hiddengems/canonical_json.py`, from `GAL-chooser`.
- **`ResolvedGem(reference: GemReference, values: list[Gem])`.**
- **`CacheEpoch(generation: str, created_at: datetime, expires_at: datetime, key_id: str)`.**
  - `generation` and `key_id` come from `secrets.token_hex(CACHE_ID_BYTES)`.
  - `created_at` and `expires_at` are UTC, with `expires_at = created_at + TTL`, fixed when the epoch is made.
- **`EpochKeys(seal: bytearray, index: bytearray)`,** two independent random keys. `seal` comes from the
  cipher's `generate_key`, and `index` from `secrets.token_bytes(CACHE_INDEX_KEY_BYTES)`. Not frozen, so
  `wipe()` can zero both in place.
- **`AcquiredKey(epoch: CacheEpoch, keys: EpochKeys)`** and **`KeyRejection(state: KeyState, epoch: CacheEpoch,
  reason: str)`**. When no key exists, `epoch` is `NO_EPOCH`, the module constant
  `CacheEpoch(generation=NO_EPOCH_ID, created_at=EPOCH_ORIGIN, expires_at=EPOCH_ORIGIN, key_id=NO_EPOCH_ID)`.
  `is_valid(NO_EPOCH)` is false.
- **`CacheRecord(identity: str, reference: GemReference, epoch: CacheEpoch, codec: CodecId, cipher: CipherId,
  metadata: bytes, payload: bytes)`.**
  - `identity` is `hmac.new(keys.index, request.canonical(), hashlib.sha256).hexdigest()`. A file name never
    shows a gem name.
  - `metadata` is `canonical_json` of `CACHE_RECORD_FORMAT`, `identity`, `reference`, `epoch`, `codec`, and
    `cipher`, encoded as UTF-8. It is passed to the cipher as associated data, so it is authenticated.
  - `payload` is the adapter's output: its nonce followed by the ciphertext and tag.
- **`CacheMiss(reason: MissReason)`**, the result of a read that found no usable record.

Enums, each a `StrEnum`:

- `CipherId`: `AES_256_GCM = "aes-256-gcm"` and `CHACHA20_POLY1305 = "chacha20-poly1305"`;
- `CodecId`: `JSON_V1 = "json-v1"`;
- `KeyState`: `ABSENT = "absent"`, `EXPIRED = "expired"`, and `REVOKED = "revoked"`;
- `MissReason`: `ABSENT = "absent"`, `MALFORMED = "malformed"`, and `OTHER_EPOCH = "other_epoch"`;
- `CommitState`: `COMMITTED = "committed"`, `RETIRED = "retired"`, and `FULL = "full"`;
- `HoldState`: `HELD = "held"` and `EXPIRED = "expired"`.

Type aliases: `GemLoader = Callable[[Deadline], ResolvedGem]` and
`AsyncGemLoader = Callable[[Deadline], Awaitable[ResolvedGem]]`.

Imported, not declared here: `Deadline` from `hiddengems.abstraction` (`GAL-parallel`).

## Lifetime invariant

For an epoch `e` created at `t_e`, its deadline is `d_e := t_e + TTL`. A record is usable at time `t` if and
only if all four hold:

```text
usable(record, t)  ⟺  t < d_e
                     ∧ current(record.epoch)
                     ∧ keyActive(record.epoch.key_id)
                     ∧ authentic(record)
```

- Every record of an epoch expires no later than its key, because both use `d_e`. A late insertion or a hit
  never extends `d_e`. Expiry is fixed-period, not sliding.
- `current` is decided by the key store: the generation of the key that `acquire` returns.
- `keyActive` is false once the key is expired or revoked, so `acquire` rejects it.
- `authentic` holds when the cipher's `open` succeeds over the payload, with the metadata as associated data.
- A clock that reads earlier than `t_e` counts as expired.

**Retirement order.** Expiry and invalidation run three steps in this order, under the generation fence:

1. retire the generation, with `key_store.revoke(key_id)`. From then on `acquire` rejects the key, and
   `commit_if_current` refuses every record of that generation;
2. destroy the key, with `key_store.destroy(key_id)`;
3. delete the records, with `store.delete_epoch(epoch)`.

A load that started before step 1 can finish later. Its commit then finds the generation retired and publishes
nothing. Its own caller still receives the values it loaded, but no other request receives them as a hit.

### Expiry and deletion

Enforcement and deletion are separate guarantees:

- **Enforced always:** every `acquire` checks `d_e` with the lifetime's clock and rejects an expired key. An
  expired key is never used, whether or not its file still exists.
- **Deleted on the next operation:** the first `get_or_load`, `expire`, `invalidate`, or `close` after `d_e`,
  in any process using the same root, runs the retirement order for that epoch.
- **Not claimed:** a key file is not removed at `d_e` while no process runs. A timestamp does not delete bytes,
  and this feature schedules nothing outside the process. Startup cleanup only shortens the window.
- **Destroying a key file** overwrites its bytes with zeros, calls `os.fsync`, unlinks it, and calls `fsync` on
  the directory. On a copy-on-write filesystem, an SSD, or in a backup, older copies of the blocks can
  survive. The guarantee is that the key is no longer readable through the filesystem; it is not forensic
  erasure.

## Public cache contract

Both cache contracts are abstract base classes, using `@abstractmethod` the way `AbstractGemProvider` does. A
class that leaves an operation unimplemented cannot be instantiated.

### `AbstractSecretCache`

| Operation | Signature | Behavior |
| --- | --- | --- |
| `get_or_load` | `(self, request, loader, *, deadline, refresh=False) -> list[Gem]` | below |
| `invalidate` | `(self) -> None` | retires the current epoch: revoke, destroy, delete |
| `expire` | `(self) -> None` | runs the retirement order for every epoch past `d_e`; others untouched |
| `close` | `(self) -> None` | releases holds and wipes in-memory keys; persisted epochs stay usable by others |

The full signature is `get_or_load(self, request: CacheRequest, loader: GemLoader, *, deadline: Deadline,
refresh: bool = False) -> list[Gem]`.

`invalidate`, `expire`, and `close` hold the generation fence with `Deadline.after(LOOKUP_TIMEOUT_SECONDS)` and
raise `TimeoutError` with `CACHE_WAIT_TIMEOUT_REASON` on `HoldState.EXPIRED`; `HiddenGems.invalidate` collects it
with its other step errors, and `hide_gem` lets it propagate after the write.

**`SecretCache.get_or_load`**, the concrete orchestration:

1. `key_store.acquire(lifetime.is_valid)`. On a `KeyRejection`, enter the generation fence and acquire again,
   because another process may have made a new epoch meanwhile. On `HoldState.EXPIRED`, raise `TimeoutError`
   with `CACHE_WAIT_TIMEOUT_REASON`. If it is still rejected:
   - finish the retirement order for the rejected epoch, when it has one. `revoke`, `destroy`, and
     `delete_epoch` are idempotent, so repeating steps another process already ran is harmless;
   - make a new epoch: `lifetime.new_epoch(key_id)`, `cipher.generate_key()` for `seal`,
     `secrets.token_bytes` for `index`, then `key_store.create(epoch, keys)`.
2. Compute `identity` with the index key.
3. Enter `coordinator.hold(identity, deadline)`. On `HoldState.EXPIRED`, raise
   `TimeoutError` with `CACHE_WAIT_TIMEOUT_REASON`. Waits for the same identity are serialized, and
   different identities proceed at the same time.
4. With `refresh=True`, skip the read and go to step 5. Otherwise `store.read(identity, epoch)`. A
   `CacheRecord` that passes `cipher.open` and `codec.decode` is a hit: return
   the decoded values. Each call decodes again, so the caller gets new objects. A `CacheMiss`, a
   `CacheIntegrityError`, or an expired epoch is a miss.
5. On a miss, or with `refresh=True`, call `loader(deadline)`. Its exceptions propagate unchanged, and nothing
   is stored. A refresh that fails leaves the existing record in place.
6. Encode the values. A value the codec does not support, such as an `IO` stream, raises `TypeError`; the
   values are then returned without storing. A payload larger than `CACHE_MAX_VALUE_BYTES` is not stored
   either.
7. Seal the payload with the metadata as associated data. Enter the generation fence, call
   `key_store.acquire(lifetime.is_valid)` again, and commit with `store.commit_if_current(record,
   current=<that epoch>)`. On `HoldState.EXPIRED`, publish nothing and go to step 8. A rejection, `RETIRED`, or
   `FULL` publishes nothing. Because `invalidate` and `expire` revoke inside the same fence, a commit can never
   land in a retired generation.
8. Return the loaded values.

The orchestration contains no cipher name, no file path, and no provider name. It sees only the six injected
dependencies.

### `AbstractAsyncSecretCache`

The same four operations as `async def`, with `loader: AsyncGemLoader` and the same `refresh` keyword.
`AsyncSecretCache` behaves as above, and also:

- coordination inside the loop uses one `asyncio.Lock` per identity; the file coordinator's waits run through
  `asyncio.to_thread`;
- the owner's load runs as one task, and waiters await it through `asyncio.shield`. Cancelling a waiter never
  cancels the owner. If the owner is cancelled or fails, the hold is released and the next waiter loads;
- the loader runs under `asyncio.timeout(deadline.remaining())`;
- storage and cipher calls run through `asyncio.to_thread`, so the event loop keeps running;
- `close()` cancels the tasks it owns and awaits them.

### `PassThroughSecretCache`

`get_or_load` returns `loader(deadline).values`; `invalidate`, `expire`, and `close` do nothing.
`PASS_THROUGH_CACHE` is its module-level instance and the default of `HiddenGems(cache=...)`.

## Replaceable dependencies

`SecretCache(cipher, codec, store, key_store, lifetime, coordinator)` receives all six through its constructor.
`AsyncSecretCache` takes the same six.

### `AbstractCacheCipher`

- `cipher_id: ClassVar[CipherId]`.
- `generate_key(self) -> bytearray`.
- `seal(self, key: bytes, plaintext: bytes, associated_data: bytes) -> bytes`. The adapter generates the nonce
  with `secrets.token_bytes` at its algorithm's length, and returns nonce followed by ciphertext and tag.
- `open(self, key: bytes, sealed: bytes, associated_data: bytes) -> bytes`. Raises `CacheIntegrityError` when
  the library raises `InvalidTag`, or when `sealed` is shorter than nonce plus tag.
- Adapters: `AesGcmCipher` (`AESGCM`, 12-byte nonce, 32-byte key) and `ChaCha20Poly1305Cipher`
  (`ChaCha20Poly1305`, 12-byte nonce, 32-byte key).

### `AbstractGemCodec`

- `codec_id: ClassVar[CodecId]`.
- `encode(self, values: list[Gem]) -> bytes`. Raises `TypeError` for a value it does not support.
- `decode(self, data: bytes) -> list[Gem]`. Raises `CacheIntegrityError` for data it cannot decode.
- Adapter `JsonGemCodec`. Each value becomes a typed node `{"t": <tag>, "v": <value>}`, keyed by
  `CODEC_TYPE_KEY` and `CODEC_VALUE_KEY`. The tag is a `CODEC_*_TAG` constant: `"str"`, `"bytes"` (base64),
  `"int"`, `"float"`, `"bool"`, `"null"`, `"list"`, or `"dict"` with string keys. The tags make the round trip
  exact, with no type lost. An `IO` value raises `TypeError`.

### `AbstractCacheStore`

- `read(self, identity: str, epoch: CacheEpoch) -> CacheRecord | CacheMiss`.
- `commit_if_current(self, record: CacheRecord, *, current: CacheEpoch) -> CommitState`.
  - It publishes only if `record.epoch.generation == current.generation`, and returns `RETIRED` otherwise.
    The caller acquires `current` inside the generation fence, which is what makes the condition hold at
    publication.
  - Publication is atomic: `atomic_file.rewrite_file(path, contents, mode=CACHE_FILE_MODE)` writes a
    temporary file in the generation's directory, then calls `os.replace`.
  - It returns `FULL` at `CACHE_MAX_RECORDS` records in one generation.
- `delete_epoch(self, epoch: CacheEpoch) -> int`: removes the generation's records and returns their number.
- Adapters:
  - `MemoryCacheStore()`: a dict keyed by `(generation, identity)`, guarded by one `threading.Lock`;
  - `FileCacheStore(root: Path)`: one directory per generation and one file per record, as listed under
    [Disk layout](#disk-layout).

### `AbstractCacheKeyStore`

- `create(self, epoch: CacheEpoch, keys: EpochKeys) -> AcquiredKey`. Called inside the generation fence. It
  stores the keys as the current epoch, unless a valid epoch is already current. In that case it returns that
  epoch's key and wipes `keys`.
- `acquire(self, is_valid: Callable[[CacheEpoch], bool]) -> AcquiredKey | KeyRejection`. Rejects an absent
  key, a revoked key, and a key whose epoch `is_valid` rejects. The caller passes `lifetime.is_valid`, so the
  key store owns no clock. At most one key is active; a second active key file found on disk is revoked.
- `revoke(self, key_id: str) -> None`: marks the key revoked; `acquire` rejects it from then on.
- `destroy(self, key_id: str) -> None`: wipes the key from memory, and, for the file store, overwrites, syncs,
  and unlinks its file.
- Adapters:
  - `MemoryCacheKeyStore()`: the keys live only in this process;
  - `FileCacheKeyStore(root: Path)`: one file per key, used only if key-placement option (a) is approved.

### `AbstractCacheLifetime`

- `new_epoch(self, key_id: str) -> CacheEpoch`.
- `is_valid(self, epoch: CacheEpoch) -> bool`: `created_at <= now < expires_at`.
- Adapter `FixedPeriodLifetime(ttl: timedelta, clock: Callable[[], datetime])`. `ttl` must lie between
  `CACHE_MIN_PERIOD_SECONDS` and `CACHE_PERIOD_SECONDS`, or the constructor raises `ValueError`.
  Configuration may shorten the period and never lengthen it.

### `AbstractCacheCoordinator`

- `hold(self, identity: str, deadline: Deadline) -> AbstractContextManager[HoldState]`. It waits until it holds
  `identity` or `deadline` passes. On leaving the context, the hold is released, even after an exception.
- The generation fence is `hold(CACHE_FENCE_IDENTITY, deadline)`.
- Adapters:
  - `ThreadCacheCoordinator()`: one `threading.Lock` per identity, in this process only;
  - `FileCacheCoordinator(root: Path)`: one lock file per identity with `fcntl.flock(LOCK_EX | LOCK_NB)`,
    retried every `CACHE_HOLD_POLL_SECONDS` until the deadline. It works across processes and threads. After
    locking, it compares `os.fstat(fd).st_ino` with `os.stat(path).st_ino` and retries when they differ. On
    release it unlinks the file while still holding the lock, then unlocks. So lock files never accumulate,
    and a waiter that locked an unlinked file retries on the new one.

## Profiles

A profile is a declared, supported combination. The contract suite runs on every one of them.

| Profile | Store | Key store | Coordinator | Shared across processes | Platforms |
| --- | --- | --- | --- | --- | --- |
| memory | `MemoryCacheStore` | `MemoryCacheKeyStore` | `ThreadCacheCoordinator` | no | all |
| disk | `FileCacheStore` | `FileCacheKeyStore` | `FileCacheCoordinator` | yes | Linux, macOS |

- Each profile runs with either cipher and in either execution mode (`SecretCache` or `AsyncSecretCache`), with
  `JsonGemCodec` and `FixedPeriodLifetime`. That makes eight combinations.
- Builders in `src/hiddengems/secret_cache_profiles.py`, outside the orchestration:
  - `memory_secret_cache(*, cipher: AbstractCacheCipher | None = None, ttl_seconds: int =
    CACHE_PERIOD_SECONDS) -> SecretCache`;
  - `disk_secret_cache(root: Path | None = None, *, cipher: AbstractCacheCipher | None = None, ttl_seconds: int =
    CACHE_PERIOD_SECONDS) -> SecretCache`;
  - the async variants `memory_async_secret_cache` and `disk_async_secret_cache`, with the same parameters.

  `None` means "not given": `AesGcmCipher()`, and `Path.home() / CACHE_DIR_NAME`. A basic user writes
  `HiddenGems(cache=memory_secret_cache())` and nothing else.
- On Windows, `disk_secret_cache` raises `ValueError` with `CACHE_DISK_UNSUPPORTED`, because the ownership and
  mode checks below are POSIX.

## Disk layout

```text
~/.hiddengem/                          0700   root = Path.home() / CACHE_DIR_NAME
  entries/                             0700   FileCacheStore(root / "entries")
    <generation>/                      0700   one directory per epoch
      <identity>.record                0600   one CacheRecord as JSON
  keys/                                0700   FileCacheKeyStore(root / "keys"), only under option (a)
    <key_id>.key                       0600   epoch, state (active or revoked), seal and index keys, base64
  locks/                               0700   FileCacheCoordinator(root / "locks")
    <identity>.lock                    0600   same-identity hold, unlinked on release
    fence.lock                         0600   generation fence, unlinked on release
```

- **Ownership and modes,** checked the way ssh's StrictModes checks `~/.ssh`. Before every read or write, each
  directory must be owned by `os.getuid()` with mode `0700`, and each file with mode `0600`. Anything else
  makes that call bypass the cache, with a `SecretCacheWarning` naming the path and the check. The cache never
  fixes modes it did not create.
- **Created by the cache** with `os.makedirs(..., mode=CACHE_DIR_MODE)` and `os.chmod`, so the umask cannot
  widen them. Files are created with `CACHE_FILE_MODE`. Record and key files are written with
  `atomic_file.rewrite_file(path, contents, mode=CACHE_FILE_MODE)` from `GAL-chooser`.
- **Partial writes:** a record or key file that does not parse, or lacks a field, is a `CacheMiss(MALFORMED)`
  or a `KeyRejection`. It is never a hit.
- **Lock files** exist only while held, by the unlink rule of `FileCacheCoordinator`.
- **Separate from remembered detection:** nothing here is written to `~/.gem_provider.json`.

## `HiddenGems` changes

- **`__init__(..., cache: AbstractSecretCache = PASS_THROUGH_CACHE)`,** a keyword-only parameter after
  `refresh` from `GAL-remember`. It stores `cache` and computes `self._routing_revision` as defined under
  `CacheRequest`. It computes it again whenever `HiddenGems.preferences` changes, as it does when `dig_gem` sets
  the saved choice after `save_preference` in step 5, and after `HiddenGems.invalidate` detects again.
- **`dig_gem`** gains the keyword-only parameter `refresh: bool = False`, after `masked` from
  `GAL-masked-value`. The final signature is `dig_gem(self, name, *, provider=None, target=None, criteria=None,
  choice=None, remember=False, masked=False, refresh=False)`. Its body becomes:
  1. `request = CacheRequest(name, selection, self._routing_revision)`;
  2. `deadline = Deadline.after(LOOKUP_TIMEOUT_SECONDS)`;
  3. `loader(deadline)` calls `self._resolve_within(name, provider=provider, target=target, criteria=criteria,
     choice=choice, deadline=deadline)` from `GAL-parallel`, then reads the reference by `deadline_capability` as
     `GAL-parallel`'s `dig_gem` does, all with the loader's `deadline`, giving `ResolvedGem(reference, values)`;
  4. `values = self._cache.get_or_load(request, loader, deadline=deadline, refresh=refresh)`. `masked` is
     applied to the values `get_or_load` returns;
  5. with `remember=True` and a `choice`, `dig_gem` saves that choice through `save_preference` of `GAL-chooser`
     after `get_or_load` returns, on a hit as on a miss. Neither `remember` nor `masked` is part of the request;
  6. the result is returned.

  The cache wait, the resolution, and the read share one deadline.

  This `refresh` re-reads one value. The `refresh` of `HiddenGems.__init__` from `GAL-remember` re-runs
  detection. They are separate parameters of separate methods.

  A `TimeoutError` from step 1 or step 3 of `get_or_load` becomes `IncompleteGemLookupError` with one `LookupIssue`:
  `provider=CACHE_ISSUE_SOURCE`, `instance_id=CACHE_ISSUE_SOURCE`, `reason=CACHE_WAIT_TIMEOUT_REASON`, and
  `next_action=TIMEOUT_NEXT_ACTION`. The set of errors `dig_gem` raises stays the same.
- **`hide_gem`:** after a successful write without `dry_run`, it calls `self._cache.invalidate()` and lets a
  `TimeoutError` from it propagate after the write.
- **`HiddenGems.invalidate`** (`GAL-remember`) gains a first step, `self._cache.invalidate()`, whose
  `TimeoutError` it collects with its other step errors, and recomputes `_routing_revision` after detecting again.
- `HiddenGems` never closes a cache it was given. The caller that built it owns `close()`.

## Abstraction-extension gate

### 1. Extensions

Every new name is in `src/hiddengems/secret_cache.py`, except the profile builders, in
`secret_cache_profiles.py`, and the constants, in `constants/cache.py`.

| Extension | Status | Implementation owner | Actual caller | Tests |
| --- | --- | --- | --- | --- |
| records and enums above | MUST ADD | `secret_cache.py` | `SecretCache`, `HiddenGems.dig_gem` | S2, S3, S5, S8 |
| `AbstractSecretCache` | MUST ADD | `SecretCache`, `PassThroughSecretCache` | `HiddenGems.dig_gem` | S1-S10, S12-S16 |
| `AbstractAsyncSecretCache` | MUST ADD | `AsyncSecretCache` | the library's caller | S11; async S1, S6-S10, S12, S13 |
| `AbstractCacheCipher` | MUST ADD | `AesGcmCipher`, `ChaCha20Poly1305Cipher` | `SecretCache` | S1, S8, S13 |
| `AbstractGemCodec` | MUST ADD | `JsonGemCodec` | `SecretCache` | S1, S5 |
| `AbstractCacheStore` | MUST ADD | `MemoryCacheStore`, `FileCacheStore` | `SecretCache` | S1, S7, S12 |
| `AbstractCacheKeyStore` | MUST ADD | `MemoryCacheKeyStore`, `FileCacheKeyStore` | `SecretCache` | S1, S6, S13 |
| `AbstractCacheLifetime` | MUST ADD | `FixedPeriodLifetime` | `SecretCache` | S1, S6 |
| `AbstractCacheCoordinator` | MUST ADD | `ThreadCacheCoordinator`, `FileCacheCoordinator` | `SecretCache` | S1,S9,S10 |
| profile builders | MUST ADD | `secret_cache_profiles.py` | the library's caller | S12, S16, S21 |
| `CacheIntegrityError` | MUST ADD | `secret_cache.py` | `SecretCache.get_or_load` step 4 | S8 |
| `SecretCacheWarning` | MUST ADD | `secret_cache.py` | `FileCacheStore`, `FileCacheKeyStore` | S16 |
| `HiddenGems(cache=...)`, `dig_gem` | behavior change | `hidden_gems.py` | the library's caller | S2-S4, S14, S22 |
| `hide_gem`, `invalidate` steps | behavior change | `hidden_gems.py` | the library's caller | S15, S20 |

In the `AbstractAsyncSecretCache` row, "async S1, S6-S10, S12, S13" means that those cases run on the four
`AsyncSecretCache` combinations.

The signature and behavior of each are in the sections above.

### 2. What each extension extends

None extends `AbstractGemProvider`, and none replaces it. The eight new abstract classes describe a cache, not a
provider, so they are a separate contract and not a parallel provider contract. `B`, the factory, and the
providers are unchanged.

### 3. Providers in P

- **`OnePasswordProvider`.** Applicable contract `C`: `AbstractGemProvider`, `DeadlineAwareGemProvider`. How it
  satisfies it: unchanged by this feature; reached only through `_resolve_within` and the read inside the loader.
- **`DotEnvProvider`.** Applicable contract `C`: `AbstractGemProvider`, `WritableGemProvider`,
  `DeadlineAwareGemProvider`; under `GAL-plugin` option one, `AbstractGemProvider`, `DeadlineAwareGemProvider`.
  How it satisfies it: unchanged by this feature; reached only through `_resolve_within` and the read inside
  the loader.
- **`KeyringProvider`.** Applicable contract `C`: `AbstractGemProvider`, and `DeadlineAwareGemProvider` under
  Keychain option (a). How it satisfies it: unchanged by this feature; reached only through `_resolve_within` and
  the read inside the loader.
- **`KubernetesProvider`.** Applicable contract `C`: `AbstractGemProvider`, `DeadlineAwareGemProvider`. How it
  satisfies it: unchanged by this feature; reached only through `_resolve_within` and the read inside the loader.

`C` is `B` with the approved Δ of `GAL-plugin`, `GAL-expand`, `GAL-parallel`, and `GAL-remember`; this feature
adds nothing to it, and none of the four is edited. Under `GAL-plugin` option one, `WritableGemProvider` does not
exist, and no provider subclasses it.

### 4. Baseline tests whose expectations change

None. The default `PASS_THROUGH_CACHE` leaves every read going to the provider, and case S14 checks that.

### 5. Baseline

The baseline stays commit `4438234`. This proposal does not redefine `B`.

## Exceptions

Two new classes, each needed because a caller must tell it apart from every existing class. This feature's
pull request adds both to `EXPECTED_EXCEPTIONS` in `test_exception_classes_match_the_register` of
`GAL-plugin`, as `("hiddengems.secret_cache", "CacheIntegrityError", ("ValueError",))` and
`("hiddengems.secret_cache", "SecretCacheWarning", ("UserWarning",))`:

- **`CacheIntegrityError(ValueError)`,** in `secret_cache.py`.
  - Raised by `AbstractCacheCipher.open` and `AbstractGemCodec.decode`.
  - Closest existing classes: `cryptography.exceptions.InvalidTag` belongs to one library, and a third-party
    cipher would raise something else; `ValueError` is too broad to catch safely around decoding, where it
    would also hide programming errors.
  - `SecretCache` catches it and treats the record as a miss. It never reaches a `HiddenGems` caller. Existing
    `except ValueError` handlers still catch it.
  - S8 also asserts that `pytest.raises(ValueError)` catches the `CacheIntegrityError` that
    `AesGcmCipher.open` raises for an altered payload.
- **`SecretCacheWarning(UserWarning)`,** in `secret_cache.py`.
  - `__init__(self, path: Path, check: str) -> None`. The message is
    `SECRET_CACHE_WARNING.format(path=path, check=check)`.
  - Emitted when a profile cannot be used safely for one call: wrong ownership or mode, an unreadable key
    file, or a failed write. The call then bypasses the cache.
  - Closest existing class: `RememberedStoreWarning` of `GAL-remember` reports the detection store, which this
    feature must stay separate from. A caller filters value-cache problems by this category without matching
    text.
  - Base `UserWarning`, so an existing `UserWarning` filter or handler still catches it; S16 asserts that
    `pytest.warns(UserWarning)` catches it.

Reused without a new class:

- `TimeoutError`: from `get_or_load` it becomes `IncompleteGemLookupError` at `dig_gem`; from `invalidate`,
  `HiddenGems.invalidate` collects it and `hide_gem` lets it propagate after the write; from `expire` and
  `close`, it reaches their caller;
- `TypeError` from `encode` means "do not store";
- `ValueError` for a TTL out of range, or the disk profile on Windows;
- `ModuleNotFoundError` when the `cache` extra is missing;
- `asyncio.CancelledError` propagates unchanged.

Messages and attributes never contain a gem value or key material. Gem names appear only inside the encrypted
payload and the authenticated metadata.

## Constants

`src/hiddengems/constants/cache.py`:

- `CACHE_DIR_NAME: Final[str] = ".hiddengem"`, `CACHE_DIR_MODE: Final[int] = 0o700`, and
  `CACHE_FILE_MODE: Final[int] = 0o600`, as the overview already lists;
- `CACHE_ENTRIES_DIR_NAME: Final[str] = "entries"`, `CACHE_KEYS_DIR_NAME: Final[str] = "keys"`, and
  `CACHE_LOCKS_DIR_NAME: Final[str] = "locks"`;
- `CACHE_RECORD_SUFFIX: Final[str] = ".record"`, `CACHE_KEY_SUFFIX: Final[str] = ".key"`,
  and `CACHE_LOCK_SUFFIX: Final[str] = ".lock"`;
- `CACHE_FENCE_IDENTITY: Final[str] = "fence"`;
- `CACHE_PERIOD_SECONDS: Final[int] = 86_400` and `CACHE_MIN_PERIOD_SECONDS: Final[int] = 60`;
- `CACHE_ID_BYTES: Final[int] = 16`, `CACHE_INDEX_KEY_BYTES: Final[int] = 32`, and `CACHE_KEY_BYTES: Final[int] =
  32`;
- `CACHE_KEY_BITS: Final[int] = CACHE_KEY_BYTES * BITS_PER_BYTE` and `BITS_PER_BYTE: Final[int] = 8`. The AES
  adapter calls `generate_key(bit_length=CACHE_KEY_BITS)`;
- `AES_GCM_NONCE_BYTES: Final[int] = 12` and `CHACHA20_POLY1305_NONCE_BYTES: Final[int] = 12`, used only by
  their adapters, because the nonce length belongs to the adapter;
- `AEAD_TAG_BYTES: Final[int] = 16`;
- `NO_EPOCH_ID: Final[str] = ""` and `EPOCH_ORIGIN: Final[datetime] = datetime.min.replace(tzinfo=timezone.utc)`.
  `secret_cache.py` builds `NO_EPOCH` from them, as listed under [Records](#records);
- `CODEC_TYPE_KEY: Final[str] = "t"` and `CODEC_VALUE_KEY: Final[str] = "v"`;
- `CODEC_STR_TAG`, `CODEC_BYTES_TAG`, `CODEC_INT_TAG`, `CODEC_FLOAT_TAG`, `CODEC_BOOL_TAG`, `CODEC_NULL_TAG`,
  `CODEC_LIST_TAG`, and `CODEC_DICT_TAG`, each `Final[str]`, with the values `"str"`, `"bytes"`, `"int"`,
  `"float"`, `"bool"`, `"null"`, `"list"`, and `"dict"`;
- `SELECTION_PROVIDER_KEY: Final[str] = "provider"`, `SELECTION_TARGET_KEY: Final[str] = "target"`,
  `SELECTION_CRITERIA_KEY: Final[str] = "criteria"`, `SELECTION_PREFERENCE_KEY: Final[str] = "preference"`, and
  `SELECTION_CHOICE_KEY: Final[str] = "choice"`, the keys of `CacheRequest.selection`;
- `CACHE_RECORD_FORMAT: Final[int] = 1` and `CACHE_KEY_FORMAT: Final[int] = 1`;
- `CACHE_MAX_RECORDS: Final[int] = 4096` and `CACHE_MAX_VALUE_BYTES: Final[int] = 1_048_576`;
- `CACHE_HOLD_POLL_SECONDS: Final[float] = 0.01`;
- `CACHE_ISSUE_SOURCE: Final[str] = "secret-cache"`;
- `CACHE_WAIT_TIMEOUT_REASON: Final[str] = "Waited for another read of the same gem until the time limit"`;
- `CACHE_DISK_UNSUPPORTED: Final[str] = "The disk cache needs POSIX ownership and modes"`;
- `SECRET_CACHE_WARNING: Final[str] = "Secret cache bypassed: {path} failed the {check} check"`;
- `CACHE_EXTRA_MISSING: Final[str] = "Install hiddengems[cache] to use the secret cache"`.

## Layout

```text
~ pyproject.toml                                 extra cache = ["cryptography>=50.0"]
+ src/hiddengems/secret_cache.py                 records, enums, abstract classes, SecretCache, AsyncSecretCache,
                                                 PassThroughSecretCache, adapters, CacheIntegrityError,
                                                 SecretCacheWarning
+ src/hiddengems/secret_cache_profiles.py        the four profile builders
+ src/hiddengems/constants/cache.py              the constants above
~ src/hiddengems/hidden_gems.py                  cache=, dig_gem, hide_gem, invalidate step
+ tests/contract/test_secret_cache_contract.py   the blocking suite
```

## Alternatives for the owner's decision

- **Key placement,** the point the overview left open:
  - (a) `FileCacheKeyStore(root / "keys")`, beside the records, protected by file modes as `~/.ssh` is.
    Recommended: a cache shared across processes needs a shared key, and destroying the key makes every record
    unreadable at once, including copies in a backup.
  - (b) `MemoryCacheKeyStore` only. Keys never touch the disk, so the disk profile is not offered; only the
    memory profile exists, and every process prompts once.
  - (c) a key held in an OS keystore item, such as the Keychain. A separate feature: reading that item can
    itself prompt.
- **`AbstractAsyncSecretCache`:**
  - (a) deliver it now as public API, with `AsyncSecretCache` and its profiles. Its caller is the library's
    caller, because `HiddenGems` has no async API. Recommended: the blocking suite includes
    `test_async_contract_does_not_block_loop`. This is an owner-mandated exception to the no-consumer rule of
    `software-design.md`: the owner's blocking suite includes `test_async_contract_does_not_block_loop`.
  - (b) declare it and lock its implementation until an async `HiddenGems` API exists to call it.
- **A timed-out wait for the same identity or for the generation fence in `get_or_load`:**
  - (a) `IncompleteGemLookupError` at `dig_gem`, as above. Recommended: callers already handle it, and the set
    of errors `dig_gem` raises does not grow.
  - (b) let `TimeoutError` reach the caller.
- **A refresh that fails:**
  - (a) leave the existing record in place. Recommended: the store contract stays at the owner's three
    operations, and the record was valid for the caller's period.
  - (b) add `discard(identity, epoch)` to `AbstractCacheStore`, and drop the record before loading.
- **A write through `hide_gem`:**
  - (a) invalidate the whole cache. Recommended: writes are rare, and the contract needs no operation to find
    records by name, which the HMAC identity hides on purpose.
  - (b) add an operation that drops one name's records.
- **What one authentication unlocks:**
  - (a) one request identity: one provider read fills one record. Recommended: it is what `get_or_load` does.
  - (b) one provider.
  - (c) every cached gem.

## Acceptance cases

The blocking gate is `tests/contract/test_secret_cache_contract.py`, in the contract-test location of `GAL-plugin`. S1,
S6 to S10, S12, and S13 are parametrized over the eight profile combinations; S2 to S5 over the four `SecretCache`
combinations, because `HiddenGems` takes an `AbstractSecretCache` and has no async API; and S11 over the four
`AsyncSecretCache` combinations; a test, or part of one, that claims process sharing runs only on the disk profile. S14
uses no cache. S15, S17 to S20, and S22 run on the memory profile with each cipher. S16 runs on the disk profile. S21
calls the builders directly. Every test uses a controlled lifetime clock, a `tmp_path` root, and fake providers with
fixture values, never a real secret or the real home directory.

The owner's twelve cases:

- **S1** `test_cache_dependencies_are_substitutable`: alternate conforming implementations of all six
  dependencies, each delegating to an adapter and recording its calls, pass the behavior tests unchanged,
  without editing `HiddenGems` or any provider. A subclass missing one abstract operation cannot be built.
- **S2** `test_hits_bypass_resolution_and_value_read`: with one successful fake provider and an unchanged
  request, 200 `dig_gem` calls produce one `find_gem` and one `get_gem`.
- **S3** `test_request_identity_isolated`: the same gem name in different instances, locations, or selection
  contexts never shares a result. Changing the effective preference changes the request and misses.
- **S4** `test_unresolved_lookup_is_not_cached_as_success`: ambiguous, incomplete, and stale-preference
  results raise their existing errors every time and leave no record.
- **S5** `test_codec_preserves_gem_contract`: strings, bytes, and nested lists and dicts round-trip without
  type loss. Mutating one returned list does not change a later result. An `IO` value is returned and not
  stored.
- **S6** `test_value_and_key_expire_together`: a hit just before `d_e`; at `d_e`, a miss. Hits do not move
  `d_e`. The old key cannot be acquired, and the next load uses a new generation and a new key.
- **S7** `test_invalidation_fences_inflight_load`: pause a loader, call `invalidate`, then release it. Its
  result is not published, and the next request misses.
- **S8** `test_ciphertext_and_metadata_authenticated`: altering the payload, the identity, the reference, or
  `expires_at` in the metadata yields no plaintext, only a miss. Persisted records and every warning and error
  text contain neither the fixture value nor key material. `pytest.raises(ValueError)` catches the
  `CacheIntegrityError` that `AesGcmCipher.open` raises for an altered payload.
- **S9** `test_concurrent_miss_has_one_loader`: 200 concurrent requests for one identity call the loader once.
  Requests for two identities overlap in time, measured by the loader's start and end stamps. On the disk
  profile, the same holds for two processes.
- **S10** `test_timeout_and_cancellation_release_ownership`: a wait and a load both stop at the deadline. A
  failed or cancelled owner releases the hold, a waiter then loads, and late work publishes nothing.
- **S11** `test_async_contract_does_not_block_loop`: an event-loop heartbeat keeps ticking during a slow loader
  and slow storage. Cancelling one waiter leaves the others and the owner running. `close()` leaves no task
  or thread behind.
- **S12** `test_disk_profile_reopens_safely`: a new `SecretCache` on the same root hits before `d_e`, and
  misses after `d_e`. A truncated record or key file is a miss. Paths and modes match
  [Disk layout](#disk-layout), and a destroyed key's file is gone.

Added by this proposal:

- **S13** `test_new_epoch_never_reuses_a_key_or_nonce`: across ten epochs, every `key_id`, seal key, and index
  key differs. Within one epoch, 10,000 seals give 10,000 different nonces. The seal and index keys of one
  epoch differ.
- **S14** `test_pass_through_cache_keeps_baseline_behavior`: without `cache=`, every `dig_gem` calls
  `find_gem` and `get_gem`, as the baseline routing tests expect.
- **S15** `test_hide_gem_invalidates_the_cache`: the writer is a test class
  `DeadlineAwareWritableEnvironmentProvider(WritableEnvironmentProvider, DeadlineAwareGemProvider)` defined in
  the test, over `WritableEnvironmentProvider` from `tests/contract/example_providers.py` (`GAL-plugin`); under
  `GAL-plugin` option one, where `WritableEnvironmentProvider` does not exist, its bases are `EnvironmentProvider`,
  whose `put_gem` the kit then defines, and `DeadlineAwareGemProvider`. Its
  `find_gem_within` and `get_gem_within` delegate to `find_gem` and `get_gem`, so its read is `SUPPORTED`
  under `GAL-parallel`. It is registered by replacing `GemProvider.provider_types`. After a write, the next
  read goes to the provider.
- **S16** `test_disk_profile_rejects_open_permissions`: a root with mode `0755`, or a record with mode `0644`,
  gives `SecretCacheWarning`, which `pytest.warns(UserWarning)` also catches, and the read goes to the provider.
- **S17** `test_refresh_reloads_and_replaces_the_record`: after a hit, `refresh=True` calls the provider once,
  and the next call without it returns the new value. A refresh whose loader fails raises that error, and
  the next call without refresh still hits the old record.
- **S18** `test_new_duplicate_does_not_end_the_choice`: the timeline under
  [Choice within a period](#choice-within-a-period), with two fake providers `p₁` and `p₂`. At `t₃` there is
  no `find_gem` call and the value is `p₁`'s. At `t₄` the result is `AmbiguousGemError` naming both.
- **S19** `test_explicit_choice_is_kept_for_the_period`: `dig_gem("x", provider=p₁)` caches; the identical
  request hits; `dig_gem("x")` without `provider` is a different request and resolves on its own.
- **S20** `test_hidden_gems_invalidate_empties_the_cache`: with the S15 writer class, after a hit,
  `HiddenGems.invalidate()` makes the next identical `dig_gem` call `find_gem` and `get_gem` once.
- **S21** `test_profile_builders_return_the_declared_combinations`: each builder, with each cipher, returns its
  profile's store, key store, and coordinator; `disk_secret_cache` on a patched Windows platform raises `ValueError`
  with `CACHE_DISK_UNSUPPORTED`; a `ttl_seconds` outside `CACHE_MIN_PERIOD_SECONDS`..`CACHE_PERIOD_SECONDS` raises
  `ValueError`.
- **S22** `test_remember_saves_the_choice_on_a_hit_and_a_miss`: with two candidates for `x` and a cache,
  `dig_gem("x", choice=key, remember=True)` reads the provider once and calls `save_preference` with the
  preference for `key`. That save changes `HiddenGems.preferences`, so the identical second call is a new
  request: it misses, reads the provider once, and calls `save_preference` again. The identical third call is a
  hit, makes no provider call, and calls `save_preference` again. Without `remember`, `save_preference` is not
  called.

## Dependencies on other features

- **Delivery:** plan 6.5, after `GAL-masked-value` (6.4).
- **Blocked by:**
  - `GAL-parallel`, for `Deadline` with its injected clock, `_resolve_within`, `deadline_capability`, and
    `get_gem_within`;
  - `GAL-remember`, for `HiddenGems.invalidate`;
  - `GAL-chooser`, for `canonical_json.py`, `atomic_file.py`, `choice`, and `save_preference`;
  - `GAL-masked-value`, for `masked`, the last `dig_gem` parameter before `refresh`;
  - `GAL-plugin`, for the `tests/contract/` location;
  - `GAL-settings`, for `LOOKUP_TIMEOUT_SECONDS`.
- **Required by:** `GAL-encrypted-store`, which reuses `AbstractCacheCipher` and `AbstractCacheKeyStore`.
- **Relates to:**
  - the proposed Keychain existence check, which removes the second Keychain read even without a cache.
