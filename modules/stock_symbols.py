"""Provider symbol syntax is not an instrument-type classifier.

Common share classes such as BRK.B are valid symbols. Admission still requires
reference metadata; a suffix alone cannot distinguish equity from warrants.
"""
import re


def valid_stock_symbol(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9]{0,14}(?:\.[A-Z0-9]{1,6})?", value) is not None
