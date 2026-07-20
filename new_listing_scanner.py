"""Compatibility import for the canonical new-listing scanner.

Production code lives in :mod:`modules.new_listing_scanner`. Keeping this
small shim prevents old scripts from importing a stale, divergent copy.
"""

from modules.new_listing_scanner import *  # noqa: F401,F403
