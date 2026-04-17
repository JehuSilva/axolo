"""Internationalization helpers.

Currently only Spanish month names are supported.  The ``locale`` field on
``OrganizerConfig`` is reserved for future use to add other locales.
"""

from __future__ import annotations

MONTH_NAMES_ES = [
    "",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]

MONTH_NAMES_ES_SHORT = [
    "",
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
]

MONTH_NAMES_ES_CAP = [name.capitalize() for name in MONTH_NAMES_ES]
