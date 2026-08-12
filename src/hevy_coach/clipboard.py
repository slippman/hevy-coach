"""Injectable macOS clipboard adapter."""

from __future__ import annotations

import subprocess


class Clipboard:
    def copy(self, text: str) -> None:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
