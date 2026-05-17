"""
TaskScheduler: asyncio-based parallel runner for Workers.

Each Worker.run() is synchronous; we hand it to a *dedicated*
ThreadPoolExecutor (not asyncio's default executor) so that the scheduler
can return as soon as wait_for fires — without blocking on slow threads
that can't be killed. Failures/timeouts return a WorkerResult with
status≠"ok" but never bubble, so one Worker cannot block its peers.
"""
import asyncio
import concurrent.futures
import time
from typing import Optional

from .schemas import SubTask, WorkerResult, WorkerRole
from .worker import WorkerAgent


class TaskScheduler:
    """Parallel scheduler for WorkerAgent.run(subtask) calls."""

    def __init__(
        self,
        max_concurrency: int = 3,
        per_task_timeout_s: float = 60.0,
    ):
        self.max_concurrency = max_concurrency
        self.per_task_timeout_s = per_task_timeout_s

    async def run_all(
        self,
        assignments: dict[WorkerRole, tuple[WorkerAgent, SubTask]],
    ) -> list[WorkerResult]:
        """Dispatch (worker, subtask) pairs concurrently.

        Returns a list of WorkerResult in the same order as the assignments
        dict. Higher-priority subtasks acquire the semaphore first.
        """
        items = sorted(
            assignments.items(),
            key=lambda kv: kv[1][1].priority,
            reverse=True,
        )
        sem = asyncio.Semaphore(self.max_concurrency)
        loop = asyncio.get_running_loop()
        # Use a dedicated executor so timeouts return promptly. shutdown(wait=False)
        # in the finally clause lets asyncio.run() exit without waiting on a thread
        # that overran its timeout (the daemon=False thread will be cleaned up by
        # Python's atexit handler if the process is still alive at exit).
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_concurrency,
            thread_name_prefix="cr-worker",
        )
        try:
            tasks = [
                asyncio.create_task(
                    self._run_one(sem, loop, executor, worker, subtask)
                )
                for _role, (worker, subtask) in items
            ]
            return await asyncio.gather(*tasks)
        finally:
            executor.shutdown(wait=False)

    # ── Internals ─────────────────────────────────────────────────────────

    async def _run_one(
        self,
        sem: asyncio.Semaphore,
        loop: asyncio.AbstractEventLoop,
        executor: concurrent.futures.Executor,
        worker: WorkerAgent,
        subtask: SubTask,
    ) -> WorkerResult:
        async with sem:
            started = time.time()
            try:
                fut = loop.run_in_executor(executor, worker.run, subtask)
                return await asyncio.wait_for(fut, timeout=self.per_task_timeout_s)
            except asyncio.TimeoutError:
                return WorkerResult(
                    subtask_id=subtask.id,
                    role=worker.role,
                    status="timeout",
                    error=f"exceeded {self.per_task_timeout_s}s",
                    duration_ms=int((time.time() - started) * 1000),
                )
            except Exception as e:
                return WorkerResult(
                    subtask_id=subtask.id,
                    role=worker.role,
                    status="failed",
                    error=str(e),
                    duration_ms=int((time.time() - started) * 1000),
                )

    # Convenience: run from sync code.
    def run_all_sync(
        self,
        assignments: dict[WorkerRole, tuple[WorkerAgent, SubTask]],
    ) -> list[WorkerResult]:
        return asyncio.run(self.run_all(assignments))
