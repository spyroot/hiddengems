"""
Manin abstract provider specification.

Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Tuple, Any

from hiddengems.abstraction import (
    GemReference, Gem, DetectedProvider)


class AbstractGemProvider(ABC):
    """Define the storage contract implemented by every gem provider.

    Implementers provide a stable ``name`` plus ``detect``, ``find_gem``,
    ``get_gem``, and ``put_gem``. Detection and lookup metadata must not
    retrieve, print, or embed a gem value.
    """

    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def detect(cls, **options: Any) -> Tuple[DetectedProvider, ...]:
        """Return locally detected instances without secrets or remote access."""
        raise NotImplementedError

    @abstractmethod
    def find_gem(
            self,
            name: str
    ) -> GemReference | None:
        """Return metadata for ``name``, or ``None`` when it is not present."""
        raise NotImplementedError

    @abstractmethod
    def get_gem(
            self,
            reference: GemReference
    ) -> Gem:
        """Return the value identified by a previously resolved reference."""
        raise NotImplementedError

    @abstractmethod
    def put_gem(self,
                name: str,
                value: Gem, *,
                dry_run: bool = False,
                ) -> GemReference:
        """Store ``value`` as ``name`` and return non-secret metadata.

        ``dry_run`` validates and plans to write without persisting the value.
        Implementations must not print or include the value in the reference.
        """
        raise NotImplementedError
