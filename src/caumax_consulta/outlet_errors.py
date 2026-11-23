"""Shared signal for a reviewable outlet choice instead of a guessed basin."""
from copy import deepcopy


class OutletSelectionRequired(ValueError):
    """Keep the original point and native raster context for manual review."""

    def __init__(self, message, selection_context=None):
        super().__init__(str(message))
        self.selection_context = deepcopy(dict(selection_context or {}))
