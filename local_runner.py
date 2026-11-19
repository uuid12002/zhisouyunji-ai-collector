"""Compatibility import for local-only execution."""
import sys
from collector import local_bootstrap as _implementation
sys.modules[__name__]=_implementation
