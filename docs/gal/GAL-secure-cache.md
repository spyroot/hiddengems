# GAL-secure-cache: encrypted value cache in front of provider reads

Status: proposal, revision 1. Not approved for implementation. The owner decides the choices in
[Alternatives](#alternatives-for-the-owners-decision). The overview of all features is
[provider-routing-design.md](../provider-routing-design.md). This proposal is written to its
[Abstraction-extension gate](../provider-routing-design.md#abstraction-extension-gate) and its exception rules.
The cache surface below is the owner's; this document gives every part its exact signature, behavior, owner,
caller, and acceptance test.

## Purpose and observable capability

A gem read 200 times in one period costs one provider lookup and one value read, so it prompts for the Keychain
password or a 1Password unlock once, not 200 times.

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
    reads of one item can prompt 400 times. This is `GAL-case-repeated-reads`;
  - nothing caches a value. "No caching of gem values" is a non-goal, and this feature relaxes it only when the
    caller passes a cache.

## Flow

```text
HiddenGems.dig_gem
    └── AbstractSecretCache.get_or_load
          ├── valid hit → open and decode the cached result
          └── miss → existing resolve_gem + existing provider read
                         │
                         └── commit the successful result, if its generation is still current
```

`inspect_gem` and `resolve_gem` never use the cache. A failed lookup is never cached: `AmbiguousGemError`,
`IncompleteGemLookupError`, `StaleGemPreferenceError`, and `GemNotFoundError` reach the caller as today.

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
| AES-256-GCM | `AESGCM`: `generate_key(bit_length=256)`, `encrypt`, `decrypt` |
| ChaCha20-Poly1305 | `ChaCha20Poly1305`: `generate_key`, `encrypt`, `decrypt` |
| Tamper detection | `cryptography.exceptions.InvalidTag`, raised by `decrypt` on altered data or metadata |
| Nonces and ids | `secrets.token_bytes`, `secrets.token_hex` |
| Request identity | `hmac.new(key, message, hashlib.sha256)` |
| File locks | `fcntl.flock`, through `atomic_file.exclusive_write_lock` from `GAL-remember` |
| Async | `asyncio.Lock`, `asyncio.shield`, `asyncio.timeout`, `asyncio.to_thread` |

- **Checked:** in the `hiddengems` environment, `cryptography` 50.0.2 provides both AEAD classes with
  `generate_key`, `encrypt`, and `decrypt`. The tag adds 16 bytes, a ChaCha20-Poly1305 key is 32 bytes, and
  wrong associated data raises `InvalidTag`.
- **Packaging:** `cryptography>=50.0` in a new extra `cache`, imported lazily inside the two adapters. Without
  the extra, building either adapter raises `ModuleNotFoundError` naming `hiddengems[cache]`.

## Records

In `src/hiddengems/secret_cache.py`, all frozen dataclasses:

- **`CacheRequest(name: str, selection: str, routing_revision: str)`.**
  - `selection` is the canonical JSON of `{"provider": provider, "criteria": criteria, "preference":
    HiddenGems.preferences.get(name)}`, with sorted keys and `default=str`.
  - `routing_revision` is the SHA-256 hex digest of the canonical JSON of the detected records'
    `(provider, instance_id, state, settings)` and of `HiddenGems.preferences`.
  - `canonical(self) -> bytes` returns the canonical JSON of the three fields.
- **`ResolvedGem(reference: GemReference, values: list[Gem])`.**
- **`CacheEpoch(generation: str, created_at: datetime, expires_at: datetime, key_id: str)`.**
  - `generation` and `key_id` come from `secrets.token_hex(CACHE_ID_BYTES)`.
  - `created_at` and `expires_at` are UTC, with `expires_at = created_at + TTL`, fixed when the epoch is made.
- **`EpochKeys(seal: bytearray, index: bytearray)`,** two independent random keys. `seal` comes from the
  cipher's `generate_key`, and `index` from `secrets.token_bytes(CACHE_INDEX_KEY_BYTES)`. Not frozen, so
  `wipe()` can zero both in place.
- **`AcquiredKey(epoch: CacheEpoch, keys: EpochKeys)`** and **`KeyRejection(state: KeyState, epoch: CacheEpoch,
  reason: str)`**. When no key exists, `epoch` is `NO_EPOCH`, a module constant whose generation is the empty
  string.
- **`CacheRecord(identity: str, reference: GemReference, epoch: CacheEpoch, codec: CodecId, cipher: CipherId,
  metadata: bytes, payload: bytes)`.**
  - `identity` is `hmac.new(keys.index, request.canonical(), hashlib.sha256).hexdigest()`. A file name never
    shows a gem name.
  - `metadata` is the canonical JSON of `CACHE_RECORD_FORMAT`, `identity`, `reference`, `epoch`, `codec`, and
    `cipher`. It is passed to the cipher as associated data, so it is authenticated.
  - `payload` is the adapter's output: its nonce followed by the ciphertext and tag.
- **`CacheMiss(reason: MissReason)`**, the result of a read that found no usable record.
- **`Deadline`**, from `GAL-parallel`, with the injected clock added there:
  `Deadline(expires_at: float, clock: Callable[[], float] = time.monotonic)`.

Enums, each a `StrEnum`:

- `CipherId`: `AES_256_GCM = "aes-256-gcm"` and `CHACHA20_POLY1305 = "chacha20-poly1305"`;
- `CodecId`: `JSON_V1 = "json-v1"`;
- `KeyState`: `ABSENT = "absent"`, `EXPIRED = "expired"`, and `REVOKED = "revoked"`;
- `MissReason`: `ABSENT = "absent"`, `MALFORMED = "malformed"`, and `OTHER_EPOCH = "other_epoch"`;
- `CommitState`: `COMMITTED = "committed"`, `RETIRED = "retired"`, and `FULL = "full"`;
- `HoldState`: `HELD = "held"` and `EXPIRED = "expired"`.

Type aliases: `GemLoader = Callable[[Deadline], ResolvedGem]` and
`AsyncGemLoader = Callable[[Deadline], Awaitable[ResolvedGem]]`.

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
| `get_or_load` | `(self, request: CacheRequest, loader: GemLoader, *, deadline: Deadline) -> list[Gem]` | below |
| `invalidate` | `(self) -> None` | retires the current epoch: revoke, destroy, delete |
| `expire` | `(self) -> None` | runs the retirement order for every epoch past `d_e`; others untouched |
| `close` | `(self) -> None` | releases holds and wipes in-memory keys; persisted epochs stay usable by others |

**`SecretCache.get_or_load`**, the concrete orchestration:

1. `key_store.acquire(lifetime.is_valid)`. On a `KeyRejection`, enter the generation fence and acquire again,
   because another process may have made a new epoch meanwhile. If it is still rejected:
   - finish the retirement order for the rejected epoch, when it has one. `revoke`, `destroy`, and
     `delete_epoch` are idempotent, so repeating steps another process already ran is harmless;
   - make a new epoch: `lifetime.new_epoch(key_id)`, `cipher.generate_key()` for `seal`,
     `secrets.token_bytes` for `index`, then `key_store.create(epoch, keys)`.
2. Compute `identity` with the index key.
3. Enter `coordinator.hold(identity, deadline)`. On `HoldState.EXPIRED`, raise
   `TimeoutError` with `CACHE_WAIT_TIMEOUT_REASON`. Waits for the same identity are serialized, and
   different identities proceed at the same time.
4. `store.read(identity, epoch)`. A `CacheRecord` that passes `cipher.open` and `codec.decode` is a hit: return
   the decoded values. Each call decodes again, so the caller gets new objects. A `CacheMiss`, a
   `CacheIntegrityError`, or an expired epoch is a miss.
5. On a miss, call `loader(deadline)`. Its exceptions propagate unchanged, and nothing is stored.
6. Encode the values. A value the codec does not support, such as an `IO` stream, raises `TypeError`; the
   values are then returned without storing. A payload larger than `CACHE_MAX_VALUE_BYTES` is not stored
   either.
7. Seal the payload with the metadata as associated data. Enter the generation fence, call
   `key_store.acquire(lifetime.is_valid)` again, and commit with `store.commit_if_current(record,
   current=<that epoch>)`. A rejection, `RETIRED`, or `FULL` publishes nothing. Because `invalidate` and
   `expire` revoke inside the same fence, a commit can never land in a retired generation.
8. Return the loaded values.

The orchestration contains no cipher name, no file path, and no provider name. It sees only the six injected
dependencies.

### `AbstractAsyncSecretCache`

The same four operations as `async def`, with `loader: AsyncGemLoader`. `AsyncSecretCache` behaves as above,
and also:

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
- Adapter `JsonGemCodec`. Each value becomes a typed node: `{"t": "str"}`, `"bytes"` (base64), `"int"`,
  `"float"`, `"bool"`, `"null"`, `"list"`, or `"dict"` with string keys. The tags make the round trip exact,
  with no type lost. An `IO` value raises `TypeError`.

### `AbstractCacheStore`

- `read(self, identity: str, epoch: CacheEpoch) -> CacheRecord | CacheMiss`.
- `commit_if_current(self, record: CacheRecord, *, current: CacheEpoch) -> CommitState`.
  - It publishes only if `record.epoch.generation == current.generation`, and returns `RETIRED` otherwise.
    The caller acquires `current` inside the generation fence, which is what makes the condition hold at
    publication.
  - Publication is atomic: a temporary file in the generation's directory, then `os.replace`.
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
  widen them. Files are created with `CACHE_FILE_MODE`.
- **Partial writes:** a record or key file that does not parse, or lacks a field, is a `CacheMiss(MALFORMED)`
  or a `KeyRejection`. It is never a hit.
- **Lock files** exist only while held, by the unlink rule of `FileCacheCoordinator`.
- **Separate from remembered detection:** nothing here is written to `~/.gem_provider.json`.

## `HiddenGems` changes

- **`__init__(..., cache: AbstractSecretCache = PASS_THROUGH_CACHE)`,** a keyword-only parameter after
  `refresh` from `GAL-remember`. It stores `cache` and computes `self._routing_revision` as defined under
  `CacheRequest`.
- **`dig_gem`** keeps its signature. Its body becomes:
  1. `request = CacheRequest(name, selection, self._routing_revision)`;
  2. `deadline = Deadline.after(LOOKUP_TIMEOUT_SECONDS)`;
  3. `loader(deadline)` runs today's path: `reference = self.resolve_gem(...)`, then the read
     `get_gem_within(reference, deadline=deadline)` from `GAL-parallel`, giving `ResolvedGem(reference,
     values)`;
  4. `return self._cache.get_or_load(request, loader, deadline=deadline)`.

  A `TimeoutError` from step 3 of `get_or_load` becomes `IncompleteGemLookupError` with one `LookupIssue`:
  `provider=CACHE_ISSUE_SOURCE`, `instance_id=CACHE_ISSUE_SOURCE`, `reason=CACHE_WAIT_TIMEOUT_REASON`, and
  `next_action=TIMEOUT_NEXT_ACTION`. The set of errors `dig_gem` raises stays the same.
- **`hide_gem`:** after a successful write without `dry_run`, it calls `self._cache.invalidate()`.
- **`HiddenGems.invalidate`** (`GAL-remember`) gains a first step, `self._cache.invalidate()`, and recomputes
  `_routing_revision` after detecting again.
- `HiddenGems` never closes a cache it was given. The caller that built it owns `close()`.

## Abstraction-extension gate

### 1. Extensions

Every new name is in `src/hiddengems/secret_cache.py`, except the profile builders, in
`secret_cache_profiles.py`, and the constants, in `constants/cache.py`.

| Extension | Status | Implementation owner | Actual caller | Tests |
| --- | --- | --- | --- | --- |
| records and enums above | MUST ADD | `secret_cache.py` | `SecretCache`, `HiddenGems.dig_gem` | S2, S3, S5, S8 |
| `AbstractSecretCache` | MUST ADD | `SecretCache`, `PassThroughSecretCache` | `HiddenGems.dig_gem` | S1-S10, S12-S16 |
| `AbstractAsyncSecretCache` | MUST ADD | `AsyncSecretCache` | the library's caller | S11, async profiles |
| `AbstractCacheCipher` | MUST ADD | `AesGcmCipher`, `ChaCha20Poly1305Cipher` | `SecretCache` | S1, S8, S13 |
| `AbstractGemCodec` | MUST ADD | `JsonGemCodec` | `SecretCache` | S1, S5 |
| `AbstractCacheStore` | MUST ADD | `MemoryCacheStore`, `FileCacheStore` | `SecretCache` | S1, S7, S12 |
| `AbstractCacheKeyStore` | MUST ADD | `MemoryCacheKeyStore`, `FileCacheKeyStore` | `SecretCache` | S1, S6, S13 |
| `AbstractCacheLifetime` | MUST ADD | `FixedPeriodLifetime` | `SecretCache` | S1, S6 |
| `AbstractCacheCoordinator` | MUST ADD | `ThreadCacheCoordinator`, `FileCacheCoordinator` | `SecretCache` | S1,S9,S10 |
| `HiddenGems(cache=...)`, `dig_gem` | behavior change | `hidden_gems.py` | the library's caller | S2, S3, S4, S14 |
| `hide_gem`, `invalidate` steps | behavior change | `hidden_gems.py` | the library's caller | S15, S16 |

The signature and behavior of each are in the sections above.

### 2. What each extension extends

None extends `AbstractGemProvider`, and none replaces it. The eight new abstract classes describe a cache, not a
provider, so they are a separate contract and not a parallel provider contract. `B`, the factory, and the
providers are unchanged.

### 3. Providers in P

All four keep exactly the baseline contract. None is edited. The cache wraps their existing `find_gem` and
`get_gem` through `resolve_gem` and the read in `dig_gem`.

### 4. Baseline tests whose expectations change

None. The default `PASS_THROUGH_CACHE` leaves every read going to the provider, and case S14 checks that.

### 5. Baseline

The baseline stays commit `4438234`. This proposal does not redefine `B`.

## Exceptions

Two new classes, each needed because a caller must tell it apart from every existing class:

- **`CacheIntegrityError(ValueError)`,** in `secret_cache.py`.
  - Raised by `AbstractCacheCipher.open` and `AbstractGemCodec.decode`.
  - Closest existing classes: `cryptography.exceptions.InvalidTag` belongs to one library, and a third-party
    cipher would raise something else; `ValueError` is too broad to catch safely around decoding, where it
    would also hide programming errors.
  - `SecretCache` catches it and treats the record as a miss. It never reaches a `HiddenGems` caller. Existing
    `except ValueError` handlers still catch it.
- **`SecretCacheWarning(UserWarning)`,** in `secret_cache.py`.
  - Emitted when a profile cannot be used safely for one call: wrong ownership or mode, an unreadable key
    file, or a failed write. The call then bypasses the cache.
  - Closest existing class: `RememberedStoreWarning` of `GAL-remember` reports the detection store, which this
    feature must stay separate from. A caller filters value-cache problems by this category without matching
    text.

Reused without a new class:

- `TimeoutError`, inside `SecretCache` only, becomes `IncompleteGemLookupError` at `dig_gem`;
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
- `AES_GCM_NONCE_BYTES: Final[int] = 12` and `CHACHA20_POLY1305_NONCE_BYTES: Final[int] = 12`, used only by
  their adapters. These replace the overview's single `CACHE_NONCE_BYTES`, because the nonce belongs to the
  adapter;
- `AEAD_TAG_BYTES: Final[int] = 16`;
- `CACHE_RECORD_FORMAT: Final[int] = 1` and `CACHE_KEY_FORMAT: Final[int] = 1`;
- `CACHE_MAX_RECORDS: Final[int] = 4096` and `CACHE_MAX_VALUE_BYTES: Final[int] = 1_048_576`;
- `CACHE_HOLD_POLL_SECONDS: Final[float] = 0.01`;
- `CACHE_ISSUE_SOURCE: Final[str] = "secret-cache"`;
- `CACHE_WAIT_TIMEOUT_REASON: Final[str] = "Waited for another read of the same gem until the time limit"`;
- `CACHE_DISK_UNSUPPORTED: Final[str] = "The disk cache needs POSIX ownership and modes"`;
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
    `test_async_contract_does_not_block_loop`.
  - (b) declare it and lock its implementation until an async `HiddenGems` API exists to call it.
- **A timed-out wait for the same identity:**
  - (a) `IncompleteGemLookupError` at `dig_gem`, as above. Recommended: callers already handle it, and the set
    of errors `dig_gem` raises does not grow.
  - (b) let `TimeoutError` reach the caller.
- **A write through `hide_gem`:**
  - (a) invalidate the whole cache. Recommended: writes are rare, and the contract needs no operation to find
    records by name, which the HMAC identity hides on purpose.
  - (b) add an operation that drops one name's records.

## Acceptance cases

The blocking gate is `tests/contract/test_secret_cache_contract.py`, in the contract-test location of
`GAL-plugin`. It is parametrized over the eight profile combinations. A test that claims process sharing runs
only on the disk profile. Every test uses a controlled lifetime clock, a `tmp_path` root, and fake providers
with fixture values, never a real secret or the real home directory.

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
  text contain neither the fixture value nor key material.
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
- **S15** `test_hide_gem_invalidates_the_cache`: after a write, the next read goes to the provider.
- **S16** `test_disk_profile_rejects_open_permissions`: a root with mode `0755`, or a record with mode `0644`,
  gives `SecretCacheWarning`, and the read goes to the provider.

## Dependencies on other features

- **Blocked by:**
  - `GAL-parallel`, for `Deadline` with its injected clock and for `get_gem_within`;
  - `GAL-remember`, for `HiddenGems.invalidate` and `atomic_file.exclusive_write_lock`;
  - `GAL-plugin`, for the `tests/contract/` location.
- **Relates to:**
  - the proposed Keychain existence check, which removes the second Keychain read even without a cache;
  - `GAL-encrypted-store`, which can reuse `AbstractCacheCipher` and `AbstractCacheKeyStore`.
