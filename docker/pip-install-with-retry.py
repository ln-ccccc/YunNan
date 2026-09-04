#!/usr/bin/env python3
"""Run one network-backed pip install with bounded outer retries."""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pip_network_retry import run_network_pip


def install_with_retry(
    pip_args,
    *,
    attempts=3,
    runner=subprocess.run,
):
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--retries",
        "12",
        "--timeout",
        "180",
        *pip_args,
    ]
    return run_network_pip(
        command,
        attempts=attempts,
        runner=runner,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument("--attempts", type=int, default=3)
    args, pip_args = parser.parse_known_args(argv)
    if pip_args[:1] == ["--"]:
        pip_args = pip_args[1:]
    if not pip_args:
        parser.error("pip install arguments are required")
    try:
        install_with_retry(pip_args, attempts=args.attempts)
    except subprocess.CalledProcessError as error:
        return error.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
