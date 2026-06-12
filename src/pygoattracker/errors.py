"""Exceptions raised by pygoattracker."""


class GoatTrackerError(Exception):
    """Base class for all pygoattracker errors."""


class SngParseError(GoatTrackerError):
    """A .SNG file (or byte string) could not be parsed."""


class SngValidationError(GoatTrackerError):
    """A Song does not satisfy the .SNG format limits."""
