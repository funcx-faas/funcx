#!/usr/bin/env python
import logging
import multiprocessing
import os
import pickle
import platform
import queue
import signal
import sys
import threading
import time
from typing import Dict

import pika
from parsl.version import VERSION as PARSL_VERSION
from retry.api import retry_call

import funcx_endpoint.endpoint.utils.config
from funcx import __version__ as funcx_sdk_version
from funcx.sdk.client import FuncXClient
from funcx.serialize import FuncXSerializer
from funcx_endpoint import __version__ as funcx_endpoint_version
from funcx_endpoint.endpoint.rabbit_mq import ResultQueuePublisher, TaskQueueSubscriber
from funcx_endpoint.endpoint.register_endpoint import register_endpoint
from funcx_endpoint.executors.high_throughput.mac_safe_queue import mpQueue

log = logging.getLogger(__name__)

LOOP_SLOWDOWN = 0.0  # in seconds
HEARTBEAT_CODE = (2**32) - 1
PKL_HEARTBEAT_CODE = pickle.dumps(HEARTBEAT_CODE)


class ShutdownRequest(Exception):
    """Exception raised when any async component receives a ShutdownRequest"""

    def __init__(self):
        self.tstamp = time.time()

    def __repr__(self):
        return f"Shutdown request received at {self.tstamp}"


class ManagerLost(Exception):
    """Task lost due to worker loss. Worker is considered lost when multiple heartbeats
    have been missed.
    """

    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.tstamp = time.time()

    def __repr__(self):
        return f"Task failure due to loss of worker {self.worker_id}"


class EndpointInterchange:
    """Interchange is a task orchestrator for distributed systems.

    1. Asynchronously queue large volume of tasks (>100K)
    2. Allow for workers to join and leave the union
    3. Detect workers that have failed using heartbeats
    4. Service single and batch requests from workers
    5. Be aware of requests worker resource capacity,
       eg. schedule only jobs that fit into walltime.

    """

    def __init__(
        self,
        config: funcx_endpoint.endpoint.utils.config.Config,
        reg_info: pika.connection.Parameters = None,
        logdir=".",
        endpoint_id=None,
        endpoint_dir=".",
        endpoint_name="default",
        funcx_client_options=None,
        results_ack_handler=None,
    ):
        """
        Parameters
        ----------
        config : funcx.Config object
             Funcx config object that describes how compute should be provisioned

        reg_info : pika.connection.Parameters
             Connection parameters to connect to the service side RabbitMQ pipes
             Optional: If not supplied, the endpoint will use a retry loop to
             attempt registration periodically.

        logdir : str
             Parsl log directory paths. Logs and temp files go here. Default: '.'

        endpoint_id : str
             Identity string that identifies the endpoint to the broker

        endpoint_dir : str
             Endpoint directory path to store registration info in

        endpoint_name : str
             Name of endpoint

        funcx_client_options : Dict
             FuncXClient initialization options
        """
        self.logdir = logdir
        log.info(
            "Initializing EndpointInterchange process with Endpoint ID: {}".format(
                endpoint_id
            )
        )
        self.config = config
        log.info(f"Got config: {config}")

        self.endpoint_dir = endpoint_dir
        self.endpoint_name = endpoint_name

        if funcx_client_options is None:
            funcx_client_options = {}
        # off_process checker is only needed on client side
        funcx_client_options["use_offprocess_checker"] = False
        self.funcx_client = FuncXClient(**funcx_client_options)

        self.initial_registration_complete = False
        self.connection_params = None
        if reg_info:
            self.connection_params = reg_info
            self.initial_registration_complete = True

        self.heartbeat_period = self.config.heartbeat_period
        self.heartbeat_threshold = self.config.heartbeat_threshold
        # initalize the last heartbeat time to start the loop
        self.last_heartbeat = time.time()
        self.serializer = FuncXSerializer()

        self.pending_task_queue = multiprocessing.Queue()
        self.containers = {}
        self.total_pending_task_count = 0

        self._quiesce_event = threading.Event()
        self._quiesce_complete = threading.Event()
        self._kill_event = threading.Event()

        self.results_ack_handler = results_ack_handler

        self.endpoint_id = endpoint_id

        self.current_platform = {
            "parsl_v": PARSL_VERSION,
            "python_v": "{}.{}.{}".format(
                sys.version_info.major, sys.version_info.minor, sys.version_info.micro
            ),
            "os": platform.system(),
            "hname": platform.node(),
            "funcx_sdk_version": funcx_sdk_version,
            "funcx_endpoint_version": funcx_endpoint_version,
            "registration": self.endpoint_id,
            "dir": os.getcwd(),
        }

        log.info(f"Platform info: {self.current_platform}")
        try:
            self.load_config()
        except Exception:
            log.exception("Caught exception")
            raise

        self.tasks = set()
        self.task_status_deltas = {}

        self._test_start = False

    def load_config(self):
        """Load the config"""
        log.info("Loading endpoint local config")

        self.results_passthrough = mpQueue()
        self.executors: Dict[str, funcx_endpoint.executors.HighThroughputExecutor] = {}
        for executor in self.config.executors:
            log.info(f"Initializing executor: {executor.label}")
            executor.funcx_service_address = self.config.funcx_service_address
            if not executor.endpoint_id:
                executor.endpoint_id = self.endpoint_id
            else:
                if not executor.endpoint_id == self.endpoint_id:
                    raise Exception("InconsistentEndpointId")
            self.executors[executor.label] = executor
            if executor.run_dir is None:
                executor.run_dir = self.logdir

    def start_executors(self):
        log.info("Starting Executors")
        for executor in self.config.executors:
            if hasattr(executor, "passthrough") and executor.passthrough is True:
                executor.start(results_passthrough=self.results_passthrough)

    def register_endpoint(self):
        reg_info = register_endpoint(
            self.funcx_client, self.endpoint_id, self.endpoint_dir, self.endpoint_name
        )
        self.connection_params = reg_info

    def migrate_tasks_to_internal(
        self,
        connection_params: pika.connection.Parameters,
        endpoint_uuid: str,
        pending_task_queue: multiprocessing.Queue,
        quiesce_event: multiprocessing.Event,
    ) -> multiprocessing.Process:
        """Pull tasks from the incoming tasks 0mq pipe onto the internal
        pending task queue

        Parameters:
        -----------
        connection_params: pika.connection.Parameters
              Connection params to connect to the service side Tasks queue

        endpoint_uuid: endpoint_uuid str

        pending_task_queue: multiprocessing.Queue
              Internal queue to which tasks should be migrated

        quiesce_event : threading.Event
              Event to let the thread know when it is time to die.
        """
        log.info("[TASK_PULL_THREAD] Starting")

        try:
            log.info(
                f"[TASK_PULL_PROC Starting the TaskQueueSubscriber"
                f" as {endpoint_uuid}"
            )
            task_q_proc = TaskQueueSubscriber(
                pika_conn_params=connection_params,
                external_queue=pending_task_queue,
                kill_event=quiesce_event,
                endpoint_uuid=endpoint_uuid,
            )
            task_q_proc.start()
        except Exception:
            log.exception("[TASK_PULL_PROC] Unhandled exception in TaskQueueSubscriber")

        return task_q_proc

    def get_container(self, container_uuid):
        """Get the container image location if it is not known to the interchange"""
        if container_uuid not in self.containers:
            if container_uuid == "RAW" or not container_uuid:
                self.containers[container_uuid] = "RAW"
            else:
                try:
                    container = self.funcx_client.get_container(
                        container_uuid, self.config.container_type
                    )
                except Exception:
                    log.exception(
                        "[FETCH_CONTAINER] Unable to resolve container location"
                    )
                    self.containers[container_uuid] = "RAW"
                else:
                    log.info(f"[FETCH_CONTAINER] Got container info: {container}")
                    self.containers[container_uuid] = container.get("location", "RAW")
        return self.containers[container_uuid]

    def quiesce(self):
        """Temporarily stop everything on the interchange in order to reach a consistent
        state before attempting to start again. This must be called on the main thread
        """
        log.info("Interchange Quiesce in progress (stopping and joining all threads)")
        self._quiesce_event.set()

        log.info("Saving unacked results to disk")
        try:
            self.results_ack_handler.persist()
        except Exception:
            log.exception("Caught exception while saving unacked results")
            log.warning("Interchange will continue without saving unacked results")
        log.warning("Waiting for quiesce complete")
        self._quiesce_complete.wait()
        log.warning("Done")
        # this must be called last to ensure the next interchange run will occur
        self._quiesce_event.clear()
        self._quiesce_complete.clear()

    def stop(self):
        """Prepare the interchange for shutdown"""
        log.info("Shutting down EndpointInterchange")

        self.quiesce()

        # shutdown executors gracefully
        for label in self.executors:
            self.executors[label].shutdown()

        # kill_event must be set before quiesce_event because we need to guarantee that
        # once the quiesce is complete, the interchange will not try to start again
        self._kill_event.set()
        self._quiesce_event.set()

    def handle_sigterm(self, sig_num, curr_stack_frame):
        log.warning("Received SIGTERM, attempting to save unacked results to disk")
        try:
            self.stop()
        except Exception:
            log.exception("Caught exception while saving unacked results")
        else:
            log.info("Unacked results successfully saved to disk")
        # sys.exit(1)

    def start(self):
        """Start the Interchange"""
        log.info("Starting EndpointInterchange")

        signal.signal(signal.SIGTERM, self.handle_sigterm)

        self._quiesce_event.clear()
        self._kill_event.clear()

        # NOTE: currently we only start the executors once because
        # the current behavior is to keep them running decoupled while
        # the endpoint is waiting for reconnection
        self.start_executors()

        while not self._kill_event.is_set():
            self._start_threads_and_main()
            self.quiesce()
            # this check is solely for testing to force this loop to only run once
            if self._test_start:
                break

        log.info("EndpointInterchange shutdown complete.")

    def _start_threads_and_main(self):
        # re-register on every loop start
        if not self.initial_registration_complete:
            # Register the endpoint
            log.info("Running endpoint registration retry loop")
            reg_info = retry_call(
                self.register_endpoint, delay=10, max_delay=300, backoff=1.2
            )
            log.info(
                "Endpoint registered with UUID: {}".format(reg_info["endpoint_id"])
            )

        self.initial_registration_complete = False

        self._task_puller_proc = self.migrate_tasks_to_internal(
            self.connection_params,
            self.endpoint_id,
            self.pending_task_queue,
            self._quiesce_event,
        )

        try:
            self._main_loop()
        except Exception:
            log.exception("[MAIN] Unhandled exception")
        finally:
            self.results_outgoing.close()
            self._task_puller_proc.terminate()
            log.info("[MAIN] Thread loop exiting")
        self._quiesce_event.set()
        self._task_puller_proc.terminate()

    def _main_loop(self):

        self.results_outgoing = ResultQueuePublisher(
            endpoint_id=self.endpoint_id,
            pika_conn_params=self.connection_params,
        )

        self.results_outgoing.connect()

        # TODO: this resend must happen after any endpoint re-registration to
        # ensure there are not unacked results left
        resend_results_messages = self.results_ack_handler.get_unacked_results_list()
        if len(resend_results_messages) > 0:
            log.info(
                "[MAIN] Resending %s previously unacked results",
                len(resend_results_messages),
            )

        for results in resend_results_messages:
            # TO-DO: Republishing backlogged/unacked messages is not supported
            # until the types are sorted out
            self.results_outgoing.publish(results)

        executor = list(self.executors.values())[0]
        last = time.time()

        while not self._quiesce_event.is_set():
            log.warning("Boop")
            if last + self.heartbeat_threshold < time.time():
                log.debug("[MAIN] alive")
                last = time.time()

            self.results_ack_handler.check_ack_counts()

            try:
                task = self.pending_task_queue.get(block=True, timeout=0.01)
                log.warning(f"Submitting task : {task}")
                executor.submit_raw(task)
            except queue.Empty:
                pass
            except Exception:
                log.exception("[MAIN] Unhandled issue while waiting for pending tasks")

            try:
                results = self.results_passthrough.get(False, 0.01)

                task_id = results["task_id"]
                if task_id:
                    self.results_ack_handler.put(task_id, results["message"])
                    log.info(f"Passing result to forwarder for task {task_id}")

                # results will be a pickled dict with task_id, container_id,
                # and results/exception
                log.warning(f"Publishing message {results['message']}")
                self.results_outgoing.publish(results["message"])
                log.warning(f"quiesce_Event : {self._quiesce_event.is_set()}")
            except queue.Empty:
                pass

            except Exception:
                log.exception(
                    "[MAIN] Something broke while forwarding results from executor "
                    "to forwarder queues"
                )
                continue

        self._quiesce_complete.set()
