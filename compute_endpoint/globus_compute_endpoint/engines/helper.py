from __future__ import annotations

import logging
import os
import pathlib
import time
import uuid

from globus_compute_common import messagepack
from globus_compute_common.messagepack.message_types import Result, Task, TaskTransition
from globus_compute_common.tasks import ActorName, TaskState
from globus_compute_endpoint.exception_handling import (
    get_error_string,
    get_result_error_details,
)
from globus_compute_endpoint.exceptions import CouldNotExecuteUserTaskError
from globus_compute_sdk.errors import MaxResultSizeExceeded
from globus_compute_sdk.sdk.utils import get_env_details
from globus_compute_sdk.serialize import ComputeSerializer
from parsl.app.python import timeout

log = logging.getLogger(__name__)

_RESULT_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MiB


def execute_task(
    task_id: uuid.UUID,
    task_body: bytes,
    endpoint_id: uuid.UUID | None = None,
    *,
    run_dir: str | os.PathLike,
    result_size_limit: int = _RESULT_SIZE_LIMIT,
    run_in_sandbox: bool = False,
    serde: ComputeSerializer = ComputeSerializer(),
) -> bytes:
    """Execute task is designed to enable any executor to execute a Task payload
    and return a Result payload, where the payload follows the globus-compute protocols
    This method is placed here to make serialization easy for executor classes
    Parameters
    ----------
    task_id: uuid string
    task_body: packed message as bytes
    endpoint_id: uuid.UUID or None
    result_size_limit: result size in bytes
    run_dir: directory to run function in
    run_in_sandbox: if enabled run task under run_dir/<task_uuid>

    Returns
    -------
    messagepack packed Result
    """
    exec_start = TaskTransition(
        timestamp=time.time_ns(), state=TaskState.EXEC_START, actor=ActorName.WORKER
    )

    result_message: dict[
        str,
        uuid.UUID | str | tuple[str, str] | list[TaskTransition] | dict[str, str],
    ]

    task_id_str = str(task_id)
    os.environ.pop("GC_TASK_SANDBOX_DIR", None)
    os.environ["GC_TASK_UUID"] = task_id_str

    if result_size_limit < 128:
        raise ValueError(
            f"Invalid result limit; must be at least 128 bytes ({result_size_limit=})"
        )

    task_dir = pathlib.Path(run_dir)
    if not task_dir.is_absolute():
        raise ValueError(f"Absolute path required.  Received: {run_dir=}")

    task_dir = task_dir.resolve()  # strict=False (default); path may not exist yet
    if run_in_sandbox:
        task_dir = task_dir / task_id_str
        # Set sandbox dir so that apps can use it
        os.environ["GC_TASK_SANDBOX_DIR"] = str(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(task_dir)

    env_details = get_env_details()
    try:
        _task, task_buffer = _unpack_messagebody(task_body)
        log.debug("executing task task_id='%s'", task_id)
        result = _call_user_function(task_buffer, serde)

        res_len = len(result)
        if res_len > result_size_limit:
            raise MaxResultSizeExceeded(res_len, result_size_limit)

        log.debug("Execution completed without exception")
        result_message = dict(task_id=task_id, data=result)

    except Exception:
        log.exception("Caught an exception while executing user function")
        code, user_message = get_result_error_details()
        error_details = {"code": code, "user_message": user_message}
        result_message = dict(
            task_id=task_id,
            data=get_error_string(),
            exception=get_error_string(),
            error_details=error_details,
        )

    env_details["endpoint_id"] = endpoint_id
    result_message["details"] = env_details

    exec_end = TaskTransition(
        timestamp=time.time_ns(),
        state=TaskState.EXEC_END,
        actor=ActorName.WORKER,
    )

    result_message["task_statuses"] = [exec_start, exec_end]

    log.debug(
        "task %s completed in %d ns",
        task_id,
        (exec_end.timestamp - exec_start.timestamp),
    )

    return messagepack.pack(Result(**result_message))


def _unpack_messagebody(message: bytes) -> tuple[Task, str]:
    """Unpack messagebody as a messagepack message with
    some legacy handling
    Parameters
    ----------
    message: messagepack'ed message body
    Returns
    -------
    tuple(task, task_buffer)
    """
    task = messagepack.unpack(message)
    if not isinstance(task, messagepack.message_types.Task):
        raise CouldNotExecuteUserTaskError(
            f"wrong type of message in worker: {type(task)}"
        )
    return task, task.task_buffer


def _call_user_function(task_buffer: str, serde: ComputeSerializer) -> str:
    """Deserialize the buffer and execute the task.
    Parameters
    ----------
    task_buffer: serialized buffer of (fn, args, kwargs)
    serde: serializer for the buffers
    Returns
    -------
    Returns serialized result or throws exception.
    """
    GC_TASK_TIMEOUT = max(0.0, float(os.environ.get("GC_TASK_TIMEOUT", 0.0)))
    f, args, kwargs = serde.unpack_and_deserialize(task_buffer)
    if GC_TASK_TIMEOUT > 0.0:
        log.debug(f"Setting task timeout to GC_TASK_TIMEOUT={GC_TASK_TIMEOUT}s")
        f = timeout(f, GC_TASK_TIMEOUT)

    return serde.serialize(f(*args, **kwargs))
