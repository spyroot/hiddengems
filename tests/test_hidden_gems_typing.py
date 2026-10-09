"""Focused behavioral coverage for typing-compatible provider contracts."""

from __future__ import annotations

from collections import UserDict
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from hiddengems.abstraction import DetectedProvider, Gem, GemReference, ProviderState
from hiddengems.gem_provider import GemProvider
from hiddengems.hidden_gems import HiddenGems


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        (None, (None, None, {})),
        ("fixture", ("fixture", None, {})),
        (
            MappingProxyType(
                {
                    "provider": "fixture",
                    "instance_id": "fixture:proxy",
                    "region": "test",
                }
            ),
            ("fixture", "fixture:proxy", {"region": "test"}),
        ),
        (
            UserDict(
                {
                    "provider": "fixture",
                    "instance_id": "fixture:userdict",
                    "region": "test",
                }
            ),
            ("fixture", "fixture:userdict", {"region": "test"}),
        ),
    ],
)
def test_selection_returns_fresh_mutable_criteria(
    selected: str | Mapping[str, Any] | None,
    expected: tuple[str | None, str | None, dict[str, Any]],
) -> None:
    """Selection always isolates mutable criteria from caller-owned mappings."""
    source_before = dict(selected) if isinstance(selected, Mapping) else None

    first = HiddenGems._selection(selected)
    second = HiddenGems._selection(selected)

    assert len(first) == 3
    assert first == expected
    assert isinstance(first[2], dict)
    assert first[2] is not second[2]

    first[2].update({"extra": True})

    assert second == expected
    if isinstance(selected, Mapping):
        assert dict(selected) == source_before


def test_detect_accepts_read_only_mapping_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detection expands any Mapping implementation into provider options."""
    received: list[dict[str, Any]] = []

    class FakeProvider:
        name = "fixture"

        @classmethod
        def detect(cls, **options: Any) -> tuple[DetectedProvider, ...]:
            """Record the options received by the fake provider."""
            received.append(options)
            return ()

    monkeypatch.setattr(GemProvider, "provider_types", (FakeProvider,))
    configuration = MappingProxyType({"endpoint": "fixture.invalid"})

    assert GemProvider.detect(fixture=configuration) == ()
    assert received == [{"endpoint": "fixture.invalid"}]


def test_hidden_gems_accepts_user_mapping_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Construction accepts a non-dict Mapping for one provider's settings."""
    received: list[dict[str, Any]] = []

    class FakeProvider:
        name = "fixture"

        @classmethod
        def detect(cls, **options: Any) -> tuple[DetectedProvider, ...]:
            """Record constructor-generated detection options."""
            received.append(options)
            return ()

    monkeypatch.setattr(GemProvider, "provider_types", (FakeProvider,))

    gems = HiddenGems(
        config_path=tmp_path / "missing.json",
        provider_settings={
            "fixture": UserDict({"endpoint": "fixture.invalid"}),
        },
    )

    assert gems.records == ()
    assert received == [
        {"endpoint": "fixture.invalid", "preferences": []},
    ]


def test_dig_gem_normalizes_provider_tuple_without_flattening_nested_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tuple provider results become a list while nested gem lists stay intact."""
    record = DetectedProvider(
        "fixture",
        "fixture:local",
        ProviderState.CONFIGURED,
        (),
        {},
    )
    reference = GemReference("TOKEN", record.provider, record.instance_id)

    class TupleProvider:
        def find_gem(
            self,
            name: str,
            *,
            criteria: Mapping[str, Any] | None = None,
        ) -> tuple[GemReference, ...]:
            """Return the single deterministic fake reference."""
            assert name == reference.name
            assert criteria == {}
            return (reference,)

        def get_gem(self, selected: GemReference) -> tuple[Gem, ...]:
            """Return tuple values including one list-valued gem."""
            assert selected == reference
            return ("fake-token", ["nested-a", "nested-b"])

    provider = TupleProvider()

    def create(
        cls: type[GemProvider], selected: DetectedProvider
    ) -> TupleProvider:
        """Return the fake provider for the requested record."""
        assert selected == record
        return provider

    monkeypatch.setattr(GemProvider, "create", classmethod(create))
    gems = HiddenGems(
        providers=(record,),
        config_path=tmp_path / "missing.json",
    )

    result = gems.dig_gem("TOKEN")

    assert isinstance(result, list)
    assert result == ["fake-token", ["nested-a", "nested-b"]]
