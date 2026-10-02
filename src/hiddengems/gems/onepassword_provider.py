"""1Password account discovery and symbolic-name lookup.
hidden gems support multiply account, caller must enable,
sdk.

Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.util import find_spec
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

_INTEGRATION_HELP = (
    "Open 1Password Settings > Developer and enable Integrate with 1Password "
    "CLI or Integrate with 1Password SDKs, then approve access"
)


def _timestamp(value: object) -> datetime | None:
    """Convert a supported value to a UTC timestamp.

    Naive datetimes are assumed to represent UTC.
    explicit, use object instead of Any, handle whitespace safely,
    and always return a timezone-aware UTC value:

    :param value: A ``datetime`` or ISO-8601 timestamp string.
    :type value: object
    :return: The parsed timezone-aware UTC timestamp, or ``None`` when invalid.
    :rtype: datetime | None
    """
    timestamp: datetime

    if isinstance(value, datetime):
        timestamp = value

    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None

        if candidate.endswith(("Z", "z")):
            candidate = f"{candidate[:-1]}+00:00"

        try:
            timestamp = datetime.fromisoformat(candidate)
        except ValueError:
            return None

    else:
        return None

    if timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    return timestamp.astimezone(UTC)


class OnePasswordProvider(AbstractGemProvider):
    """Use an authorized local CLI or desktop SDK session for one account."""

    name: ClassVar[str] = "onepassword"

    def __init__(self, **settings: Any) -> None:
        self.instance_id = settings["instance_id"]
        self.cli = Path(settings["cli"]) if settings.get("cli") else None
        self.sdk_installed = settings.get("sdk_installed", False)
        self.account_name = settings.get("account_name")

    @staticmethod
    def _known_cli_paths() -> tuple[Path, ...]:
        """Return known platform-specific 1Password CLI paths.

        :return: Ordered, deduplicated candidate paths.
        :rtype: tuple[Path, ...]
        """
        candidates: list[Path] = []
        home = Path.home()
        system = platform.system()

        def add_environment_path(
                variable: str,
                *parts: str,
        ) -> None:
            """

            :param variable:
            :param parts:
            :return:
            """
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root).joinpath(*parts))

        if system == "Darwin":
            add_environment_path("HOMEBREW_PREFIX", "bin", "op")
            candidates.extend(
                (
                    Path("/opt/homebrew/bin/op"),
                    Path("/usr/local/bin/op"),
                    Path("/opt/local/bin/op"),
                    home / ".local/bin/op",
                    home / "bin/op",
                )
            )

        elif system == "Linux":
            add_environment_path("HOMEBREW_PREFIX", "bin", "op")
            candidates.extend(
                (
                    Path("/usr/bin/op"),
                    Path("/usr/local/bin/op"),
                    Path("/home/linuxbrew/.linuxbrew/bin/op"),
                    Path("/run/current-system/sw/bin/op"),
                    home / ".local/bin/op",
                    home / ".nix-profile/bin/op",
                    home / "bin/op",
                )
            )

        elif system == "Windows":
            add_environment_path(
                "ProgramFiles",
                "1Password CLI",
                "op.exe",
            )
            add_environment_path(
                "ProgramFiles(x86)",
                "1Password CLI",
                "op.exe",
            )
            add_environment_path(
                "LOCALAPPDATA",
                "Microsoft",
                "WinGet",
                "Links",
                "op.exe",
            )
            add_environment_path(
                "LOCALAPPDATA",
                "1Password",
                "cli",
                "op.exe",
            )

            add_environment_path(
                "ChocolateyInstall",
                "bin",
                "op.exe",
            )

            add_environment_path("SCOOP", "shims", "op.exe")
            candidates.append(home / "scoop/shims/op.exe")

        return tuple(dict.fromkeys(candidates))

    @staticmethod
    def _is_executable(path: Path) -> bool:
        """Determine whether a path is a usable executable file.

        :param path: Path to validate.
        :type path: Path
        :return: ``True`` when the path is a usable executable.
        :rtype: bool
        """
        try:
            return path.is_file() and (
                    platform.system() == "Windows" or os.access(path, os.X_OK)
            )
        except OSError:
            return False

    @classmethod
    def _find_cli(
            cls,
            candidate: str | Path | None,
    ) -> tuple[Path | None, EvidenceSource]:
        """Find the 1Password CLI executable.

        :param candidate: An explicitly configured executable path or command.
        :type candidate: str | Path | None
        :return: The executable path and evidence source, or
            ``(None, EvidenceSource.UNKNOWN)``.
        :rtype: tuple[Path | None, EvidenceSource]
        """
        if candidate is not None:
            path = Path(candidate).expanduser()

            if cls._is_executable(path):
                return path, EvidenceSource.CALLER

            # Also allows the caller to provide a command such as "op".
            if executable := shutil.which(str(candidate)):
                return Path(executable), EvidenceSource.CALLER

        if executable := shutil.which("op"):
            return Path(executable), EvidenceSource.EXECUTABLE

        for path in cls._known_cli_paths():
            if cls._is_executable(path):
                return path, EvidenceSource.KNOWN_PATH

        return None, EvidenceSource.UNKNOWN

    @classmethod
    def detect(cls, **options: Any) -> tuple[DetectedProvider, ...]:
        """Record local installation/configuration, without signing in.
        :param options:
        :return:
        """
        cli, source = cls._find_cli(options.get("cli"))
        sdk_installed = find_spec("onepassword") is not None

        if cli is None and not sdk_installed:
            return ()

        evidence = []
        if cli is not None:
            evidence.append(DetectionEvidence(source, "1Password CLI found", cli))

        if sdk_installed:
            evidence.append(
                DetectionEvidence(
                    EvidenceSource.PYTHON_SDK, "1Password Python SDK importable"
                )
            )

        instances = list(options.get("instances") or [])
        if options.get("account_name"):
            instances.append({"account_name": options["account_name"]})
        instances.extend(
            {"account_name": preference["account_name"]}
            for preference in options.get("preferences", ())
            if isinstance(preference, Mapping) and preference.get("account_name")
        )

        if not instances:
            instances = [{"account_name": None}]

        records = []
        seen_accounts = set()
        for account in instances:
            if not isinstance(account, Mapping):
                raise TypeError("1Password account configuration must be an object")
            account_name = account.get("account_name")
            if account_name in seen_accounts:
                continue
            seen_accounts.add(account_name)
            account_evidence = list(evidence)
            if account_name:
                account_evidence.append(
                    DetectionEvidence(EvidenceSource.CONFIG, "Account name configured")
                )
            records.append(
                DetectedProvider(
                    provider=cls.name,
                    instance_id=f"onepassword:{account_name or 'local'}",
                    state=ProviderState.CONFIGURED
                    if account_name
                    else ProviderState.DETECTED,
                    evidence=tuple(account_evidence),
                    settings={
                        "cli": cli,
                        "sdk_installed": sdk_installed,
                        "account_name": account_name,
                    },
                )
            )
        return tuple(records)

    def _run_cli(self, *args: str) -> Any:
        if self.cli is None:
            raise ProviderLookupError("1Password CLI was not found", _INTEGRATION_HELP)
        try:
            result = subprocess.run(
                [str(self.cli), *args, "--format", "json"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProviderLookupError(
                f"1Password CLI could not be checked ({type(error).__name__})",
                _INTEGRATION_HELP,
            ) from error
        if result.returncode != 0:
            raise ProviderLookupError(
                f"1Password CLI returned exit {result.returncode}",
                _INTEGRATION_HELP,
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ProviderLookupError(
                "1Password CLI did not return JSON", _INTEGRATION_HELP
            ) from error

    def _accounts(self, wanted: str | None = None) -> tuple[str | None, ...]:
        """Resolve local account selectors; no account name is not a stop."""
        accounts = self._run_cli("account", "list")
        if not isinstance(accounts, list):
            raise ProviderLookupError(
                "1Password account list was not an array", _INTEGRATION_HELP
            )
        wanted = wanted or self.account_name
        selectors = []
        for account in accounts:
            if not isinstance(account, dict):
                continue
            if wanted and wanted not in {
                account.get("name"),
                account.get("email"),
                account.get("shorthand"),
                account.get("url"),
            }:
                continue
            selector = account.get("shorthand") or account.get("url")
            if selector:
                selectors.append(selector)
        if selectors:
            return tuple(selectors)
        if wanted:
            return (wanted,)
        return (None,)

    async def _sdk_client(self):
        if not self.sdk_installed or not self.account_name:
            raise ProviderLookupError(
                "1Password SDK needs an importable package and account name",
                "Configure an account name or enable 1Password CLI account discovery",
            )
        from onepassword.client import Client, DesktopAuth

        return await Client.authenticate(
            auth=DesktopAuth(account_name=self.account_name),
            integration_name="Hidden Gems",
            integration_version="v0.1.0",
        )

    async def _find_sdk(
            self, name: str, vault_id: str | None = None
    ) -> tuple[GemReference, ...]:
        sdk = await self._sdk_client()
        matches = []
        for vault in await sdk.vaults.list():
            if vault_id and vault.id != vault_id:
                continue
            for item in await sdk.items.list(vault.id):
                if item.title == name:
                    matches.append(
                        GemReference(
                            name=name,
                            provider=self.name,
                            instance_id=self.instance_id,
                            created_at=_timestamp(getattr(item, "created_at", None)),
                            modified_at=_timestamp(getattr(item, "updated_at", None)),
                            location={
                                "vault_id": vault.id,
                                "item_id": item.id,
                                "account": self.account_name,
                                "backend": "sdk",
                            },
                        )
                    )
        return tuple(matches)

    def find_gem(
            self, name: str, *, criteria: Mapping[str, Any] | None = None
    ) -> tuple[GemReference, ...]:
        """Find exact item titles, preserving account/vault ambiguity."""
        criteria = criteria or {}
        if set(criteria) - {"account_name", "vault_id"}:
            raise ProviderNotApplicable
        wanted_account = criteria.get("account_name")
        if wanted_account and self.account_name and wanted_account != self.account_name:
            raise ProviderNotApplicable
        cli_error: ProviderLookupError | None = None
        matches: list[GemReference] = []
        if self.cli is not None:
            try:
                for account in self._accounts(wanted_account):
                    args = ["item", "list"]
                    if account:
                        args.extend(["--account", account])
                    for item in self._run_cli(*args):
                        if item.get("title") != name:
                            continue
                        vault = item.get("vault") or {}
                        vault_id = vault.get("id") if isinstance(vault, dict) else vault
                        if (
                                criteria.get("vault_id")
                                and criteria["vault_id"] != vault_id
                        ):
                            continue
                        matches.append(
                            GemReference(
                                name=name,
                                provider=self.name,
                                instance_id=self.instance_id,
                                created_at=_timestamp(item.get("created_at")),
                                modified_at=_timestamp(item.get("updated_at")),
                                location={
                                    "vault_id": vault_id,
                                    "item_id": item["id"],
                                    "account": account,
                                    "backend": "cli",
                                },
                            )
                        )
                return tuple(matches)
            except (ProviderLookupError, KeyError, TypeError) as error:
                cli_error = ProviderLookupError(
                    error.reason
                    if isinstance(error, ProviderLookupError)
                    else "1Password CLI returned unexpected item metadata",
                    _INTEGRATION_HELP,
                    tuple(matches),
                )
        if self.sdk_installed and self.account_name:
            try:
                return asyncio.run(self._find_sdk(name, criteria.get("vault_id")))
            except Exception as error:
                raise ProviderLookupError(
                    f"1Password SDK could not check this account ({type(error).__name__})",
                    _INTEGRATION_HELP,
                    tuple(matches),
                ) from error
        if cli_error:
            raise cli_error
        raise ProviderLookupError(
            "1Password was detected but no authorized lookup path was available",
            _INTEGRATION_HELP,
        )

    async def _get_sdk(self, location: Mapping[str, Any]) -> list[Gem]:
        sdk = await self._sdk_client()
        item = await sdk.items.get(location["vault_id"], location["item_id"])
        return [
            {
                (
                        getattr(field, "title", None) or getattr(field, "id", "field")
                ): getattr(field, "value", None)
                for field in item.fields
            }
        ]

    def get_gem(self, reference: GemReference) -> list[Gem]:
        """Read one selected item and return its fields as one dict gem."""
        if reference.provider != self.name or reference.instance_id != self.instance_id:
            raise ValueError("Gem reference belongs to a different provider instance")
        location = reference.location
        if not isinstance(location, Mapping):
            raise TypeError("1Password reference needs an item location")
        if location.get("backend") == "sdk":
            return asyncio.run(self._get_sdk(location))
        args = ["item", "get", location["item_id"], "--vault", location["vault_id"]]
        if location.get("account"):
            args.extend(["--account", location["account"]])
        item = self._run_cli(*args)
        return [
            {
                field.get("label") or field.get("id", "field"): field.get("value")
                for field in item.get("fields", [])
            }
        ]

    def put_gem(
            self,
            name: str,
            value: Gem,
            *,
            criteria: Mapping[str, Any] | None = None,
            dry_run: bool = False,
    ) -> GemReference:
        """1Password item writes need a separately specified item format."""
        raise NotImplementedError("1Password gem storage is not implemented")
