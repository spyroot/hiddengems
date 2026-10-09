"""Values and non-secret metadata shared by Hidden Gems providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import IO, Any, TypeAlias


class ProviderNotAvailableError(LookupError):
    """Report that the caller selected a provider that is not available."""


Gem: TypeAlias = str | bytes | list[Any] | dict[str, Any] | IO[str] | IO[bytes]


class ProviderState(StrEnum):
    """Describe how far local provider discovery has progressed."""

    DETECTED = "detected"
    CONFIGURED = "configured"
    READY = "ready"


class EvidenceSource(StrEnum):
    """Identify the non-secret source that established provider availability."""

    UNKNOWN = "unknown"
    CALLER = "caller"
    CONFIG = "config"
    ENVIRONMENT = "environment"
    OS_FACILITY = "os_facility"
    EXECUTABLE = "executable"
    PYTHON_SDK = "python_sdk"
    KNOWN_PATH = "known_path"
    REMEMBERED = "remembered"


@dataclass(frozen=True)
class DetectionEvidence:
    """Record one non-secret observation made while detecting a provider."""

    source: EvidenceSource
    description: str
    path: Path | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True)
class DetectedProvider:
    """Describe one detected provider instance and its local evidence."""

    provider: str
    instance_id: str
    state: ProviderState
    evidence: tuple[DetectionEvidence, ...]
    settings: dict[str, Any]


@dataclass(frozen=True)
class GemReference:
    """Describe a stored gem without containing its secret value."""

    name: str
    provider: str
    instance_id: str
    created_at: datetime | None = None
    modified_at: datetime | None = None
    location: dict[str, Any] | str | None = None


class ProviderLookupError(Exception):
    """A provider could not check a gem; no absence claim is implied."""

    def __init__(
            self,
            reason: str,
            next_action: str,
            matches: tuple[GemReference, ...] = (),
    ) -> None:
        self.reason = reason
        self.next_action = next_action
        self.matches = matches
        super().__init__(reason)


class ProviderNotApplicable(Exception):
    """This instance is outside the caller's partial preference."""


@dataclass(frozen=True)
class LookupIssue:
    """A non-secret explanation for one unchecked provider instance."""

    provider: str
    instance_id: str
    reason: str
    next_action: str


@dataclass(frozen=True)
class ProviderObservation:
    """Provider state and evidence; None means declared but not detected."""

    provider: str
    instance_id: str
    state: ProviderState | None
    evidence: tuple[DetectionEvidence, ...]


@dataclass(frozen=True)
class LookupResult:
    """Confirmed locations and providers that could not be checked."""

    name: str
    matches: tuple[GemReference, ...]
    issues: tuple[LookupIssue, ...]
    providers: tuple[ProviderObservation, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return printable metadata only; gem values are never included."""
        return {
            "name": self.name,
            "matches": [
                {
                    "provider": match.provider,
                    "instance_id": match.instance_id,
                    "location": match.location,
                    "created_at": match.created_at.isoformat()
                    if match.created_at
                    else None,
                    "modified_at": match.modified_at.isoformat()
                    if match.modified_at
                    else None,
                }
                for match in self.matches
            ],
            "issues": [
                {
                    "provider": issue.provider,
                    "instance_id": issue.instance_id,
                    "reason": issue.reason,
                    "next_action": issue.next_action,
                }
                for issue in self.issues
            ],
            "providers": [
                {
                    "provider": provider.provider,
                    "instance_id": provider.instance_id,
                    "state": provider.state.value if provider.state else None,
                    "evidence": [
                        {
                            "source": item.source.value,
                            "description": item.description,
                            "path": str(item.path) if item.path else None,
                        }
                        for item in provider.evidence
                    ],
                }
                for provider in self.providers
            ],
        }
