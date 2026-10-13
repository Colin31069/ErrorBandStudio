from __future__ import annotations


def spreadsheet_safe_cell(value: object) -> object:
    """Escape formula-like text in CSV without changing numeric measurements."""
    if not isinstance(value, str):
        return value
    leading = value.lstrip()
    if not leading or leading[0] not in "=+-@":
        return value
    # Legitimate negative/positive measurement strings must remain numeric.
    try:
        float(leading)
    except ValueError:
        return "'" + value
    return value
