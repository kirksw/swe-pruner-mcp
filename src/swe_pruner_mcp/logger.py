"""JSON logger for SWE-Pruner MCP operations"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PrunerLogger:
    """Logs pruning operations to a JSON file for analysis"""

    def __init__(self, stats_path: str | None = None):
        """Initialize logger with stats file path"""
        self.enabled = True
        if stats_path is None:
            cache_dir = Path.home() / ".cache" / "swe-pruner"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.stats_path = str(cache_dir / "stats.json")
        else:
            self.stats_path = str(stats_path)
        try:
            self._ensure_stats_file()
        except OSError:
            self.enabled = False

    def _ensure_stats_file(self):
        """Ensure stats file exists with valid JSON array"""
        path = Path(self.stats_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            self._write_stats([])

    def _write_stats(self, stats: list[dict[str, Any]]):
        """Atomically write stats to file"""
        path = Path(self.stats_path)
        temp_path = path.with_suffix('.tmp')
        temp_path.write_text(json.dumps(stats, indent=2))
        temp_path.replace(path)

    def _read_stats(self) -> list[dict[str, Any]]:
        """Read current stats from file"""
        try:
            path = Path(self.stats_path)
            if path.exists():
                return json.loads(path.read_text())
        except (json.JSONDecodeError, IOError):
            pass
        return []

    def log_operation(
        self,
        operation: str,
        input_size: int,
        output_size: int,
        compression_ratio: float | None = None,
        status: str = "success",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Log a pruning operation to the stats file"""
        if not self.enabled:
            return

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "input_size": input_size,
            "output_size": output_size,
            "compression_ratio": compression_ratio,
            "status": status,
            "error": error,
            "metadata": metadata or {},
        }
        try:
            stats = self._read_stats()
            stats.append(entry)
            self._write_stats(stats)
        except OSError:
            self.enabled = False
