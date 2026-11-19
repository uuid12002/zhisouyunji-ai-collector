"""Compatibility entry point; implementation: collector.infrastructure.evidence.sources."""
import sys
from collector.infrastructure.evidence import sources as _implementation
sys.modules[__name__]=_implementation
