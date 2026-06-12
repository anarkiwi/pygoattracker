"""Exceptions raised by pygoattracker."""


class GoatTrackerError(Exception):
    """Base class for all pygoattracker errors."""


class SngParseError(GoatTrackerError):
    """A .SNG file (or byte string) could not be parsed."""


class SngValidationError(GoatTrackerError):
    """A Song does not satisfy the .SNG format limits."""


class NinjaParseError(GoatTrackerError):
    """A NinjaTracker 2 song file could not be parsed."""


class NinjaValidationError(GoatTrackerError):
    """A NinjaSong does not satisfy the NinjaTracker 2 format limits."""
