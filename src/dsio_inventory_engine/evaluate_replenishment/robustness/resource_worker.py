"""Small installed entrypoint; only bounded references enter startup arguments."""

from pathlib import Path
import sys

from .resource_execution import worker_main


if __name__ == "__main__":
    root, raw_limit, name, expected_hash, deadline, owner = sys.argv[1:]
    worker_main(
        Path(root),
        int(raw_limit),
        {"name": name, "sha256": expected_hash},
        float(deadline),
        int(owner),
    )
