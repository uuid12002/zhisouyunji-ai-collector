"""Compatibility entry point; implementation: collector.collection_bootstrap."""
import sys
from collector import collection_bootstrap as _implementation
sys.modules[__name__]=_implementation
