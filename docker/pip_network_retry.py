#!/usr/bin/env python3
"""Retry pip network operations only after a verified transient failure."""

import subprocess
import sys
import tempfile
from pathlib import Path


_PERMANENT_MARKERS = (
    "resolutionimpossible",
    "no matching distribution found",
    "subprocess-exited-with-error",
)

_TRANSIENT_MARKERS = (
    "ssleoferror",
    "eof occurred in violation of protocol",
    "packages do not match the hashes",
    "expected sha256",
    "readtimeouterror",
    "timed out",
    "http 502",
    "502 server error",
    "bad gateway",
    "incomplete-download",
    "not enough bytes",
    "network connectivity",
    "remotedisconnected",
    "remote end closed",
    "protocolerror",
    "connection aborted",
)


def is_transient_pip_failure(log_text):
    text = log_text.lower()
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return True
    if any(marker in text for marker in _PERMANENT_MARKERS):
        return False
    return False


def _command_with_log(command, log_path):
    command = list(command)
    if command[:3] != [sys.executable, "-m", "pip"]:
        raise ValueError("command must start with the current Python pip module")
    return [*command[:3], "--log", str(log_path), *command[3:]]


def _command_without_cache(command):
    command = list(command)
    if command[:3] != [sys.executable, "-m", "pip"]:
        raise ValueError("command must start with the current Python pip module")
    if "--no-cache-dir" in command:
        return command
    return [*command[:4], "--no-cache-dir", *command[4:]]


def run_network_pip(
    command,
    *,
    attempts=3,
    runner=subprocess.run,
):
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    for attempt in range(1, attempts + 1):
        with tempfile.TemporaryDirectory(prefix="pip-network-") as temp_dir:
            log_path = Path(temp_dir) / "pip.log"
            attempt_command = (
                command if attempt == 1 else _command_without_cache(command)
            )
            logged_command = _command_with_log(attempt_command, log_path)
            try:
                return runner(logged_command, check=True)
            except subprocess.CalledProcessError:
                try:
                    log_text = log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    log_text = ""
                if not is_transient_pip_failure(log_text):
                    raise
                if attempt == attempts:
                    print(
                        f"PIP_NETWORK_RETRY_EXHAUSTED: {attempts} attempts",
                        file=sys.stderr,
                    )
                    raise
                print(
                    f"transient pip network failure; retrying without pip cache "
                    f"{attempt + 1}/{attempts}",
                    file=sys.stderr,
                )
