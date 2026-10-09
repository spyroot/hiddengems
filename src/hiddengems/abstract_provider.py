"""
Manin abstract provider specification.
Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from hiddengems.abstraction import (
    DetectedProvider, Gem, GemReference)


class AbstractGemProvider(ABC):
    """Define the storage contract implemented by every gem provider.

    Implementers provide a stable ``name`` plus ``detect``, ``find_gem``,
    ``get_gem``, and ``put_gem``. Detection and lookup return only non-secret
    metadata; ``get_gem`` reads the selected value.
    """

    name: ClassVar[str]

    @abstractmethod
    def __init__(self, **settings: Any) -> None:
        """Validate and store provider configuration without authenticating."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def detect(cls, **options: Any) -> tuple[DetectedProvider, ...]:
        """Discover local provider configuration without authentication."""
        raise NotImplementedError

    @abstractmethod
    def find_gem(
            self,
            name: str,
            *,
            criteria: Mapping[str, Any] | None = None,
    ) -> tuple[GemReference, ...]:
        """Return all concrete references for ``name`` in this instance."""
        raise NotImplementedError

    @abstractmethod
    def get_gem(self, reference: GemReference) -> Sequence[Gem]:
        """Return values as a sequence, wrapping a singleton as one element."""
        raise NotImplementedError

    @abstractmethod
    def put_gem(
            self,
            name: str,
            value: Gem,
            *,
            criteria: Mapping[str, Any] | None = None,
            dry_run: bool = False,
    ) -> GemReference:
        """Store ``value`` as ``name`` and return non-secret metadata.

        ``dry_run`` validates and plans to write without persisting the value.
        Implementations must not print or include the value in the reference.
        """
        raise NotImplementedError
