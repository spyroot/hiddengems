"""
Store and retrieve named string gems in caller-selected dotenv files.

Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com
"""

from __future__ import annotations

import os
import stat
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import StringIO
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar, TypeGuard

from dotenv import dotenv_values, set_key

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
from hiddengems.gems.types import PathValue

_LOOKUP_HELP = "Check the selected dotenv path and file permissions"


def read_line_callback(value: str) -> str:
    """Return one resolved dotenv value without interpreting its contents."""
    return value


@contextmanager
def _exclusive_write_lock(filename: Path) -> Iterator[None]:
    """Serialize writers using a stable lock on each supported platform.
    :param filename:
    :return:
    """
    if os.name == "nt":
        import msvcrt

        lock_path = filename.with_name(f".{filename.name}.lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)
        return

    import fcntl

    directory = os.open(filename.parent, os.O_RDONLY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        yield
    finally:
        os.close(directory)


@contextmanager
def _rewrite_file(filename: Path, contents: str) -> Iterator[Path]:
    """Yield a temporary file and atomically replace ``filename`` on success.
    :param filename:
    :param contents:
    :return:
    """
    temporary = None
    try:
        with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=filename.parent,
                prefix=".hidden-gems-",
                delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        yield temporary
        os.chmod(temporary, 0o600)
        os.replace(temporary, filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class HiddenDotFileGem:
    """Store named strings in the caller's chosen .env file."""

    def __init__(
            self,
            filename: PathValue,
            *,
            backup_filename: PathValue | None = None,
            parser_fn: Callable[[str], Gem] | None = None,
    ) -> None:
        """Select the gem file, optional backup file, and value parser

        :param filename:
        :param backup_filename:
        :param parser_fn:
        """
        if parser_fn is not None and not callable(parser_fn):
            raise TypeError("parser_fn must be callable")

        self.parser_fn = read_line_callback if parser_fn is None else parser_fn
        self.filename = Path(filename).expanduser()
        self.backup_filename = (
            Path(backup_filename).expanduser() if backup_filename is not None else None
        )
        if (
                self.backup_filename is not None
                and self.backup_filename.resolve() == self.filename.resolve()
        ):
            raise ValueError("Select a backup filename different from the gem file.")

    def dig_gem(self, name: str) -> Gem:
        """Parse and return the value stored as ``name`` or raise ``KeyError``.
        :param name:
        :return:
        """
        return self.parser_fn(self._read_raw(name))

    def _read_raw(self, name: str) -> str:
        """Return one uninterpreted dotenv value.
        :param name:
        :return:
        """
        value = dotenv_values(self.filename, interpolate=False).get(name)
        if value is None:
            raise KeyError(name)
        return value

    def hide_gem(self, name: str, value: Gem, *, dry_run: bool = False) -> None:
        """Store one named string, or validate only when ``dry_run`` is true.

        :param name:
        :param value:
        :param dry_run:
        :return:
        """
        if not isinstance(name, str) or not name:
            raise ValueError("A gem needs a nonempty string name.")
        if not isinstance(value, str):
            raise TypeError("DotFile currently stores string gems.")
        if dry_run:
            return

        # Lock stable storage, not the inode atomic replace discards.
        with _exclusive_write_lock(self.filename):
            exists = self.filename.exists()
            contents = self.filename.read_text(encoding="utf-8") if exists else ""
            if (
                    exists
                    and stat.S_IMODE(self.filename.stat().st_mode) == 0o600
                    and dotenv_values(stream=StringIO(contents), interpolate=False).get(
                name
            )
                    == value
            ):
                return

            with _rewrite_file(self.filename, contents) as temporary:
                set_key(temporary, name, value, quote_mode="always")
                if dotenv_values(temporary, interpolate=False).get(name) != value:
                    raise ValueError(
                        "The gem name cannot be represented in a .env file."
                    )
                if exists and self.backup_filename is not None:
                    with _rewrite_file(self.backup_filename, contents):
                        pass


@dataclass(frozen=True)
class _DotEnvCandidate:
    """One canonical dotenv path proposed by config or bounded discovery."""

    path: Path
    backup_filename: PathValue | None
    source: EvidenceSource


@dataclass
class _DotEnvPathRecord:
    """Merged non-secret observations for one canonical dotenv path."""

    backup_filename: PathValue | None
    access_issue: str | None
    evidence: list[DetectionEvidence] = field(default_factory=list)


def _is_path_value(value: object) -> TypeGuard[PathValue]:
    """Determine whether a value is a string-compatible path.

    :param value: Value to validate.
    :type value: object
    :return: Whether the value is a valid ``PathValue``.
    :rtype: bool
    """
    if isinstance(value, str):
        return True

    if not isinstance(value, os.PathLike):
        return False

    try:
        return isinstance(os.fspath(value), str)
    except TypeError:
        return False


class DotEnvProvider(AbstractGemProvider):
    """Expose each readable dotenv file as one concrete provider instance."""

    name: ClassVar[str] = "dotenv"
    _MAX_SCAN_ENTRIES: ClassVar[int] = 1024
    _MAX_SCAN_SECONDS: ClassVar[float] = 0.25

    def __init__(self, **settings: Any) -> None:
        """
        :param settings:
        """
        self.instance_id = settings["instance_id"]
        self.scan_issue: str | None = self._optional_issue(
            settings.get("scan_issue"), setting="scan_issue"
        )
        self.access_issue: str | None = self._optional_issue(
            settings.get("access_issue"), setting="access_issue"
        )
        self.parser_fn = settings.get("parser_fn")

        configured_path = settings.get("path")
        configured_backup = settings.get("backup_filename")

        # These are the validated and resolved Path values.
        self.path: Path | None = self._optional_path(
            configured_path,
            setting="path",
        )
        self.backup_filename: Path | None = self._optional_path(
            configured_backup,
            setting="backup_filename",
        )

        self._dotfile: HiddenDotFileGem | None = (
            HiddenDotFileGem(
                self.path,
                backup_filename=self.backup_filename,
                parser_fn=self.parser_fn,
            )
            if self.path is not None
            else None
        )

    @staticmethod
    def _optional_issue(value: object, *, setting: str) -> str | None:
        """Validate one optional caller-visible issue message."""
        if value is None or isinstance(value, str):
            return value
        raise TypeError(f"{setting} must be a string, not {type(value).__name__}")

    @staticmethod
    def _optional_path(value: object, *, setting: str) -> Path | None:
        """Validate and resolve an optional path value.

        :param value: Original value supplied through provider settings.
        :type value: object
        :param setting: Setting name used in validation errors.
        :type setting: str
        :return: The resolved path, or ``None``.
        :rtype: Path | None
        :raises TypeError:
            If the supplied value is not a string-compatible path.
        """
        if value is None:
            return None

        if not _is_path_value(value):
            raise TypeError(
                f"{setting} must be a string or PathLike[str], "
                f"not {type(value).__name__}"
            )

        filesystem_path = os.fspath(value)
        return Path(filesystem_path).expanduser().resolve()

    @staticmethod
    def _configured_candidate(value: Any) -> _DotEnvCandidate:
        """Build one canonical candidate from caller configuration.

        :param value:
        :return:
        """
        if not isinstance(value, Mapping):
            raise TypeError("Dotenv instance configuration must be an object")
        if not value.get("path"):
            raise ValueError("Dotenv instance configuration needs a path")
        path = DotEnvProvider._optional_path(value["path"], setting="path")
        if path is None:
            raise ValueError("Dotenv instance configuration needs a path")
        backup = value.get("backup_filename")
        return _DotEnvCandidate(
            path=path,
            backup_filename=DotEnvProvider._optional_path(
                backup, setting="backup_filename"
            ),
            source=EvidenceSource.CONFIG,
        )

    @classmethod
    def _configured_candidates(
            cls,
            options: Mapping[str, Any],
    ) -> tuple[_DotEnvCandidate, ...]:
        """Resolve complete paths already supplied by config or preference.

        :param options:
        :return:
        """
        candidates: list[_DotEnvCandidate] = []
        if options.get("path"):
            candidates.append(cls._configured_candidate(options))
        for instance in options.get("instances") or ():
            candidates.append(cls._configured_candidate(instance))
        for preference in options.get("preferences") or ():
            if isinstance(preference, Mapping) and preference.get("path"):
                candidates.append(cls._configured_candidate(preference))
        return tuple(candidates)

    @staticmethod
    def _is_dotenv_name(name: str) -> bool:
        return name == ".env" or name.startswith(".env.")

    @classmethod
    def _directory_entries(
            cls,
            directory: Path,
            remaining: int,
    ) -> tuple[tuple[os.DirEntry[str], ...], bool, str | None]:
        """Build one canonical candidate from caller configuration.

        :param directory:
        :param remaining:
        :return:
        """
        try:
            with os.scandir(directory) as stream:
                entries = list(islice(stream, remaining + 1))
        except OSError:
            return (), False, "Dotenv discovery could not read every directory"

        limit_reached = len(entries) > remaining
        entries = entries[:remaining]
        entries.sort(key=lambda item: item.name)
        return tuple(entries), limit_reached, None

    @classmethod
    def _scan(cls, root: Path) -> tuple[tuple[Path, ...], str | None]:
        """Find dotenv-shaped files below ``root`` within fixed work bounds.

        :param root:
        :return:
        """
        deadline = time.monotonic() + cls._MAX_SCAN_SECONDS
        directories = deque([root])
        found: list[Path] = []
        entries_seen = 0
        issue = None
        while directories:
            if time.monotonic() >= deadline:
                issue = "Dotenv discovery time limit reached"
                break
            directory = directories.popleft()
            entries, limit_reached, read_issue = cls._directory_entries(
                directory,
                cls._MAX_SCAN_ENTRIES - entries_seen,
            )
            if read_issue is not None:
                issue = read_issue
                continue
            if limit_reached:
                issue = "Dotenv discovery entry limit reached"
                directories.clear()
            for entry in entries:
                entries_seen += 1
                if time.monotonic() >= deadline:
                    issue = "Dotenv discovery time limit reached"
                    directories.clear()
                    break
                try:
                    if entry.is_file(follow_symlinks=False):
                        if cls._is_dotenv_name(entry.name):
                            found.append(Path(entry.path).resolve())
                    elif entry.is_dir(follow_symlinks=False):
                        path = Path(entry.path)
                        if not limit_reached and not os.path.ismount(path):
                            directories.append(path)
                except OSError:
                    issue = "Dotenv discovery could not inspect every entry"
        return tuple(found), issue

    @classmethod
    def _discovered_candidates(
            cls,
            root: Path,
    ) -> tuple[tuple[_DotEnvCandidate, ...], str | None]:
        """Resolve an unspecified path through bounded local discovery.
        :param root:
        :return:
        """
        paths, issue = cls._scan(root)
        return (
            tuple(
                _DotEnvCandidate(path, None, EvidenceSource.KNOWN_PATH)
                for path in paths
            ),
            issue,
        )

    @staticmethod
    def _inspect_candidate(
            candidate: _DotEnvCandidate,
    ) -> _DotEnvPathRecord | None:
        """Classify one path without reading any dotenv value.
        :param candidate:
        :return:
        """
        try:
            exists = candidate.path.is_file()
            readable = exists and os.access(candidate.path, os.R_OK)
        except OSError:
            exists = False
            readable = False
        if not exists:
            return None
        access_issue = None if readable else "Dotenv file is not readable"
        return _DotEnvPathRecord(
            backup_filename=candidate.backup_filename,
            access_issue=access_issue,
            evidence=[
                DetectionEvidence(
                    candidate.source,
                    "Readable dotenv file" if readable else "Unreadable dotenv file",
                    candidate.path,
                )
            ],
        )

    @classmethod
    def _merge_candidates(
            cls,
            candidates: tuple[_DotEnvCandidate, ...],
    ) -> dict[Path, _DotEnvPathRecord]:
        """Collapse equivalent paths while retaining all detection evidence.
        :param candidates:
        :return:
        """

        records: dict[Path, _DotEnvPathRecord] = {}
        for candidate in candidates:
            observed = cls._inspect_candidate(candidate)
            if observed is None:
                continue
            current = records.get(candidate.path)
            if current is None:
                records[candidate.path] = observed
                continue
            if current.backup_filename is None:
                current.backup_filename = observed.backup_filename
            current.access_issue = current.access_issue or observed.access_issue
            current.evidence.extend(observed.evidence)
        return records

    @classmethod
    def _detected_record(
            cls,
            path: Path,
            record: _DotEnvPathRecord,
    ) -> DetectedProvider:
        """Convert one classified path into a provider record.

        :param path:
        :param record:
        :return:
        """
        return DetectedProvider(
            provider=cls.name,
            instance_id=f"dotenv:{path}",
            state=(
                ProviderState.DETECTED
                if record.access_issue
                else ProviderState.CONFIGURED
            ),
            evidence=tuple(record.evidence),
            settings={
                "path": path,
                "backup_filename": record.backup_filename,
                "access_issue": record.access_issue,
            },
        )

    @classmethod
    def _scan_issue_record(cls, root: Path, issue: str) -> DetectedProvider:
        """Represent incomplete bounded discovery without claiming absence.
        :param root:
        :param issue:
        :return:
        """
        return DetectedProvider(
            provider=cls.name,
            instance_id=f"dotenv:unverified:{root}",
            state=ProviderState.DETECTED,
            evidence=(
                DetectionEvidence(
                    EvidenceSource.KNOWN_PATH,
                    issue,
                    root,
                ),
            ),
            settings={"scan_issue": issue},
        )

    @classmethod
    def detect(cls, **options: Any) -> tuple[DetectedProvider, ...]:
        """Find configured files and conventional ``./.env`` without reading them.
        :param options:
        :return:
        """
        candidates: tuple[_DotEnvCandidate, ...] = cls._configured_candidates(options)
        scan_root: Path = Path.cwd().resolve()
        scan_issue: str | None = None
        if not candidates:
            candidates, scan_issue = cls._discovered_candidates(scan_root)
        records: dict[Path, _DotEnvPathRecord] = cls._merge_candidates(candidates)
        detected: tuple[DetectedProvider, ...] = tuple(
            cls._detected_record(path, record) for path, record in records.items()
        )
        if scan_issue is None:
            return detected
        return detected + (cls._scan_issue_record(scan_root, scan_issue),)

    def _check_criteria(self, criteria: Mapping[str, Any] | None) -> None:
        """

        :param criteria:
        :return:
        """
        criteria = criteria or {}
        if set(criteria) - {"path"}:
            raise ProviderNotApplicable
        if criteria.get("path"):
            if self.path is None:
                raise ProviderNotApplicable
            selected = Path(criteria["path"]).expanduser().resolve()
            if selected != self.path:
                raise ProviderNotApplicable

    def _reference(self, name: str) -> GemReference:
        """

        :param name:
        :return:
        """
        if self.path is None:
            raise ProviderLookupError(
                self.scan_issue or "Dotenv path is unknown",
                _LOOKUP_HELP,
            )
        return GemReference(
            name=name,
            provider=self.name,
            instance_id=self.instance_id,
            location={"path": str(self.path)},
        )

    def _require_dotfile(self) -> HiddenDotFileGem:
        """Return this instance's helper or preserve its uncertainty reason.
        :return:
        """
        issue = self.scan_issue or self.access_issue
        if issue:
            raise ProviderLookupError(issue, _LOOKUP_HELP)

        if self.path is None or self._dotfile is None:
            raise ProviderLookupError("Dotenv path is unknown", _LOOKUP_HELP)

        return self._dotfile

    def _require_readable_dotfile(self) -> HiddenDotFileGem:
        """Return the helper only while its file remains readable.
        :return:
        """

        dotfile = self._require_dotfile()
        if not self.path.is_file() or not os.access(self.path, os.R_OK):
            raise ProviderLookupError(
                "The selected dotenv file is no longer readable",
                _LOOKUP_HELP,
            )
        return dotfile

    def _dotfile_for_reference(self, reference: GemReference) -> HiddenDotFileGem:
        """Validate that a reference belongs to this concrete instance.
        :param reference:
        :return:
        """
        if reference.provider != self.name or reference.instance_id != self.instance_id:
            raise ValueError("Gem reference belongs to a different provider instance")
        if self.path is None:
            raise ProviderLookupError("Dotenv path is unknown", _LOOKUP_HELP)
        location = reference.location
        if not isinstance(location, Mapping) or location.get("path") != str(self.path):
            raise ValueError("Dotenv reference needs this instance's canonical path")
        return self._require_readable_dotfile()

    def find_gem(
            self,
            name: str,
            *,
            criteria: Mapping[str, Any] | None = None,
    ) -> tuple[GemReference, ...]:
        """
        Return a non-secret reference when this file contains ``name``.
        :param name:
        :param criteria:
        :return:
        """
        self._check_criteria(criteria)
        dotfile = self._require_readable_dotfile()
        try:
            dotfile._read_raw(name)
        except KeyError:
            return ()
        except (OSError, UnicodeError) as error:
            raise ProviderLookupError(
                "The selected dotenv file could not be checked "
                f"({type(error).__name__})",
                _LOOKUP_HELP,
            ) from error
        return (self._reference(name),)

    def get_gem(self, reference: GemReference) -> list[Gem]:
        """Read one selected dotenv entry as a singleton list.

        :param reference:
        :return:
        """
        dotfile = self._dotfile_for_reference(reference)
        try:
            return [dotfile.dig_gem(reference.name)]
        except KeyError as error:
            raise ProviderLookupError(
                f"Dotenv entry {reference.name!r} is no longer available",
                _LOOKUP_HELP,
            ) from error
        except (OSError, UnicodeError) as error:
            raise ProviderLookupError(
                f"The selected dotenv file could not be read ({type(error).__name__})",
                _LOOKUP_HELP,
            ) from error

    def put_gem(
            self,
            name: str,
            value: Gem,
            *,
            criteria: Mapping[str, Any] | None = None,
            dry_run: bool = False,
    ) -> GemReference:
        """Store one string in this selected dotenv file.
        :param name:
        :param value:
        :param criteria:
        :param dry_run:
        :return:
        """
        self._check_criteria(criteria)
        dotfile = self._require_dotfile()
        dotfile.hide_gem(name, value, dry_run=dry_run)
        return self._reference(name)
