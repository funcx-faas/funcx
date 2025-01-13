from __future__ import annotations

import logging
import multiprocessing
import queue
import typing as t
import uuid
from concurrent.futures import Future
from concurrent.futures import ProcessPoolExecutor as NativeExecutor

import psutil
from globus_compute_common.messagepack.message_types import (
    EPStatusReport,
    TaskTransition,
)
from globus_compute_endpoint.engines.base import (
    GlobusComputeEngineBase,
    ReportingThread,
)
from globus_compute_sdk.serialize.facade import DeserializerAllowlist

logger = logging.getLogger(__name__)


class ProcessPoolEngine(GlobusComputeEngineBase):
    def __init__(
        self,
        *args,
        label: str = "ProcessPoolEngine",
        allowed_serializers: DeserializerAllowlist | None = None,
        **kwargs,
    ):
        self.label = label
        self.executor: t.Optional[NativeExecutor] = None
        self._executor_args = args
        self._executor_kwargs = kwargs
        self._status_report_thread = ReportingThread(target=self.report_status, args=[])
        super().__init__(
            *args,
            **kwargs,
            allowed_serializers=allowed_serializers,
        )

    def start(
        self,
        *args,
        endpoint_id: t.Optional[uuid.UUID] = None,
        run_dir: t.Optional[str] = None,
        results_passthrough: t.Optional[queue.Queue] = None,
        **kwargs,
    ) -> None:
        """
        Parameters
        ----------
        endpoint_id: Endpoint UUID
        run_dir: endpoint run directory
        results_passthrough: Queue to which packed results will be posted
        Returns
        -------
        """
        if self.executor is None:
            # We are instantiating the executor here, rather than in the constructor,
            # to ensure the executor starts within the daemon context. Doing so avoids
            # having the daemon close existing pipe file descriptors required for
            # inter-process communication.
            self.executor = NativeExecutor(
                *self._executor_args, **self._executor_kwargs
            )

        assert endpoint_id, "ProcessPoolExecutor requires kwarg:endpoint_id at start"
        self.endpoint_id = endpoint_id
        if results_passthrough:
            self.results_passthrough = results_passthrough
        assert self.results_passthrough
        self.set_working_dir(run_dir=run_dir)

        self._status_report_thread.start()
        self._engine_ready = True

    def get_status_report(self) -> EPStatusReport:
        assert self.executor, "The engine has not been started"
        executor_status: t.Dict[str, t.Any] = {
            "task_id": -2,
            "info": {
                "total_cores": multiprocessing.cpu_count(),
                "total_mem": round(psutil.virtual_memory().available / (2**30), 1),
                "total_core_hrs": 0,
                "total_workers": self.executor._max_workers,  # type: ignore
                "pending_tasks": 0,
                "outstanding_tasks": 0,
                "scaling_enabled": False,
                "max_blocks": 1,
                "min_blocks": 1,
                "max_workers_per_node": self.executor._max_workers,  # type: ignore
                "nodes_per_block": 1,
                "heartbeat_period": None,
            },
        }
        task_status_deltas: t.Dict[str, t.List[TaskTransition]] = {}

        return EPStatusReport(
            endpoint_id=self.endpoint_id,
            global_state=executor_status,
            task_statuses=task_status_deltas,
        )

    def _submit(
        self,
        func: t.Callable,
        resource_specification: t.Dict,
        *args: t.Any,
        **kwargs: t.Any,
    ) -> Future:
        """We pass all params except the function to executor.submit()"""
        assert self.executor, "The engine has not been started"
        return self.executor.submit(func, *args, **kwargs)

    def status_polling_interval(self) -> int:
        return 30

    def shutdown(self, /, block=False, **kwargs) -> None:
        self._status_report_thread.stop()
        if self.executor:
            self.executor.shutdown(wait=block)
