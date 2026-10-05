"""One local owner for a durable remote Evolution run.

A run ID is not a retry token for starting the whole search again. Even a fully
settled helper response does not prove that its surrounding archive/checkpoint
committed. Until whole-run replay is implemented, reopening an admitted run is
explicit recovery, not another initial evaluation or parent sample.
"""
import asyncio
import json
import sqlite3

from pantheon.apps.agent_execution_runner import ToolReceiptJournal
from pantheon.utils.owned_io import run_owned_io
from .lifetime import EvolutionCleanupError, join_cleanup


class EvolutionRunLease:
    def __init__(self, binding, config):
        self.binding, self.config = binding, config
        self.journal = None
        self._admitted = False
        self._closing = None

    async def acquire(self):
        def claim():
            self.journal = ToolReceiptJournal(self.binding.root / self.binding.run_id / '_run',
                                              self.binding.binding_id)
            with sqlite3.connect(self.journal.path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS evolution_run (id TEXT PRIMARY KEY, '
                           'config TEXT NOT NULL, phase TEXT NOT NULL)')
                if db.execute('SELECT 1 FROM evolution_run').fetchone():
                    raise RuntimeError('Reconcile the saved Evolution run; do not restart its initial evaluation')
                db.execute("INSERT INTO evolution_run VALUES (?,?,'admitted')", (
                    self.binding.run_id, json.dumps(self.config, allow_nan=False)))
                self._admitted = True
        try:
            await run_owned_io(claim)
        except Exception as exc:
            raise EvolutionCleanupError([exc]) from exc

    async def close(self, *, completed):
        """Called only AFTER all owned tools and helpers successfully shut down."""
        if self._closing is None:
            def release():
                if self.journal is None: return
                if self._admitted:
                    with sqlite3.connect(self.journal.path) as db:
                        saved = db.execute('UPDATE evolution_run SET phase=? WHERE id=?',
                            ('completed' if completed else 'stopped', self.binding.run_id))
                        if saved.rowcount != 1:
                            raise RuntimeError('Evolution run identity disappeared')
                self.journal.close()
            async def finish():
                try:
                    await run_owned_io(release)
                except Exception as exc:
                    raise EvolutionCleanupError([exc]) from exc
            self._closing = asyncio.create_task(finish())
        await join_cleanup(self._closing)
