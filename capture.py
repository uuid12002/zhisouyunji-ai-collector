"""Compatibility entry point; implementation: collector.infrastructure.evidence.capture."""
import sys
from collector.infrastructure.evidence import capture as _implementation
sys.modules[__name__]=_implementation
