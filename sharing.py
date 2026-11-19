"""Compatibility entry point; implementation: collector.infrastructure.evidence.sharing."""
import sys
from collector.infrastructure.evidence import sharing as _implementation
sys.modules[__name__]=_implementation
