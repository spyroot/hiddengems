"""Focused macOS Keychain bridge tests using only fake secret values."""

from __future__ import annotations

import ctypes
from unittest.mock import Mock, call

import pytest

from hiddengems.abstraction import (
    EvidenceSource,
    GemReference,
    ProviderLookupError,
)
from hiddengems.gems import keyring_provider as keyring


class _Callable:
    def __init__(self, implementation):
        self.implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.implementation(*args)


class _FakeLibrary:
    def __init__(self, result):
        self.freed = 0
        self.hg_keychain_abi_version = _Callable(
            lambda: keyring._HG_KEYCHAIN_ABI_VERSION
        )
        self.hg_keychain_result_size = _Callable(
            lambda: ctypes.sizeof(keyring._HGKeychainResult)
        )
        self.hg_keychain_read = _Callable(lambda name: result)
        self.hg_keychain_result_free = _Callable(self._free)

    def _free(self, result):
        self.freed += 1


def _provider(monkeypatch, native):
    monkeypatch.setattr(keyring, "_NativeKeychain", lambda library: native)
    return keyring.KeyringProvider(
        instance_id="keyring:macos",
        library="/fixture/libkeychainorpasswordread.dylib",
    )


def test_native_bridge_copies_text_and_always_frees(monkeypatch):
    value = ctypes.create_string_buffer(b"fake-keychain-value")
    result = keyring._HGKeychainResult(
        keyring._HG_KEYCHAIN_OK,
        ctypes.addressof(value),
        len(b"fake-keychain-value"),
        None,
    )
    library = _FakeLibrary(result)
    monkeypatch.setattr(keyring.ctypes, "CDLL", lambda path: library)

    native = keyring._NativeKeychain("/fixture/library.dylib")

    assert native.read("test_from_keychain") == "fake-keychain-value"
    assert library.freed == 1


def test_native_bridge_rejects_an_incompatible_abi(monkeypatch):
    library = _FakeLibrary(keyring._HGKeychainResult())
    library.hg_keychain_abi_version = _Callable(lambda: 99)
    monkeypatch.setattr(keyring.ctypes, "CDLL", lambda path: library)

    with pytest.raises(OSError, match="ABI version"):
        keyring._NativeKeychain("/fixture/library.dylib")


def test_detect_records_native_library_without_reading_a_gem(monkeypatch, tmp_path):
    library = tmp_path / "libkeychainorpasswordread.dylib"
    library.touch()
    opened = []
    monkeypatch.setattr(keyring.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        keyring,
        "_NativeKeychain",
        lambda path: opened.append(path) or object(),
    )

    records = keyring.KeyringProvider.detect(library=str(library))

    assert opened == [str(library)]
    assert len(records) == 1
    assert records[0].provider == "keyring"
    assert records[0].instance_id == "keyring:macos"
    assert records[0].settings == {"library": str(library)}
    assert records[0].evidence[0].source == EvidenceSource.CALLER
    assert records[0].evidence[0].path == library


def test_provider_find_and_get_use_the_native_reader_without_leaking_value(
    monkeypatch,
):
    native = Mock()
    native.read.return_value = "fake-keychain-value"
    provider = _provider(monkeypatch, native)

    references = provider.find_gem("test_from_keychain")

    assert len(references) == 1
    assert references[0].location == {"backend": "macos-keychain"}
    assert "fake-keychain-value" not in repr(references[0])
    assert provider.get_gem(references[0]) == ["fake-keychain-value"]
    assert native.read.call_args_list == [
        call("test_from_keychain"),
        call("test_from_keychain"),
    ]


def test_provider_reports_absence_and_native_errors(monkeypatch):
    native = Mock()
    native.read.return_value = None
    provider = _provider(monkeypatch, native)

    assert provider.find_gem("missing") == ()
    with pytest.raises(ProviderLookupError, match="no longer available"):
        provider.get_gem(GemReference("missing", "keyring", "keyring:macos"))

    native.read.side_effect = keyring._NativeKeychainError("fixture failure")
    with pytest.raises(ProviderLookupError, match="fixture failure"):
        provider.find_gem("broken")
