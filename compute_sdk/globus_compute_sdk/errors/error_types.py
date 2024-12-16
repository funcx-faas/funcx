from __future__ import annotations

import re
import textwrap
import time


class ComputeError(Exception):
    """Base class for all funcx exceptions"""

    def __str__(self):
        return self.__repr__()


class VersionMismatch(ComputeError):
    """Either client and endpoint version mismatch, or version cannot be retrieved."""

    def __init__(self, version_message):
        self.version_message = version_message

    def __repr__(self):
        return f"Globus Compute Versioning Issue: {self.version_message}"


class SerdeError(ComputeError):
    """Base class for SerializationError and DeserializationError"""

    def __init__(self, reason: str):
        self.reason = reason

    def __repr__(self):
        return self.reason


class SerializationError(SerdeError):
    """Something failed during serialization."""

    def __init__(self, reason):
        self.reason = reason

    def __repr__(self):
        return f"Serialization failed: {self.reason}"


class DeserializationError(SerdeError):
    """Something failed during deserialization."""

    def __init__(self, reason):
        self.reason = reason

    def __repr__(self):
        return f"Deserialization failed: {self.reason}"


class TaskPending(ComputeError):
    """Task is pending and no result is available yet"""

    def __init__(self, reason):
        self.reason = reason

    def __repr__(self):
        return f"Task is pending due to {self.reason}"


class MaxResultSizeExceeded(Exception):
    """Result produced by the function exceeds the maximum supported result size
    threshold"""

    def __init__(self, result_size: int, result_size_limit: int):
        self.result_size = result_size
        self.result_size_limit = result_size_limit

    def __str__(self) -> str:
        return (
            f"Task result of {self.result_size}B exceeded current "
            f"limit of {self.result_size_limit}B"
        )


SERDE_TASK_EXECUTION_FAILED_HELP_MESSAGE = """

This appears to be an error with serialization. If it is, using a different
serialization strategy from globus_compute_sdk.serialize might resolve the issue. For
example, to use globus_compute_sdk.serialize.CombinedCode:

    from globus_compute_sdk import Client, Executor
    from globus_compute_sdk.serialize import CombinedCode

    gcc = Client(code_serialization_strategy=CombinedCode())
    with Executor('<your-endpoint-id>', client=gcc) as gcx:
        # do something with gcx

For more information, see:
    https://globus-compute.readthedocs.io/en/latest/sdk.html#specifying-a-serialization-strategy
"""


class TaskExecutionFailed(Exception):
    """
    Error result from the remote end, wrapped as an exception object
    """

    SERDE_REGEX = re.compile("dill|pickle|serializ", re.IGNORECASE)

    def __init__(
        self,
        remote_data: str,
        completion_t: str | None = None,
        task_details: dict | None = None,
    ):
        self.remote_data = remote_data
        self.task_details = task_details
        # Fill in completion time if missing
        self.completion_t = completion_t or str(time.time())

    def __str__(self) -> str:
        remote_data = textwrap.indent(self.remote_data, " ")
        message = "\n" + remote_data
        if re.search(TaskExecutionFailed.SERDE_REGEX, remote_data):
            message += SERDE_TASK_EXECUTION_FAILED_HELP_MESSAGE
        return message
