"""Kill switch: a file on disk (`KILL`) plus, later, a dashboard button.

When engaged: no new orders, square off everything, refuse to start.
Checked at the top of every order path (SECURITY.md §3.4).
A file is deliberately low-tech — it works even when the app is wedged,
and a human can create it with any tool in seconds.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class KillSwitch:
    def __init__(self, path: Path | str):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def engaged(self) -> bool:
        return self._path.exists()

    def reason(self) -> str:
        if not self.engaged():
            return ""
        try:
            return self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return "(unreadable KILL file)"

    def engage(self, reason: str) -> None:
        self._path.write_text(
            f"{datetime.now(timezone.utc).isoformat()} {reason}\n", encoding="utf-8"
        )

    def disengage(self) -> None:
        # Deliberate manual step: only a human should ever remove the file.
        self._path.unlink(missing_ok=True)
