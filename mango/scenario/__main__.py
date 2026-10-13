"""``python -m mango.scenario``: the ``mango-scenario`` command line tool."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
