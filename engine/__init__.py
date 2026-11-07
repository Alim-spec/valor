"""Deterministic calculation core (ARC-01).

Pure functions over Decimal: no database, no clock, no randomness, no ML.
Identical inputs and configuration always produce identical results.
Every module returns results together with the clause reference that drives them.
"""

ENGINE_VERSION = '0.1.0'
