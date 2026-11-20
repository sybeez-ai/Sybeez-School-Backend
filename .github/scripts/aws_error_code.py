"""Print an AWS CLI error code from stdin. The rest of the message is dropped."""

from __future__ import annotations

import re
import sys

text = sys.stdin.read()
match = re.search(r"\(([A-Za-z0-9]+)\)", text)
print(match.group(1) if match else "unknown")
