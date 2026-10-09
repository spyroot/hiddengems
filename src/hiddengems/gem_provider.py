"""Discover concrete instances from registered provider plugins."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any, ClassVar

from hiddengems.abstract_provider import AbstractGemProvider
from hiddengems.abstraction import DetectedProvider
from hiddengems.gems.dotenv_provider import DotEnvProvider
from hiddengems.gems.k8s_provider import KubernetesProvider
from hiddengems.gems.keyring_provider import KeyringProvider

OnePasswordProvider = import_module(
    "hiddengems.gems.onepassword_provider"
).OnePasswordProvider


class GemProvider:
    """Register plugins without knowing their configuration fields."""

    provider_types: ClassVar[tuple[type[AbstractGemProvider], ...]] = (
        OnePasswordProvider,
        DotEnvProvider,
        KeyringProvider,
        KubernetesProvider,
    )

    @classmethod
    def detect(
            cls, **options: Any
    ) -> tuple[DetectedProvider, ...]:
        """
        :param options:
        :return:
        """
        registered = {provider_type.name for provider_type in cls.provider_types}
        unknown = set(options) - registered
        if unknown:
            raise ValueError(f"Unregistered providers: {sorted(unknown)}")

        by_name = {
            provider_type.name: provider_type for provider_type in cls.provider_types
        }

        ordered_names = [*options, *(name for name in by_name if name not in options)]
        records: list[DetectedProvider] = []
        seen: set[tuple[str, str]] = set()
        for name in ordered_names:
            provider_type = by_name[name]
            settings = options.get(name, {})
            if isinstance(settings, list):
                settings = {"instances": settings}
            if not isinstance(settings, Mapping):
                raise TypeError(
                    f"{provider_type.name!r} configuration must be an object or list"
                )
            for record in provider_type.detect(**settings):
                identity = (record.provider, record.instance_id)
                if identity not in seen:
                    seen.add(identity)
                    records.append(record)
        return tuple(records)

    @classmethod
    def create(
            cls,
            record: DetectedProvider,
            **overrides: Any,
    ) -> AbstractGemProvider:
        """
        :param record:
        :param overrides:
        :return:
        """
        provider_type = next(
            (
                candidate
                for candidate in cls.provider_types
                if candidate.name == record.provider
            ),
            None,
        )
        if provider_type is None:
            raise ValueError(f"Unknown provider type: {record.provider!r}")
        return provider_type(
            **{**record.settings, **overrides, "instance_id": record.instance_id}
        )
