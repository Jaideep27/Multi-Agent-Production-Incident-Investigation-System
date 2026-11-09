"""EvidenceBackend: the questions a data source must answer for the five tools.

The tools, the failure injector and the agents only see this interface, so another source
(Prometheus, Loki) can be added later as a new backend without touching them.

Each query method receives the validated args model for its tool and returns a BackendPage.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class BackendPage:
    items: list                                   # ToolItem objects, in result order
    truncated: bool = False
    next_cursor: str | None = None
    warnings: list = field(default_factory=list)


class BackendError(Exception):
    """A problem the caller caused and can fix (for example a malformed cursor)."""


class EvidenceBackend(ABC):
    @abstractmethod
    async def query_logs(self, args):
        ...

    @abstractmethod
    async def query_metrics(self, args):
        ...

    @abstractmethod
    async def get_change_history(self, args):
        ...

    @abstractmethod
    async def get_service_dependencies(self, args):
        ...

    @abstractmethod
    async def search_past_incidents(self, args):
        ...

    @abstractmethod
    async def failure_rules(self):
        """[[tool name, rules dict], ...] for the bound incident."""
