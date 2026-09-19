"""Handbrake: the immutable core (DESIGN §5).

This package is hash-pinned (ADR-0007). The runtime (`mito/`) has no write access to it and
reaches it only through the localhost API (ADR-0002). Nothing in here may import from `mito`.
"""

__version__ = "0.0.1"
