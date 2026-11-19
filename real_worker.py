"""Stable worker entry point."""
import sys
from collector import worker_bootstrap as _implementation
if __name__=='__main__':
    import asyncio
    asyncio.run(_implementation.main())
else:
    sys.modules[__name__]=_implementation
