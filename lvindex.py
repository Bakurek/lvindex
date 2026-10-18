"""Run lvindex from a checkout without installing it: python -I <repo>\\lvindex.py [--project P] <command> ..."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from lvindex.cli import main  # noqa: E402

main()
