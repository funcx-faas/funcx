from __future__ import annotations

import queue
import typing as t
import unittest.mock
import uuid

from globus_compute_common import messagepack
from globus_compute_common.messagepack.message_types import Result, Task
from globus_compute_sdk import Client
from parsl.executors.errors import InvalidResourceSpecification


class MockExecutor(unittest.mock.Mock):
    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)
        self.results_passthrough: queue.Queue | None = None
        self.passthrough = True

    def start(
        self,
        endpoint_id: uuid.UUID | None = None,
        run_dir: str | None = None,
        results_passthrough: queue.Queue = None,
        funcx_client: Client = None,
    ):
        self.results_passthrough = results_passthrough
        self.funcx_client = funcx_client
        self.endpoint_id = endpoint_id
        self.run_dir = run_dir

    def submit(
        self,
        task_id: uuid.UUID,
        packed_task: bytes,
        resource_specification: t.Dict,
    ):
        task: Task = messagepack.unpack(packed_task)
        res = Result(task_id=task_id, data=task.task_buffer)

        # This is a hack to trigger an InvalidResourceSpecification
        if "BAD_KEY" in resource_specification:
            raise InvalidResourceSpecification({"BAD_KEY"})

        packed_result = messagepack.pack(res)
        msg = {"task_id": str(task_id), "message": packed_result}
        self.results_passthrough.put(msg)
