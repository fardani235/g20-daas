"""Progress, status and log reporting.

Three channels, all optional for the caller:

* **stderr** — every stage announces itself; the sandbox keeps the tail for the
  run log and shows it when a run fails.
* **progress file** — when the request carries a ``progress_path`` the plugin
  writes ``{"percent": N, "message": "..."}`` there (atomically) whenever the
  stage or percentage changes. The platform polls that file and shows it as
  the run's progress bar and status line while the job is still running.
* **summary** — stage timings and warnings, folded into the run metadata so a
  user can see afterwards what the plugin did and how long each step took.
"""

import json
import os
import sys
import time


class Progress:
    def __init__(self, stream=None, progress_path: str | None = None, tag: str = "3d-reconstruction"):
        self.stream = stream or sys.stderr
        self.progress_path = progress_path
        self.tag = tag
        self.stages = []
        self.warnings = []
        self.percent = 0
        self.message = ""
        self._t0 = time.monotonic()

    def log(self, message: str):
        elapsed = time.monotonic() - self._t0
        print(f"[{self.tag} +{elapsed:6.1f}s] {message}", file=self.stream, flush=True)

    def warn(self, message: str):
        self.warnings.append(message)
        self.log(f"warning: {message}")

    def report(self, percent: float, message: str | None = None):
        """Publish ``percent`` (0-100) and an optional status line."""
        percent = int(max(0, min(100, round(percent))))
        message = message if message is not None else self.message
        if percent == self.percent and message == self.message:
            return
        self.percent, self.message = percent, message
        if not self.progress_path:
            return
        tmp = f"{self.progress_path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"percent": percent, "message": message}, f)
            os.replace(tmp, self.progress_path)
        except OSError:
            # Progress is best-effort; never fail a run over it.
            pass

    def stage(self, name: str, percent: float | None = None):
        return _Stage(self, name, percent)

    def summary(self) -> dict:
        return {
            "stages": [{"name": s["name"], "seconds": round(s["seconds"], 2)} for s in self.stages],
            "warnings": list(self.warnings),
            "total_seconds": round(time.monotonic() - self._t0, 2),
        }


class _Stage:
    def __init__(self, progress: Progress, name: str, percent: float | None):
        self.progress = progress
        self.name = name
        self.percent = percent

    def __enter__(self):
        self.progress.log(f"{self.name}...")
        if self.percent is not None:
            self.progress.report(self.percent, self.name)
        else:
            self.progress.report(self.progress.percent, self.name)
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        seconds = time.monotonic() - self._t0
        peak = _peak_rss_mb()
        self.progress.stages.append({"name": self.name, "seconds": seconds, "peak_rss_mb": peak})
        status = "failed" if exc_type else "done"
        self.progress.log(f"{self.name} {status} ({seconds:.1f}s, peak {peak} MB)")
        return False


def _peak_rss_mb() -> int:
    """Peak resident memory so far; the sandbox caps address space, so this is worth watching."""
    try:
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
    except Exception:
        return 0
