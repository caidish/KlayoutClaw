"""Shared state and base backend class for the Qiskit MCP server."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharedState:
    """Shared state passed to all backends.

    Provides access to the IPython kernel namespace where Qiskit objects live.
    """

    ipython: Any
    namespace: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.ipython and not self.namespace:
            self.namespace = self.ipython.user_ns


class BaseBackend:
    """Base class for all Qiskit MCP backends."""

    def __init__(self, state: SharedState):
        self.state = state

    @property
    def ipython(self):
        return self.state.ipython

    @property
    def namespace(self):
        return self.state.namespace
