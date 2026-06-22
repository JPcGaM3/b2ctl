"""pytest bootstrap for the b2ctl test suite.

Run from the package root:  cd codes && python3 -m pytest tests/

Puts this tests/ directory on sys.path so every per-module test file can do
`from helpers import _disk, _RAIDZ_STATUS, ...` for the shared Disk factory and
sample command outputs.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
