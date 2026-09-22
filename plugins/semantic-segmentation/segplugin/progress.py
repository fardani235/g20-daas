"""Progress and status reporting.

The sandbox keeps the tail of stderr for the run log and surfaces it when a run
fails, so every stage announces itself there. The same events are collected and
written into the run metadata on success, so a user can see what the plugin
did (inputs used, model, grid, timings, warnings) alongside the result.
"""

import sys
import time


class Progress:
    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self.stages = []
        self.warnings = []
        self._t0 = time.monotonic()

    def log(self, message: str):
        elapsed = time.monotonic() - self._t0
        print(f"[semantic-segmentation +{elapsed:6.1f}s] {message}", file=self.stream, flush=True)

    def warn(self, message: str):
        self.warnings.append(message)
        self.log(f"warning: {message}")

    def stage(self, name: str):
        return _Stage(self, name)

    def summary(self) -> dict:
        return {
            "stages": [{"name": s["name"], "seconds": round(s["seconds"], 2)} for s in self.stages],
            "warnings": list(self.warnings),
            "total_seconds": round(time.monotonic() - self._t0, 2),
        }


class _Stage:
    def __init__(self, progress: Progress, name: str):
        self.progress = progress
        self.name = name

    def __enter__(self):
        self.progress.log(f"{self.name}...")
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        seconds = time.monotonic() - self._t0
        self.progress.stages.append({"name": self.name, "seconds": seconds})
        status = "failed" if exc_type else "done"
        self.progress.log(f"{self.name} {status} ({seconds:.1f}s)")
        return False
