"""Event Grid topics: the destinations of cost control alerts."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ._base import _Operations


class EventGridOperations(_Operations):
    """Read Event Grid topics. A cost control alert needs a topic that uses the CloudEvents v1.0 input schema and allows
    key-based authentication. (The Microsoft package for creating topics is ``azure-mgmt-eventgrid``.)"""

    def list_topics(self) -> Iterator[dict[str, Any]]:
        """Event Grid topics in the subscription."""
        yield from self._list(f"{self.subscription_scope}/providers/Microsoft.EventGrid/topics", self._v.event_grid)
