"""Read named values from the macOS Keychain through the native C bridge."""

from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import platform
from collections.abc import Mapping
from contextlib import ExitStack
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, ClassVar

from hiddengems.abstract_provider import AbstractGemProvider
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

_HG_KEYCHAIN_OK = 0
_HG_KEYCHAIN_NOT_FOUND = 1
_HG_KEYCHAIN_ABI_VERSION = 1
_LIBRARY_FILENAME = "libkeychainorpasswordread.dylib"
_LOOKUP_HELP = "Unlock the macOS Keychain, allow access if prompted, and retry"
_RESOURCE_FILES = ExitStack()
atexit.register(_RESOURCE_FILES.close)


class _HGKeychainResult(ctypes.Structure):
    _fields_ = (
        ("status", ctypes.c_int32),
        ("value", ctypes.c_void_p),
        ("value_size", ctypes.c_size_t),
        ("error_message", ctypes.c_void_p),
    )


class _NativeKeychainError(RuntimeError):
    pass


class _NativeKeychain:
    def __init__(self, library: str | Path) -> None:
        self.library = ctypes.CDLL(str(library))
        self.library.hg_keychain_abi_version.argtypes = ()
        self.library.hg_keychain_abi_version.restype = ctypes.c_uint32
        self.library.hg_keychain_result_size.argtypes = ()
        self.library.hg_keychain_result_size.restype = ctypes.c_size_t
        if self.library.hg_keychain_abi_version() != _HG_KEYCHAIN_ABI_VERSION:
            raise OSError("Unsupported native Keychain ABI version")
        if self.library.hg_keychain_result_size() != ctypes.sizeof(_HGKeychainResult):
            raise OSError("Native Keychain ABI result layout mismatch")
        self.library.hg_keychain_read.argtypes = (ctypes.c_char_p,)
        self.library.hg_keychain_read.restype = _HGKeychainResult
        self.library.hg_keychain_result_free.argtypes = (
            ctypes.POINTER(_HGKeychainResult),
        )
        self.library.hg_keychain_result_free.restype = None

    def read(self, name: str) -> str | bytes | None:
        if not isinstance(name, str) or not name or "\0" in name:
            raise ValueError("A keychain gem needs a nonempty string name")

        result = self.library.hg_keychain_read(name.encode("utf-8"))
        try:
            if result.status == _HG_KEYCHAIN_NOT_FOUND:
                return None
            if result.status != _HG_KEYCHAIN_OK:
                message = (
                    ctypes.string_at(result.error_message).decode(
                        "utf-8", errors="replace"
                    )
                    if result.error_message
                    else "Unknown native Keychain error"
                )
                raise _NativeKeychainError(message)

            value = ctypes.string_at(result.value, result.value_size)
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value
        finally:
            self.library.hg_keychain_result_free(ctypes.byref(result))


def _library_candidates(
        configured: str | None,
) -> tuple[tuple[str, EvidenceSource, Path | None], ...]:
    if configured:
        path = Path(configured).expanduser()
        return ((str(path), EvidenceSource.CALLER, path),)

    repository = Path(__file__).resolve().parents[3]
    paths: list[Path] = []
    packaged = files("hiddengems.gems").joinpath(_LIBRARY_FILENAME)
    if packaged.is_file():
        paths.append(_RESOURCE_FILES.enter_context(as_file(packaged)))
    paths.extend(
        (
            repository / "cmake-build-debug" / "Debug" / _LIBRARY_FILENAME,
            repository / "cmake-build-release" / "Release" / _LIBRARY_FILENAME,
            repository / "build" / _LIBRARY_FILENAME,
        )
    )
    candidates = [
        (str(path), EvidenceSource.KNOWN_PATH, path) for path in paths if path.is_file()
    ]
    loader_name = ctypes.util.find_library("keychainorpasswordread")
    if loader_name:
        candidates.append((loader_name, EvidenceSource.OS_FACILITY, None))
    return tuple(candidates)


class KeyringProvider(AbstractGemProvider):
    """Represent the current user's macOS Keychain native lookup bridge."""

    name: ClassVar[str] = "keyring"

    def __init__(self, **settings: Any) -> None:
        """

        :param settings:
        """
        self.instance_id = settings["instance_id"]
        self.library = settings["library"]
        self._native = _NativeKeychain(self.library)

    @classmethod
    def detect(cls, **options: Any) -> tuple[DetectedProvider, ...]:
        """

        :param options:
        :return:
        """
        if platform.system() != "Darwin":
            return ()

        for library, source, path in _library_candidates(options.get("library")):
            try:
                _NativeKeychain(library)
            except (AttributeError, OSError):
                continue
            return (
                DetectedProvider(
                    provider=cls.name,
                    instance_id="keyring:macos",
                    state=ProviderState.DETECTED,
                    evidence=(
                        DetectionEvidence(
                            source,
                            "macOS Keychain native bridge found",
                            path,
                        ),
                    ),
                    settings={"library": library},
                ),
            )
        return ()

    def find_gem(
            self,
            name: str,
            *,
            criteria: Mapping[str, Any] | None = None,
    ) -> tuple[GemReference, ...]:
        """

        :param name:
        :param criteria:
        :return:
        """
        if criteria:
            raise ProviderNotApplicable
        try:
            value = self._native.read(name)
        except (OSError, _NativeKeychainError) as error:
            raise ProviderLookupError(str(error), _LOOKUP_HELP) from error
        if value is None:
            return ()
        return (
            GemReference(
                name=name,
                provider=self.name,
                instance_id=self.instance_id,
                location={"backend": "macos-keychain"},
            ),
        )

    def get_gem(self, reference: GemReference) -> list[Gem]:
        """

        :param reference:
        :return: List of gems for a given reference.
        """
        if reference.provider != self.name or reference.instance_id != self.instance_id:
            raise ValueError("Gem reference belongs to a different provider instance")
        try:
            value = self._native.read(reference.name)
        except (OSError, _NativeKeychainError) as error:
            raise ProviderLookupError(str(error), _LOOKUP_HELP) from error
        if value is None:
            raise ProviderLookupError(
                f"Keychain item {reference.name!r} is no longer available",
                _LOOKUP_HELP,
            )
        return [value]

    def put_gem(
            self,
            name: str,
            value: Gem,
            *,
            criteria: Mapping[str, Any] | None = None,
            dry_run: bool = False,
    ) -> GemReference:
        """

        :param name:
        :param value:
        :param criteria:
        :param dry_run:
        :return:
        """
        raise NotImplementedError("macOS Keychain gem storage is not implemented")
