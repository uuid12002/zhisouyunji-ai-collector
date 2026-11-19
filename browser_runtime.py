"""Compatibility entry point; implementation: collector.infrastructure.browser.runtime."""
import sys
from collector.infrastructure.browser import runtime as _implementation
if __name__ == "__main__":
    import argparse, asyncio
    p=argparse.ArgumentParser()
    p.add_argument("platform",choices=_implementation.URLS)
    asyncio.run(_implementation.login(p.parse_args().platform))
else:
    sys.modules[__name__]=_implementation
