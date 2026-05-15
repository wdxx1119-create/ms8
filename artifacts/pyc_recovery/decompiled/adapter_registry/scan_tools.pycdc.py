# Source Generated with Decompyle++
# File: scan_tools.cpython-39.pyc (Python 3.9)

import json
import os
from pathlib import Path
from typing import Dict, List
from urllib.request import Request, urlopen

def _path_exists(raw_path = None):
    return Path(os.path.expanduser(raw_path)).exists()


def scan_mcp_tools(scan_paths = None):

# [stderr]
PycBuffer::getByte(): Unexpected end of stream
