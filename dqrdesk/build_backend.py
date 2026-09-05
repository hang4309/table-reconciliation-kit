"""Tiny PEP 517 backend so source installs need no build dependency."""

from __future__ import annotations

import base64
import csv
import hashlib
import os
import zipfile
from pathlib import Path


def _wheel_name() -> str:
    return "dqrdesk-1.0.0-py3-none-any.whl"


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    target = Path(wheel_directory) / _wheel_name()
    files: list[tuple[str, bytes]] = []
    for path in sorted(Path("dqrdesk").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files.append((path.as_posix(), path.read_bytes()))
    dist_info = "dqrdesk-1.0.0.dist-info"
    files.extend(
        [
            (f"{dist_info}/METADATA", b"Metadata-Version: 2.1\nName: dqrdesk\nVersion: 1.0.0\nRequires-Python: >=3.10\n"),
            (f"{dist_info}/WHEEL", b"Wheel-Version: 1.0\nGenerator: dqrdesk\nRoot-Is-Purelib: true\nTag: py3-none-any\n"),
            (f"{dist_info}/entry_points.txt", b"[console_scripts]\ndqrdesk = dqrdesk.cli:main\n"),
        ]
    )
    records: list[list[str]] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            records.append([name, f"sha256={digest}", str(len(data))])
        record_name = f"{dist_info}/RECORD"
        rows = [",".join(row) for row in records] + [f"{record_name},,"]
        info = zipfile.ZipInfo(record_name, (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, "\n".join(rows) + "\n")
    return target.name


def build_sdist(sdist_directory, config_settings=None):
    raise RuntimeError("sdist is not provided; use the self-contained release zip")


def get_requires_for_build_wheel(config_settings=None):
    return []

