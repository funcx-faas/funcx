from __future__ import annotations

import logging
import os
import queue
import threading
import time
import typing as t
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import Future

from globus_compute_common import messagepack
from globus_compute_common.messagepack.message_types import (
    EPStatusReport,
    Result,
    TaskTransition,
)
from globus_compute_common.tasks import ActorName, TaskState
from globus_compute_endpoint.engines.helper import execute_task
from globus_compute_endpoint.exception_handling import (
    get_error_string,
    get_result_error_details,
)
from globus_compute_sdk.serialize.facade import ComputeSerializer, DeserializerAllowlist
from parsl.utils import RepresentationMixin

logger = logging.getLogger(__name__)
_EXC_HISTORY_TMPL = "+" * 68 + "\nTraceback from attempt: {ndx}\n{exc}\n" + "-" * 68


class ReportingThread:
    def __init__(self, target: t.Callable, args: list, reporting_period: float = 30.0):
        """This class wraps threading.Thread to run a callable in a loop
        periodically until the user calls `stop`. A status attribute can
        report exceptions to the parent thread upon failure.
        Parameters
        ----------
        target: Target function to be invoked to get report and post to queue
        args: args to be passed to target fn
        kwargs: kwargs to be passed to target fn
        reporting_period: float, defaults to 30.0s
        """
        self.status: Future = Future()
        self._shutdown_event = threading.Event()
        self.reporting_period = reporting_period
        self._thread = threading.Thread(
            target=self.run_in_loop, args=(target, *args), name="GCReportingThread"
        )

    def start(self):
        logger.info("Start called")
        self._thread.start()

    def run_in_loop(self, target: t.Callable, *args) -> None:
        try:
            target(*args)
            while not self._shutdown_event.wait(self.reporting_period):
                target(*args)
            self.status.set_result(None)
        except Exception as e:
            self.status.set_exception(exception=e)

        logger.warning("ReportingThread exiting")

    def stop(self) -> None:
        self._shutdown_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=0.1)


class GlobusComputeEngineBase(ABC, RepresentationMixin):
    """Shared functionality and interfaces required by all GlobusCompute Engines.
    This is designed to plug-in executors following the concurrent.futures.Executor
    interface as execution backends to GlobusCompute
    """

    def __init__(
        self,
        *args: object,
        endpoint_id: uuid.UUID | None = None,
        max_retries_on_system_failure: int = 0,
        working_dir: str | os.PathLike = "tasks_working_dir",
        allowed_serializers: DeserializerAllowlist | None = None,
        **kwargs: object,
    ):
        """
        Parameters
        ----------

        endpoint_id: uuid | None
            ID of the endpoint that the engine serves as execution backend

        max_retries_on_system_failure: int
            Set the number of retries for functions that fail due to system
            failures such as node failure/loss. Since functions can fail
            after partial runs, consider additional cleanup logic before
            enabling this functionality. default=0

        working_dir: str | os.PathLike
            Directory within which functions should execute, defaults to
            (~/.globus_compute/<endpoint_name>/tasks_working_dir)
            If a relative path is supplied, the working dir is set relative
            to the endpoint.run_dir. If an absolute path is supplied, it is
            used as is. default="tasks_working_dir"

        kwargs
        """
        self._shutdown_event = threading.Event()
        self.endpoint_id = endpoint_id
        self.serde = ComputeSerializer(allowed_deserializer_types=allowed_serializers)
        self.max_retries_on_system_failure = max_retries_on_system_failure
        self._retry_table: dict[str, dict] = {}
        # remove these unused vars that we are adding to just keep
        # endpoint interchange happy
        self.container_type: str | None = None
        self.run_dir: str | None = None
        self.working_dir: str | os.PathLike = working_dir
        self.run_in_sandbox: bool = False
        # This attribute could be set by the subclasses in their
        # start method if another component insists on owning the queue.
        self.results_passthrough: queue.Queue[dict[str, bytes | str | None]] = (
            queue.Queue()
        )
        self._engine_ready: bool = False

    @abstractmethod
    def start(
        self,
        *args,
        **kwargs,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_status_report(self) -> EPStatusReport:
        raise NotImplementedError

    def set_working_dir(self, run_dir: str | None = None):
        if not os.path.isabs(self.working_dir):
            run_dir = os.path.abspath(run_dir or os.getcwd())
            self.working_dir = os.path.join(run_dir, self.working_dir)

    def report_status(self) -> None:
        status_report = self.get_status_report()
        packed: bytes = messagepack.pack(status_report)
        self.results_passthrough.put({"message": packed})

    def _handle_task_exception(
        self,
        task_id: str,
        execution_begin: TaskTransition,
        exception: BaseException,
    ) -> bytes:
        """Repackage task exception to messagepack'ed bytes
        Parameters
        ----------
        task_id: str
        execution_begin: TaskTransition
        exception: Exception object from the task failure

        Returns
        -------
        bytes
        """
        code, user_message = get_result_error_details(exception)
        error_details = {"code": code, "user_message": user_message}
        execution_end = TaskTransition(
            timestamp=time.time_ns(),
            actor=ActorName.INTERCHANGE,
            state=TaskState.EXEC_END,
        )
        exception_string = ""
        for index, prev_exc in enumerate(
            self._retry_table[task_id]["exception_history"]
        ):
            templated_history = _EXC_HISTORY_TMPL.format(
                ndx=index + 1, exc=get_error_string(exc=prev_exc)
            )
            exception_string += templated_history

        final = _EXC_HISTORY_TMPL.format(
            ndx="final attempt", exc=get_error_string(exc=exception)
        )
        exception_string += final

        result_message = dict(
            task_id=task_id,
            data=exception_string,
            exception=exception_string,
            error_details=error_details,
            task_statuses=[execution_begin, execution_end],  # only timings we have
        )
        return messagepack.pack(Result(**result_message))

    def _setup_future_done_callback(self, task_id: str, future: Future) -> None:
        """
        Set up the done() callback for the provided future.

        The done callback handles
        Callback to post result to the passthrough queue
        Parameters
        ----------
        future: Future for which the callback is triggerd
        """

        exec_beg = TaskTransition(  # Reminder: used by *closure*, below
            timestamp=time.time_ns(),
            actor=ActorName.INTERCHANGE,
            state=TaskState.WAITING_FOR_LAUNCH,
        )

        def _done_cb(f: Future):
            try:
                packed = f.result()
            except Exception as exception:
                packed = self._handle_task_exception(
                    task_id=task_id, execution_begin=exec_beg, exception=exception
                )

            if packed:
                # _handle_task_exception can return empty bytestring
                # when it retries task, indicating there's no task status update
                self.results_passthrough.put({"task_id": task_id, "message": packed})
                self._retry_table.pop(task_id, None)

        future.add_done_callback(_done_cb)

    @abstractmethod
    def _submit(
        self,
        func: t.Callable,
        resource_specification: dict,
        *args: t.Any,
        **kwargs: t.Any,
    ) -> Future:
        """Subclass should use the internal execution system to implement this"""
        raise NotImplementedError()

    def _ensure_ready(self):
        """Raises a RuntimeError if engine is not started"""
        if not self._engine_ready:
            raise RuntimeError("Engine not started and cannot execute tasks")

    def submit(
        self,
        task_id: str,
        packed_task: bytes,
        resource_specification: dict,
    ) -> Future:
        """GC Endpoints should submit tasks via this method so that tasks are
        tracked properly.
        Parameters
        ----------
        packed_task: messagepack bytes buffer
        resource_specification: Dict that specifies resource requirements
        Returns
        -------
        future
        """
        self._ensure_ready()

        if task_id not in self._retry_table:
            self._retry_table[task_id] = {
                "retry_count": 0,
                "packed_task": packed_task,
                "exception_history": [],
                "resource_specification": resource_specification,
            }
        try:
            future = self._submit(
                execute_task,
                resource_specification,
                task_id,
                packed_task,
                self.endpoint_id,
                run_dir=self.working_dir,
                run_in_sandbox=self.run_in_sandbox,
                serde=self.serde,
            )
        except Exception as e:
            future = Future()
            future.set_exception(e)
        self._setup_future_done_callback(task_id, future)
        return future

    @abstractmethod
    def shutdown(self, /, **kwargs) -> None:
        raise NotImplementedError()
