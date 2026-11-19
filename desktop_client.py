"""Stable desktop executable entry point."""
import sys
from collector import client_bootstrap as _implementation
if __name__=='__main__':_implementation.entrypoint()
else:sys.modules[__name__]=_implementation
