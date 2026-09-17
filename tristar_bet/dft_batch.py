"""Bounded, reusable process workers for independent sample calculations.

Workers never touch widgets or the GUI's result cache. Keep BLAS single
threaded inside each worker to avoid multiplying CPU threads by pool size.
"""
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os


def _initialize_worker():
    from threadpoolctl import threadpool_limits
    global _worker_blas_limit
    _worker_blas_limit = threadpool_limits(limits=1, user_api="blas")


def _calculate(job):
    from .analysis import dft_pore_distribution
    sample, options = job
    return dft_pore_distribution(sample, **options)


class DftBatchCalculator:
    """Synchronous batch API; reuse workers across Apply All operations."""

    def __init__(self, max_workers=None):
        self.max_workers = max_workers or min(4, max(1, (os.cpu_count() or 1) - 1))
        self._pool = None
        self.last_error = None

    def calculate(self, jobs):
        jobs = list(jobs)
        if not jobs:
            return []
        self.last_error = None
        try:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(
                    max_workers=self.max_workers,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=_initialize_worker,
                )
            # Ordered results keep each calculation paired with its cache key.
            return list(self._pool.map(_calculate, jobs, chunksize=1))
        except Exception as exc:
            # Startup/pickling/worker failure must never leave wrong or partial
            # cache entries. The caller can compute missing results normally.
            self.last_error = str(exc)
            self.close()
            return [None] * len(jobs)

    def close(self):
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
