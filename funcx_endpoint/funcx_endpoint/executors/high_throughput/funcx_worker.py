from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

import dill
import zmq
from funcx_common import messagepack
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
from funcx_common.tasks import ActorName, TaskState
=======
from funcx_common.tasks import TaskState
>>>>>>> baa726ea (Use list of tuples with updated TaskState fields to capture EXEC_START and EXEC_END times)
=======
from funcx_common.tasks.constants import ActorName, TaskState
>>>>>>> 5bd67dfa (Include actor name as part of status update.)
=======
from funcx_common.tasks import ActorName, TaskState
>>>>>>> 4149165f (Fix import line)

from funcx.errors import MaxResultSizeExceeded
from funcx.serialize import FuncXSerializer
from funcx_endpoint.exception_handling import get_error_string, get_result_error_details
from funcx_endpoint.exceptions import CouldNotExecuteUserTaskError
from funcx_endpoint.executors.high_throughput.messages import Message
from funcx_endpoint.logging_config import setup_logging

log = logging.getLogger(__name__)

DEFAULT_RESULT_SIZE_LIMIT_MB = 10
DEFAULT_RESULT_SIZE_LIMIT_B = DEFAULT_RESULT_SIZE_LIMIT_MB * 1024 * 1024


class FuncXWorker:
    """The FuncX worker
    Parameters
    ----------

    worker_id : str
     Worker id string

    address : str
     Address at which the manager might be reached. This is usually 127.0.0.1

    port : int
     Port at which the manager can be reached

    result_size_limit : int
     Maximum result size allowed in Bytes
     Default = 10 MB

    Funcx worker will use the REP sockets to:
         task = recv ()
         result = execute(task)
         send(result)
    """

    def __init__(
        self,
        worker_id,
        address,
        port,
        worker_type="RAW",
        result_size_limit=DEFAULT_RESULT_SIZE_LIMIT_B,
    ):

        self.worker_id = worker_id
        self.address = address
        self.port = port
        self.worker_type = worker_type
        self.serializer = FuncXSerializer()
        self.serialize = self.serializer.serialize
        self.deserialize = self.serializer.deserialize
        self.result_size_limit = result_size_limit

        log.info(f"Initializing worker {worker_id}")
        log.info(f"Worker is of type: {worker_type}")

        self.context = zmq.Context()
        self.poller = zmq.Poller()
        self.identity = worker_id.encode()

        self.task_socket = self.context.socket(zmq.DEALER)
        self.task_socket.setsockopt(zmq.IDENTITY, self.identity)

        log.info(f"Trying to connect to : tcp://{self.address}:{self.port}")
        self.task_socket.connect(f"tcp://{self.address}:{self.port}")
        self.poller.register(self.task_socket, zmq.POLLIN)
        signal.signal(signal.SIGTERM, self.handler)

    def handler(self, signum, frame):
        log.error(f"Signal handler called with signal {signum}")
        sys.exit(1)

    def _send_registration_message(self):
        log.debug("Sending registration")
        payload = {"worker_id": self.worker_id, "worker_type": self.worker_type}
        self.task_socket.send_multipart([b"REGISTER", dill.dumps(payload)])

    def start(self):
        log.info("Starting worker")
        self._send_registration_message()

        while True:
            log.debug("Waiting for task")
            p_task_id, p_container_id, msg = self.task_socket.recv_multipart()
            task_id: str = dill.loads(p_task_id)
            container_id: str = dill.loads(p_container_id)
            log.debug(f"Received task with task_id='{task_id}' and msg='{msg}'")

            result = None
            if task_id == "KILL":
                log.info("[KILL] -- Worker KILL message received! ")
                # send a "worker die" message back to the manager
                self.task_socket.send_multipart([b"WRKR_DIE", b""])
                log.info(f"*** WORKER {self.worker_id} ABOUT TO DIE ***")
                # Kill the worker after accepting death in message to manager.
                sys.exit()
            else:
                result = self.execute_task(task_id, msg)
                result["container_id"] = container_id
                log.debug("Sending result")
                # send bytes over the socket back to the manager
                self.task_socket.send_multipart([b"TASK_RET", dill.dumps(result)])

        log.warning("Broke out of the loop... dying")

    def execute_task(self, task_id: str, task_body: bytes) -> dict:
        log.debug("executing task task_id='%s'", task_id)
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
        exec_start = (_now_ms(), TaskState.EXEC_START, ActorName.WORKER)
=======
        exec_times = {"exec_start_ms": _now_ms()}
>>>>>>> cbd5779a (Black)
=======
        exec_times = {"exec_start_ms": _now_ms()}
>>>>>>> d375e5fc (Black)
=======
        exec_start = (_now_ms(), TaskState.EXEC_START)
>>>>>>> baa726ea (Use list of tuples with updated TaskState fields to capture EXEC_START and EXEC_END times)
=======
        exec_start = (_now_ms(), TaskState.EXEC_START, ActorName.WORKER)
>>>>>>> ac98dda4 (Fix missing actor name in exec start timestamp)
=======
        exec_start = (time.time_ns(), TaskState.EXEC_START, ActorName.WORKER)
>>>>>>> 5b5e6865 (Switch to time_ns())

        try:
            result = self.call_user_function(task_body)
        except Exception:
            log.exception("Caught an exception while executing user function")
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
            exec_end = (_now_ms(), TaskState.EXEC_END, ActorName.WORKER)
=======
            exec_times["exec_end_ms"] = _now_ms()
>>>>>>> cbd5779a (Black)
=======
            exec_times["exec_end_ms"] = _now_ms()
>>>>>>> d375e5fc (Black)
=======
            exec_end = (_now_ms(), TaskState.EXEC_END)
>>>>>>> baa726ea (Use list of tuples with updated TaskState fields to capture EXEC_START and EXEC_END times)
=======
            exec_end = (_now_ms(), TaskState.EXEC_END, ActorName.WORKER)
>>>>>>> 5bd67dfa (Include actor name as part of status update.)
=======
            exec_end = (time.time_ns(), TaskState.EXEC_END, ActorName.WORKER)
>>>>>>> 5b5e6865 (Switch to time_ns())
            result_message = dict(
                task_id=task_id,
                exception=get_error_string(),
                error_details=get_result_error_details(),
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
                statuses=[exec_start, exec_end],
            )
        else:
            log.debug("Execution completed without exception")
<<<<<<< HEAD
            exec_end = (_now_ms(), TaskState.EXEC_END, ActorName.WORKER)
<<<<<<< HEAD
            result_message = dict(
                task_id=task_id,
                data=result,
                statuses=[exec_start, exec_end],
=======
                times=exec_times,
            )
        else:
            log.debug("Execution completed without exception")
            exec_times["exec_end_ms"] = _now_ms()
=======
            exec_end = (time.time_ns(), TaskState.EXEC_END, ActorName.WORKER)
>>>>>>> 5b5e6865 (Switch to time_ns())
            result_message = dict(
                task_id=task_id,
                data=result,
                times=exec_times,
>>>>>>> cbd5779a (Black)
=======
                times=exec_times,
=======
                status=[exec_start, exec_end],
>>>>>>> baa726ea (Use list of tuples with updated TaskState fields to capture EXEC_START and EXEC_END times)
=======
                statuses=[exec_start, exec_end],
>>>>>>> facc8945 (Correct time difference and statuses)
            )
        else:
            log.debug("Execution completed without exception")
            exec_end = (_now_ms(), TaskState.EXEC_END)
=======
>>>>>>> 5bd67dfa (Include actor name as part of status update.)
            result_message = dict(
                task_id=task_id,
                data=result,
<<<<<<< HEAD
<<<<<<< HEAD
                times=exec_times,
>>>>>>> d375e5fc (Black)
=======
                status=[exec_start, exec_end],
>>>>>>> baa726ea (Use list of tuples with updated TaskState fields to capture EXEC_START and EXEC_END times)
=======
                statuses=[exec_start, exec_end],
>>>>>>> facc8945 (Correct time difference and statuses)
            )

        log.debug(
            "task %s completed in %d ms",
            task_id,
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
            (exec_end[0] - exec_start[0]),
=======
            (exec_times["exec_end_ms"] - exec_times["exec_start_ms"]),
>>>>>>> cbd5779a (Black)
=======
            (exec_times["exec_end_ms"] - exec_times["exec_start_ms"]),
>>>>>>> d375e5fc (Black)
=======
            (exec_end - exec_start),
>>>>>>> baa726ea (Use list of tuples with updated TaskState fields to capture EXEC_START and EXEC_END times)
=======
            (exec_end[0] - exec_start[1]),
>>>>>>> facc8945 (Correct time difference and statuses)
=======
            (exec_end[0] - exec_start[0]),
>>>>>>> d504dffe (typo)
        )
        return result_message

    def call_user_function(self, message: bytes) -> str:
        """Deserialize the buffer and execute the task.

        Returns the result or throws exception.
        """
        # try to unpack it as a messagepack message
        try:
            task = messagepack.unpack(message)
            if not isinstance(task, messagepack.message_types.Task):
                raise CouldNotExecuteUserTaskError(
                    f"wrong type of message in worker: {type(task)}"
                )
            task_data = task.task_buffer
        # on parse errors, failover to trying the "legacy" message reading
        except (
            messagepack.InvalidMessageError,
            messagepack.UnrecognizedProtocolVersion,
        ):
            task = Message.unpack(message)
            task_data = task.task_buffer.decode("utf-8")  # type: ignore[attr-defined]

        f, args, kwargs = self.serializer.unpack_and_deserialize(task_data)
        result_data = f(*args, **kwargs)
        serialized_data = self.serialize(result_data)

        if len(serialized_data) > self.result_size_limit:
            raise MaxResultSizeExceeded(len(serialized_data), self.result_size_limit)

        return serialized_data


def cli_run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-w", "--worker_id", required=True, help="ID of worker from process_worker_pool"
    )
    parser.add_argument(
        "-t", "--type", required=False, help="Container type of worker", default="RAW"
    )
    parser.add_argument(
        "-a", "--address", required=True, help="Address for the manager, eg X,Y,"
    )
    parser.add_argument(
        "-p",
        "--port",
        required=True,
        help="Internal port at which the worker connects to the manager",
    )
    parser.add_argument(
        "--logdir", required=True, help="Directory path where worker log files written"
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Directory path where worker log files written",
    )
    args = parser.parse_args()

    setup_logging(
        logfile=os.path.join(args.logdir, f"funcx_worker_{args.worker_id}.log"),
        debug=args.debug,
    )

    # Redirect the stdout and stderr
    stdout_path = os.path.join(args.logdir, f"funcx_worker_{args.worker_id}.stdout")
    stderr_path = os.path.join(args.logdir, f"funcx_worker_{args.worker_id}.stderr")
    with open(stdout_path, "w") as fo, open(stderr_path, "w") as fe:
        # Redirect the stdout
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = fo
        sys.stderr = fe

        try:
            worker = FuncXWorker(
                args.worker_id,
                args.address,
                int(args.port),
                worker_type=args.type,
            )
            worker.start()
        finally:
            # Switch them back
            sys.stdout = old_stdout
            sys.stderr = old_stderr


if __name__ == "__main__":
    cli_run()
