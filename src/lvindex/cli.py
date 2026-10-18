"""Command line entry point: `lvindex [--project P] <command> ...` (console script, python -m lvindex, lvindex.py)."""
import os
import sys


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--project" in argv:                      # before the configuration is loaded (cache.py loads it on import)
        i = argv.index("--project")
        if i + 1 >= len(argv):
            sys.exit("--project needs a folder or a vi-tools.toml")
        os.environ["VI_PROJECT"] = os.path.abspath(argv[i + 1])
        del argv[i:i + 2]
    from . import cache
    cache.main(argv)
