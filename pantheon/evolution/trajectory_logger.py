"""Streaming JSONL logger for evolution trajectories (SFT/RL corpus builder)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .program import Program


class TrajectoryLogger:
    """Appends one JSON object per evolution iteration to a JSONL file.

    Each record captures the full (analyzer prompt, analyzer output, mutator
    prompt, diff, metrics, score) tuple needed to build merged-agent SFT pairs
    in the form `<think>{analyzer_output}</think>\\n{diff}` with the metric
    delta as reward signal.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = open(self._path, "a", encoding="utf-8")

    def log_iteration(
        self,
        iteration: int,
        parent: Program,
        child: Program,
        parent_score: float,
        child_score: float,
        improvement: float,
        accepted: bool,
        mutation_time: float,
        evaluation_time: float,
        llm_cost: float,
        task_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        record: Dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "timestamp": time.time(),
            "task_id": task_id,
            "iteration": iteration,
            "parent_id": parent.id,
            "child_id": child.id,
            "generation": child.generation,
            "analyzer_prompt": child.analysis_prompt_used,
            "analyzer_output": child.analysis_used,
            "analyzer_messages_raw": child.analyzer_messages_raw,
            "mutator_prompt": child.mutator_prompt_used,
            "mutator_response_raw": child.mutator_response_raw,
            "mutator_messages_raw": child.mutator_messages_raw,
            "diff": child.diff_from_parent or "",
            "mutation_summary": getattr(child, "mutation_summary", ""),
            "mutation_category": getattr(child, "mutation_category", ""),
            "is_algorithmic": getattr(child, "is_algorithmic", None),
            "metrics": child.metrics,
            "parent_score": parent_score,
            "child_score": child_score,
            "improvement": improvement,
            "accepted": bool(accepted),
            "error": getattr(child, "error", None),
            "mutation_time": mutation_time,
            "evaluation_time": evaluation_time,
            "llm_cost": llm_cost,
        }
        if extra:
            record["extra"] = extra

        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def log_failure(
        self,
        iteration: int,
        parent: Program,
        parent_score: float,
        error: str,
        mutation_time: float = 0.0,
        llm_cost: float = 0.0,
        phase: str = "",
        analyzer_prompt: str = "",
        analyzer_output: str = "",
        analyzer_messages_raw: Optional[list] = None,
        mutator_prompt: str = "",
        mutator_response_raw: str = "",
        mutator_messages_raw: Optional[list] = None,
        task_id: Optional[str] = None,
    ) -> None:
        """Log an iteration that errored out before a child program existed.

        Writes a record with the same schema as log_iteration but with child-
        specific fields left empty and `error`/`phase` populated. Use this from
        analyzer-timeout / analyzer-failed / mutation-timeout skip paths so
        negative-reward training data is preserved.
        """
        record: Dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "timestamp": time.time(),
            "task_id": task_id,
            "iteration": iteration,
            "parent_id": parent.id,
            "child_id": None,
            "generation": parent.generation + 1,
            "analyzer_prompt": analyzer_prompt,
            "analyzer_output": analyzer_output,
            "analyzer_messages_raw": analyzer_messages_raw or [],
            "mutator_prompt": mutator_prompt,
            "mutator_response_raw": mutator_response_raw,
            "mutator_messages_raw": mutator_messages_raw or [],
            "diff": "",
            "mutation_summary": "",
            "mutation_category": "",
            "is_algorithmic": None,
            "metrics": {},
            "parent_score": parent_score,
            "child_score": parent_score,
            "improvement": 0.0,
            "accepted": False,
            "error": error,
            "phase": phase,
            "mutation_time": mutation_time,
            "evaluation_time": 0.0,
            "llm_cost": llm_cost,
        }

        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
