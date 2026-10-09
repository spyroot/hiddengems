"""Resolve symbolic gem names without assuming a storage provider.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations
import json
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

from hiddengems.abstraction import (
    DetectedProvider,
    Gem,
    GemReference,
    LookupIssue,
    LookupResult,
    ProviderLookupError,
    ProviderNotApplicable,
    ProviderNotAvailableError,
    ProviderObservation,
    ProviderState,
)
from hiddengems.gem_provider import GemProvider


class GemNotFoundError(LookupError):
    """No checked provider contains the requested name."""


class ProviderRequiredError(ValueError):
    """A write needs an explicit storage provider."""


class AmbiguousGemError(LookupError):
    """More than one concrete location matches a symbolic name."""

    def __init__(self, result: LookupResult) -> None:
        self.result = result
        super().__init__(f"{result.name!r} has {len(result.matches)} matches")


class IncompleteGemLookupError(LookupError):
    """A detected provider could not be checked for the requested name."""

    def __init__(self, result: LookupResult) -> None:
        self.result = result
        super().__init__(
            f"{result.name!r}: {len(result.matches)} confirmed match(es), "
            f"{len(result.issues)} unchecked provider(s)"
        )


class StaleGemPreferenceError(LookupError):
    """A chosen provider is unavailable or does not contain this name."""


class HiddenGems:
    """Route gem lookup by concrete provider instance and non-secret evidence."""

    def __init__(
            self,
            providers: Sequence[DetectedProvider] | None = None,
            *,
            config_path: str | Path | None = None,
            provider_settings: Mapping[str, Any] | None = None,
            preferences: Mapping[str, Any] | None = None,
            interactive: bool = False,
    ) -> None:
        """

        :param providers:
        :param config_path:
        :param provider_settings:
        :param preferences:
        :param interactive:
        """
        if config_path is None:
            config_path = Path.home() / ".gem_provider.json"

        path = Path(config_path).expanduser()
        document = json.loads(path.read_text()) if path.is_file() else {}

        if not isinstance(document, dict):
            raise TypeError("Gem provider configuration must be a JSON object")

        configured = document.get("providers", {})
        documented_preferences = document.get("preferences", {})

        if not isinstance(configured, dict) or not isinstance(
                documented_preferences, dict
        ):
            raise TypeError("Providers and preferences must be objects")

        self.preferences = {**documented_preferences, **(preferences or {})}
        configured = {**configured, **(provider_settings or {})}
        known = {item.name for item in GemProvider.provider_types}
        hints: dict[str, list[MutableMapping[str, Any]]] = {}

        for preference in self.preferences.values():
            kind, _, details = self._selection(preference)
            if kind:
                hints.setdefault(kind, []).append(details)
        detection_options = {}

        ordered = [
            *configured,
            *(kind for kind in hints if kind not in configured),
            *(
                kind
                for kind in sorted(known)
                if kind not in configured and kind not in hints
            ),
        ]
        for kind in ordered:
            if kind not in known:
                continue
            options = configured.get(kind, {})
            if isinstance(options, list):
                options = {"instances": options}
            if not isinstance(options, Mapping):
                raise TypeError(f"{kind!r} provider settings must be an object")
            detection_options[kind] = {**options, "preferences": hints.get(kind, [])}

        self.records = tuple(
            providers
            if providers is not None
            else GemProvider.detect(**detection_options)
        )

        self._unavailable = {
            kind: (
                "Configured provider plugin is not implemented"
                if kind not in known
                else "Configured provider was not detected"
            )
            for kind in (
                    (set(configured) | set(hints))
                    - {record.provider for record in self.records}
            )
        }

        self._instances = {
            (record.provider, record.instance_id): GemProvider.create(record)
            for record in self.records
        }

        self._states = {
            (record.provider, record.instance_id): record.state
            for record in self.records
        }

        self.interactive = interactive

    @staticmethod
    def _selection(
            selected: str | Mapping[str, Any] | None,
    ) -> tuple[str | None, str | None, MutableMapping[str, Any]]:
        """Normalize a preference without mutating the caller's mapping.

        :param selected: A provider name, preference mapping, or no preference.
        :returns: Provider, instance ID, and a fresh mutable criteria mapping.
        """
        if selected is None:
            return None, None, {}
        if isinstance(selected, str):
            return selected, None, {}
        if not isinstance(selected, Mapping):
            raise TypeError("A preference must be a provider name or an object")
        provider = selected.get("provider")
        if provider is not None and not isinstance(provider, str):
            raise TypeError("A provider preference must be a string")

        return (
            provider,
            selected.get("instance_id"),
            {
                key: value
                for key, value in selected.items()
                if key not in ("provider", "instance_id")
            },
        )

    def inspect_gem(
            self,
            name: str,
            *,
            provider: str | None = None,
            criteria: Mapping[str, Any] | None = None,
    ) -> LookupResult:
        """Collect confirmed locations and reasons any location is unchecked
        :param name:
        :param provider:
        :param criteria:
        :return:
        """
        if not isinstance(name, str) or not name:
            raise ValueError("A gem needs a nonempty symbolic name")

        preferred = provider if provider is not None else self.preferences.get(name)
        selected_type, selected_id, selected_criteria = self._selection(preferred)
        selected_criteria.update(criteria or {})
        records = tuple(
            record
            for record in self.records
            if (selected_type is None or record.provider == selected_type)
            and (selected_id is None or record.instance_id == selected_id)
        )
        if (
                selected_type is not None
                and not records
                and selected_type not in self._unavailable
        ):
            raise ProviderNotAvailableError(
                f"Selected provider {selected_type!r} is not currently available"
            )

        matches: list[GemReference] = []
        issues: list[LookupIssue] = []
        applicable: list[DetectedProvider] = []
        unavailable = (
            self._unavailable
            if selected_type is None
            else {selected_type: self._unavailable[selected_type]}
            if selected_type in self._unavailable
            else {}
        )
        issues.extend(
            LookupIssue(
                provider=kind,
                instance_id=kind,
                reason=reason,
                next_action=f"Configure or install the {kind} provider and retry",
            )
            for kind, reason in sorted(unavailable.items())
        )
        for record in records:
            identity = (record.provider, record.instance_id)
            try:
                found = self._instances[identity].find_gem(
                    name, criteria=selected_criteria
                )
            except ProviderNotApplicable:
                continue
            except ProviderLookupError as error:
                applicable.append(record)
                self._states[identity] = record.state
                matches.extend(error.matches)
                issues.append(
                    LookupIssue(
                        record.provider,
                        record.instance_id,
                        error.reason,
                        error.next_action,
                    )
                )
            else:
                applicable.append(record)
                self._states[identity] = ProviderState.READY
                matches.extend(found)
        observations = tuple(
            ProviderObservation(
                record.provider,
                record.instance_id,
                self._states[(record.provider, record.instance_id)],
                record.evidence,
            )
            for record in applicable
        ) + tuple(
            ProviderObservation(kind, kind, None, ()) for kind in sorted(unavailable)
        )
        return LookupResult(name, tuple(matches), tuple(issues), observations)

    def resolve_gem(
            self,
            name: str,
            *,
            provider: str | None = None,
            criteria: Mapping[str, Any] | None = None,
    ) -> GemReference:
        """Select exactly one location, never silently ignoring unchecked stores."""
        result = self.inspect_gem(name, provider=provider, criteria=criteria)
        selected = provider is not None or self.preferences.get(name) not in (None, {})
        if not result.matches:
            if result.issues:
                raise IncompleteGemLookupError(result)
            if selected:
                raise StaleGemPreferenceError(name)
            raise GemNotFoundError(name)
        if result.issues:
            raise IncompleteGemLookupError(result)
        if len(result.matches) == 1:
            return result.matches[0]
        if self.interactive:
            for index, reference in enumerate(result.matches, 1):
                print(
                    f"{index}. {reference.provider} / {reference.instance_id} "
                    f"/ {reference.location}"
                )
            choice = input("Select gem location: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(result.matches):
                return result.matches[int(choice) - 1]
        raise AmbiguousGemError(result)

    def dig_gem(
            self,
            name: str,
            *,
            provider: str | None = None,
            criteria: Mapping[str, Any] | None = None,
    ) -> list[Gem]:
        """Return the resolved provider's values, always as a list.

        :param name:
        :param provider:
        :param criteria:
        :return:
        """
        reference = self.resolve_gem(name, provider=provider, criteria=criteria)
        return list(
            self._instances[(reference.provider, reference.instance_id)].get_gem(
                reference
            )
        )

    def hide_gem(
            self,
            name: str,
            value: Gem,
            *,
            provider: str | None = None,
            criteria: Mapping[str, Any] | None = None,
            dry_run: bool = False,
    ) -> GemReference:
        """Write through one explicitly selected concrete provider.

        :param name:
        :param value:
        :param provider:
        :param criteria:
        :param dry_run:
        :return:
        """
        if provider is None:
            raise ProviderRequiredError("Specify a provider when storing a gem")

        records = [record for record in self.records if record.provider == provider]
        if not records:
            raise ProviderNotAvailableError(provider)
        if len(records) != 1:
            raise AmbiguousGemError(LookupResult(name, (), ()))
        record = records[0]
        return self._instances[(record.provider, record.instance_id)].put_gem(
            name, value, criteria=criteria, dry_run=dry_run
        )
