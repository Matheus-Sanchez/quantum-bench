from __future__ import annotations

import json
import math
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_token(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", text).strip("-").lower()


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if q <= 0:
        return min(values)
    if q >= 100:
        return max(values)
    ordered = sorted(values)
    index = (len(ordered) - 1) * (q / 100.0)
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def iqr(values: list[float]) -> float | None:
    q1 = percentile(values, 25)
    q3 = percentile(values, 75)
    if q1 is None or q3 is None:
        return None
    return q3 - q1


def safe_float(value: Any) -> float | None:
    if value in ("", None, "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    if value in ("", None, "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def is_wsl() -> bool:
    release = platform.release().lower()
    version = platform.version().lower()
    return "microsoft" in release or "microsoft" in version or "wsl" in version


def run_subprocess(command: list[str], timeout_s: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )


def package_version(package_name: str) -> str | None:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover
        import importlib_metadata as metadata  # type: ignore
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return None


def env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
