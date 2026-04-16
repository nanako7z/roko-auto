"""Schedule type implementations: interval, cron, oneshot."""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from typing import Optional

from ..config.models import ScheduleConfig, ScheduleType


class ScheduleCalculator:
    """Calculates next run time based on schedule configuration."""

    def __init__(self, config: ScheduleConfig) -> None:
        self.config = config

    def next_delay(self, elapsed: float = 0.0) -> Optional[float]:
        """Calculate seconds until next run. Returns None if schedule is exhausted (oneshot done)."""
        if self.config.type == ScheduleType.interval:
            return self._interval_delay(elapsed)
        elif self.config.type == ScheduleType.cron:
            return self._cron_delay()
        elif self.config.type == ScheduleType.oneshot:
            return None  # Oneshot runs once then stops
        return None

    def initial_delay(self) -> float:
        return max(0.0, self.config.start_delay_sec)

    def _interval_delay(self, elapsed: float) -> float:
        interval = self.config.interval_sec or 20.0
        jitter = 0.0
        if self.config.jitter_sec > 0:
            jitter = random.uniform(-self.config.jitter_sec, self.config.jitter_sec)
        target = max(0.0, interval + jitter)
        return max(0.0, target - elapsed)

    def _cron_delay(self) -> float:
        """Calculate delay until next cron trigger.

        Supports simple cron patterns: */N for minutes.
        For full cron support, install croniter.
        """
        expr = self.config.cron_expression or "*/5 * * * *"

        try:
            from croniter import croniter
            cron = croniter(expr, datetime.now())
            next_time = cron.get_next(datetime)
            return max(0.0, (next_time - datetime.now()).total_seconds())
        except ImportError:
            pass

        # Fallback: parse simple */N minute patterns
        return self._simple_cron_delay(expr)

    @staticmethod
    def _simple_cron_delay(expr: str) -> float:
        """Minimal cron parser — supports '*/N * * * *' (every N minutes)."""
        parts = expr.strip().split()
        if len(parts) < 5:
            raise ValueError(f"Invalid cron expression: {expr!r}")

        minute_part = parts[0]
        match = re.match(r"\*/(\d+)", minute_part)
        if match:
            interval_minutes = int(match.group(1))
            now = datetime.now()
            current_minute = now.minute
            next_minute = ((current_minute // interval_minutes) + 1) * interval_minutes
            if next_minute >= 60:
                next_time = now.replace(minute=next_minute % 60, second=0, microsecond=0) + timedelta(hours=1)
            else:
                next_time = now.replace(minute=next_minute, second=0, microsecond=0)
            return max(0.0, (next_time - now).total_seconds())

        # Single minute value (e.g. "30 * * * *")
        if minute_part.isdigit():
            target_minute = int(minute_part)
            now = datetime.now()
            next_time = now.replace(minute=target_minute, second=0, microsecond=0)
            if next_time <= now:
                next_time += timedelta(hours=1)
            return max(0.0, (next_time - now).total_seconds())

        # Unsupported pattern — default to 5 minutes
        print(f"[WARN] Unsupported cron pattern '{expr}', defaulting to 5-minute interval")
        return 300.0
