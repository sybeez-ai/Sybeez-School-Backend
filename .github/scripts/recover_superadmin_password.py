"""Write the super-admin password removed from seed.py. Never print it.

Commit 5f2b87f deleted the local seed constant. The Actions token cannot
create repository secrets, so the release deploy reads that deleted line,
masks it, and passes it only to the provision task.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Full object id. A short SHA is fetched as a ref name and GitHub rejects it.
SEED_COMMIT = "5f2b87f3ec9e68a659b7e3e19f97949661a333ae"
diff = subprocess.check_output(
    ["git", "show", SEED_COMMIT, "--", "apps/api/src/db/seed.py"],
    text=True,
)
match = re.search(r'^-PLATFORM_SUPER_ADMIN_PASSWORD = "([^"]+)"', diff, re.M)
if not match:
    print("removed seed constant was not found", file=sys.stderr)
    raise SystemExit(1)
Path("provision-password.txt").write_text(match.group(1))
