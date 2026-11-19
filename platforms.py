"""Compatibility entry point; implementation: collector.domain.platforms."""
import sys
from collector.domain import platforms as _implementation
sys.modules[__name__]=_implementation
