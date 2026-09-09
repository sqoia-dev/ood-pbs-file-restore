#!/usr/bin/env python3
"""Validate a non-secret site configuration without printing its contents."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from site_config import ConfigError, load

parser = argparse.ArgumentParser()
parser.add_argument("config")
args = parser.parse_args()
try:
    load(args.config)
except ConfigError as error:
    print("invalid: %s" % error, file=sys.stderr)
    raise SystemExit(1)
print("valid site configuration")
