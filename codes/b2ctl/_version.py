"""Single source of the b2ctl version string.

Kept in its own tiny module so importing the version never drags in the whole
application graph (cli -> watch/core/zfs/installer …). __init__.py and cli.py
both read it from here (F-066). Bump this string on every release (CLAUDE.md §10).
"""
__version__ = "0.9.0-itmode"
