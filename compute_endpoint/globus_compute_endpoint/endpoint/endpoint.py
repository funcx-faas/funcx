from __future__ import annotations

import json
import logging
import os
import pathlib
import pwd
import re
import shutil
import signal
import socket
import subprocess
import sys
import typing as t
from datetime import datetime
from http import HTTPStatus

import daemon
import daemon.pidfile
import psutil
import setproctitle
import texttable
import yaml
from globus_compute_endpoint import __version__
from globus_compute_endpoint.endpoint.config import BaseConfig, UserEndpointConfig
from globus_compute_endpoint.endpoint.config.utils import serialize_config
from globus_compute_endpoint.endpoint.interchange import EndpointInterchange
from globus_compute_endpoint.endpoint.result_store import ResultStore
from globus_compute_endpoint.endpoint.utils import _redact_url_creds, update_url_port
from globus_compute_endpoint.logging_config import setup_logging
from globus_compute_sdk.sdk.client import Client
from globus_sdk import AuthAPIError, GlobusAPIError, NetworkError

log = logging.getLogger(__name__)


class Endpoint:
    """
    Endpoint is primarily responsible for configuring, launching and stopping
    the Endpoint.
    """

    def __init__(self, debug=False):
        """Initialize the Endpoint

        Parameters
        ----------
        debug: Bool
            Enable debug logging. Default: False
        """
        self.debug = debug

    @staticmethod
    def _config_file_path(endpoint_dir: pathlib.Path) -> pathlib.Path:
        return endpoint_dir / "config.yaml"

    @staticmethod
    def user_config_template_path(endpoint_dir: pathlib.Path) -> pathlib.Path:
        return endpoint_dir / "user_config_template.yaml.j2"

    @staticmethod
    def user_config_schema_path(endpoint_dir: pathlib.Path) -> pathlib.Path:
        return endpoint_dir / "user_config_schema.json"

    @staticmethod
    def _user_environment_path(endpoint_dir: pathlib.Path) -> pathlib.Path:
        return endpoint_dir / "user_environment.yaml"

    @staticmethod
    def _example_identity_mapping_configuration_path(
        endpoint_dir: pathlib.Path,
    ) -> pathlib.Path:
        return endpoint_dir / "example_identity_mapping_config.json"

    @staticmethod
    def update_config_file(
        original_path: pathlib.Path,
        target_path: pathlib.Path,
        multi_user: bool,
        high_assurance: bool,
        display_name: str | None,
        auth_policy: str | None,
        subscription_id: str | None,
    ):
        config_text = original_path.read_text()
        config_dict = yaml.safe_load(config_text)

        if display_name:
            config_dict["display_name"] = display_name

        if auth_policy:
            config_dict["authentication_policy"] = auth_policy

        if high_assurance:
            config_dict["high_assurance"] = high_assurance

        if multi_user:
            config_dict["multi_user"] = multi_user
            config_dict.pop("engine", None)
            config_dict["identity_mapping_config_path"] = str(
                Endpoint._example_identity_mapping_configuration_path(
                    target_path.parent
                )
            )

        if subscription_id:
            config_dict["subscription_id"] = subscription_id

        config_text = yaml.safe_dump(config_dict)
        target_path.write_text(config_text)

    @staticmethod
    def init_endpoint_dir(
        endpoint_dir: pathlib.Path,
        endpoint_config: pathlib.Path | None = None,
        multi_user=False,
        high_assurance=False,
        display_name: str | None = None,
        auth_policy: str | None = None,
        subscription_id: str | None = None,
    ):
        """Initialize a clean endpoint dir

        :param endpoint_dir: Path to the endpoint configuration dir
        :param endpoint_config: Path to a config file to be used instead of
            the Globus Compute default config file
        :param multi_user: Whether the endpoint is a multi-user endpoint
        :param display_name: A display name to use, if desired
        :param auth_policy: Globus authentication policy
        :param subscription_id: Subscription ID to associate endpoint with
        """
        log.debug(f"Creating endpoint dir {endpoint_dir}")
        user_umask = os.umask(0o0077)
        os.umask(0o0077 | (user_umask & 0o0400))  # honor only the UR bit for dirs
        try:
            # pathlib.Path does not handle unusual umasks (e.g., 0o0111) so well
            # in the parents=True case, so temporarily change it.  This is nominally
            # only an issue for totally new users (no .globus_compute/!), but that is
            # also precisely the interaction -- the first one -- that should go smoothly
            endpoint_dir.mkdir(parents=True, exist_ok=True)

            config_target_path = Endpoint._config_file_path(endpoint_dir)
            package_dir = pathlib.Path(__file__).resolve().parent

            if endpoint_config is None:
                endpoint_config = package_dir / "config/default_config.yaml"

            Endpoint.update_config_file(
                endpoint_config,
                config_target_path,
                multi_user,
                high_assurance,
                display_name,
                auth_policy,
                subscription_id,
            )

            if multi_user:
                # template must be readable by user-endpoint processes (see
                # endpoint_manager.py)
                owner_only = 0o0600
                world_readable = 0o0644 & ((0o0777 - user_umask) | 0o0444)
                world_executable = 0o0711 & ((0o0777 - user_umask) | 0o0111)
                endpoint_dir.chmod(world_executable)

                src_user_tmpl_path = package_dir / "config/user_config_template.yaml.j2"
                src_user_schem_path = package_dir / "config/user_config_schema.json"
                src_user_env_path = package_dir / "config/user_environment.yaml"
                src_example_idmap_path = (
                    package_dir / "config/example_identity_mapping_config.json"
                )
                dst_user_tmpl_path = Endpoint.user_config_template_path(endpoint_dir)
                dst_user_schem_path = Endpoint.user_config_schema_path(endpoint_dir)
                dst_user_env_path = Endpoint._user_environment_path(endpoint_dir)
                dst_idmap_conf_path = (
                    Endpoint._example_identity_mapping_configuration_path(endpoint_dir)
                )

                shutil.copy(src_user_tmpl_path, dst_user_tmpl_path)
                shutil.copy(src_user_schem_path, dst_user_schem_path)
                shutil.copy(src_user_env_path, dst_user_env_path)
                shutil.copy(src_example_idmap_path, dst_idmap_conf_path)

                dst_user_tmpl_path.chmod(world_readable)
                dst_user_schem_path.chmod(world_readable)
                dst_user_env_path.chmod(world_readable)
                dst_idmap_conf_path.chmod(owner_only)

        finally:
            os.umask(user_umask)

    @staticmethod
    def configure_endpoint(
        conf_dir: pathlib.Path,
        endpoint_config: str | None,
        multi_user: bool = False,
        high_assurance: bool = False,
        display_name: str | None = None,
        auth_policy: str | None = None,
        subscription_id: str | None = None,
    ):
        ep_name = conf_dir.name
        if conf_dir.exists():
            print(conf_dir)
            print(f"config dir <{ep_name}> already exists")
            raise Exception("ConfigExists")

        templ_conf_path = pathlib.Path(endpoint_config) if endpoint_config else None
        Endpoint.init_endpoint_dir(
            conf_dir,
            templ_conf_path,
            multi_user,
            high_assurance,
            display_name,
            auth_policy,
            subscription_id,
        )
        config_path = Endpoint._config_file_path(conf_dir)
        if multi_user:
            user_conf_tmpl_path = Endpoint.user_config_template_path(conf_dir)
            user_conf_schema_path = Endpoint.user_config_schema_path(conf_dir)
            user_env_path = Endpoint._user_environment_path(conf_dir)
            idmap_ex_conf_path = Endpoint._example_identity_mapping_configuration_path(
                conf_dir
            )

            print(f"Created multi-user profile for endpoint named <{ep_name}>")
            print(
                f"\n\tConfiguration file: {config_path}\n"
                f"\n\tExample identity mapping configuration: {idmap_ex_conf_path}\n"
                f"\n\tUser endpoint configuration template: {user_conf_tmpl_path}"
                f"\n\tUser endpoint configuration schema: {user_conf_schema_path}"
                f"\n\tUser endpoint environment variables: {user_env_path}"
                "\n\nUse the `start` subcommand to run it:\n"
                f"\n\t$ globus-compute-endpoint start {ep_name}"
            )

        else:
            print(f"Created profile for endpoint named <{ep_name}>")
            print(
                f"\n\tConfiguration file: {config_path}\n"
                "\nUse the `start` subcommand to run it:\n"
                f"\n\t$ globus-compute-endpoint start {ep_name}"
            )

    @staticmethod
    def validate_endpoint_name(path_name: str) -> None:
        # Validate the path by removing all relative path modifiers (i.e., ../),
        # taking the basename, ensuring it's not Unix hidden (initial dot [.]), and
        # has no whitespace.  The path name is used deep within Parsl, which uses
        # raw shell to perform some actions ... and is not robust about it at all.
        # The path of least resistence, then, is to restrict this EP name upfront.
        # Examples:
        #
        # Good: "nice_normal_name"
        # Good: "91239anothervalidname"
        # Bad: " has_a_space"
        # Bad: "has_a_space "
        # Bad: "has a_space"
        # Bad: "has/../relative_path_syntax"
        # Bad: ".has_an_initial_dot(.);_'hidden'_directories_disallowed"
        # Bad: "a" * 129  # too long
        # Bad: ""  # come on now
        # Bad: "/absolute_path"
        # Bad: "contains...\r\v\t\n...other_whitespace"
        # Bad: "we_\\_do_not_accept_escapes"
        # Bad: "we_don't_accept_single_qoutes"
        # Bad: 'we_do_not_accept_"double_qoutes"'
        err_msg = "Invalid endpoint name: "
        pathname_re = re.compile(r"[\\/\s'\"]")
        if not path_name:
            err_msg += "Received no endpoint name"
            raise ValueError(err_msg)

        if len(path_name) > 128:
            err_msg += f"must be less than 129 characters (length: {len(path_name)})"
            raise ValueError(err_msg)

        # let pathlib resolve to an absolute (no ../), then take basename
        validated = pathlib.Path(path_name).resolve().name
        if validated != path_name:
            err_msg += (
                "no '..' or '/' characters"
                f"\n  Requested:  {path_name}"
                f"\n  Reduced to: {validated} (*potentially* valid)"
            )
            raise ValueError(err_msg)

        validated = pathname_re.sub("", validated).lstrip(".")
        if validated != path_name:
            err_msg += (
                "no whitespace (spaces, newlines, tabs), slashes, or prefixed '.'"
                f"\n  Requested:  {path_name}"
                f"\n  Reduced to: {validated} (*potentially* valid)"
            )
            raise ValueError(err_msg)

        # verify it's still a valid pathname *after* our ministrations
        validated = pathlib.Path(validated).name

        # only valid if completely unchanged by pathlib and basename shenanigans
        if path_name != validated:
            err_msg += (
                "unknown reason"
                f"\n  Requested:  {path_name}"
                f"\n  Reduced to: {validated} (*potentially* valid)"
            )
            raise ValueError(err_msg)

    @staticmethod
    def get_endpoint_id(endpoint_dir: pathlib.Path) -> str | None:
        info_file_path = endpoint_dir / "endpoint.json"
        try:
            return json.loads(info_file_path.read_bytes())["endpoint_id"]
        except FileNotFoundError:
            pass
        return None

    @staticmethod
    def get_endpoint_dir_by_uuid(
        gc_conf_dir: pathlib.Path, uuid: str
    ) -> pathlib.Path | None:
        for ep_path in Endpoint._get_ep_dirs(gc_conf_dir):
            if uuid == Endpoint.get_endpoint_id(ep_path):
                return ep_path
        return None

    @staticmethod
    def get_funcx_client(config: BaseConfig | None) -> Client:
        if config:
            return Client(
                local_compute_services=config.local_compute_services,
                environment=config.environment,
            )
        else:
            return Client()

    def start_endpoint(
        self,
        endpoint_dir: pathlib.Path,
        endpoint_uuid,
        endpoint_config: UserEndpointConfig,
        log_to_console: bool,
        no_color: bool,
        reg_info: dict,
        ep_info: dict,
        die_with_parent: bool = False,
    ):
        # If we are running a full detached daemon then we will send the output to
        # log files, otherwise we can piggy back on our stdout
        if endpoint_config.detach_endpoint:
            stdout: t.TextIO = open(
                os.path.join(endpoint_dir, endpoint_config.stdout), "a+"
            )
            stderr: t.TextIO = open(
                os.path.join(endpoint_dir, endpoint_config.stderr), "a+"
            )
        else:
            stdout = sys.stdout
            stderr = sys.stderr

        ostream = None
        if sys.stdout.isatty():
            ostream = sys.stdout
        elif sys.stderr.isatty():
            ostream = sys.stderr

        # This is to ensure that at least 1 executor is defined
        if not endpoint_config.executors:
            msg = (
                "Endpoint configuration has no executors defined.  Endpoint will not"
                " start."
            )
            log.critical(msg)
            raise ValueError(msg)

        pid_check = Endpoint.check_pidfile(endpoint_dir)
        # if the pidfile exists, we should return early because we don't
        # want to attempt to create a new daemon when one is already
        # potentially running with the existing pidfile
        if pid_check["exists"]:
            if pid_check["active"]:
                endpoint_name = endpoint_dir.name
                if endpoint_config.display_name:
                    endpoint_name = endpoint_config.display_name
                active_msg = f"Endpoint '{endpoint_name}' is already active"
                log.info(active_msg)
                if ostream:
                    print(active_msg, file=ostream)
                sys.exit(-1)
            else:
                log.info(
                    "A prior Endpoint instance appears to have been terminated without "
                    "proper cleanup. Cleaning up now."
                )
                Endpoint.pidfile_cleanup(endpoint_dir)

        if endpoint_config.endpoint_setup:
            Endpoint._run_command("endpoint_setup", endpoint_config.endpoint_setup)

        try:
            result_store = ResultStore(endpoint_dir=endpoint_dir)
        except Exception as e:
            exc_type = type(e).__name__
            log.critical(f"Failed to initialize the result storage.  ({exc_type}) {e}")
            raise

        try:
            pid_file = endpoint_dir / "daemon.pid"
            context = daemon.DaemonContext(
                working_directory=endpoint_dir,
                umask=0o002,
                pidfile=daemon.pidfile.PIDLockFile(pid_file),
                stdout=stdout,
                stderr=stderr,
                detach_process=endpoint_config.detach_endpoint,
            )

        except Exception:
            log.exception(
                "Caught exception while trying to setup endpoint context dirs"
            )
            exit(-1)

        # place registration after everything else so that the endpoint will
        # only be registered if everything else has been set up successfully
        if not reg_info:
            endpoint_uuid = Endpoint.get_endpoint_id(endpoint_dir) or endpoint_uuid
            log.debug("Attempting registration; trying with eid: %s", endpoint_uuid)
            try:
                fx_client = Endpoint.get_funcx_client(endpoint_config)
                reg_info = fx_client.register_endpoint(
                    name=endpoint_dir.name,
                    endpoint_id=endpoint_uuid,
                    metadata=Endpoint.get_metadata(endpoint_config),
                    multi_user=False,
                    display_name=endpoint_config.display_name,
                    allowed_functions=endpoint_config.allowed_functions,
                    auth_policy=endpoint_config.authentication_policy,
                    subscription_id=endpoint_config.subscription_id,
                    high_assurance=endpoint_config.high_assurance,
                )

            except GlobusAPIError as e:
                blocked_msg = f"Endpoint registration blocked.  [{e.text}]"
                log.warning(blocked_msg)
                if ostream:
                    print(blocked_msg, file=ostream)
                if e.http_status in (
                    HTTPStatus.CONFLICT,
                    HTTPStatus.LOCKED,
                    HTTPStatus.NOT_FOUND,
                ):
                    raise SystemExit(os.EX_UNAVAILABLE) from e
                elif e.http_status in (
                    HTTPStatus.BAD_REQUEST,
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                ):
                    raise SystemExit(os.EX_DATAERR) from e
                raise

            except NetworkError as e:
                # the output of a NetworkError exception is huge and unhelpful, so
                # it seems better to just stringify it here and get a concise error
                log.exception(
                    f"Caught exception while attempting endpoint registration: {e}"
                )
                msg = (
                    "globus-compute-endpoint is unable to reach the Globus Compute "
                    "service due to a network error.\n"
                    "Please ensure the Globus Compute service address is reachable, "
                    "then attempt restarting the endpoint."
                )
                log.critical(msg)
                if ostream:
                    print(msg, file=ostream)
                exit(os.EX_TEMPFAIL)

            ret_ep_uuid = reg_info.get("endpoint_id")
            if endpoint_uuid and ret_ep_uuid != endpoint_uuid:
                log.error(
                    "Unexpected response from server: mismatched endpoint id."
                    f"\n  Expected: {endpoint_uuid}, received: {ret_ep_uuid}"
                )
                exit(os.EX_SOFTWARE)

        try:
            endpoint_uuid = reg_info["endpoint_id"]
        except KeyError:
            log.error("Invalid credential structure")
            exit(os.EX_DATAERR)

        try:
            tq_info, rq_info, hbq_info = (
                reg_info["task_queue_info"],
                reg_info["result_queue_info"],
                reg_info["heartbeat_queue_info"],
            )
        except KeyError:
            log.error("Invalid credential structure")
            exit(os.EX_DATAERR)

        if endpoint_config.amqp_port is not None:
            for q_info in tq_info, rq_info, hbq_info:
                q_info["connection_url"] = update_url_port(
                    q_info["connection_url"], endpoint_config.amqp_port
                )

        # sanitize passwords in logs
        log_reg_info = _redact_url_creds(repr(reg_info))
        log.debug(f"Registration information: {log_reg_info}")

        json_file = endpoint_dir / "endpoint.json"

        # `endpoint_id` key kept for backward compatibility when
        # globus-compute-endpoint list is called
        ep_save_uuid = {"endpoint_id": endpoint_uuid}
        json_file.write_text(json.dumps(ep_save_uuid))
        log.debug(f"Registration info written to {json_file}")

        ptitle = f"Globus Compute Endpoint ({endpoint_uuid}, {endpoint_dir.name})"
        if endpoint_config.environment:
            ptitle += f" - {endpoint_config.environment}"
        ptitle += f" [{setproctitle.getproctitle()}]"
        setproctitle.setproctitle(ptitle)

        parent_pid = 0
        if die_with_parent:
            parent_pid = os.getppid()

        log.debug("Launching endpoint daemon process")

        # NOTE
        # It's important that this log is emitted before we enter the daemon context
        # because daemonization closes down everything, a log message inside the
        # context won't write the currently configured loggers
        logfile = endpoint_dir / "endpoint.log"
        log.debug(
            "Reconfiguring logging for daemonization. logfile: %s , debug: %s",
            logfile,
            self.debug,
        )

        if ostream:
            msg = f"Starting endpoint; registered ID: {endpoint_uuid}"
            if log_to_console:
                # make more prominent against other drab gray text ...
                msg = f"\n    {msg}\n"
            print(msg, file=ostream)

        with context:
            # Per DaemonContext implementation, and that we _don't_ pass stdin,
            # fd 0 is already connected to devnull.  Unfortunately, there is an
            # as-yet unknown interaction on Polaris (ALCF) that needs this
            # connection setup *again*.  So, repeat what daemon context already
            # did, and dup2 stdin from devnull to ... devnull.  (!@#$%^&*)
            # On any other system, this should make no difference (same file!)
            with open(os.devnull) as nullf:
                if os.dup2(nullf.fileno(), 0) != 0:
                    msg = "Unable to close stdin; endpoint will not start."
                    log.critical(msg)
                    raise Exception(msg)

            setup_logging(
                logfile=logfile,
                debug=self.debug,
                console_enabled=log_to_console,
                no_color=no_color,
            )

            now_tz = datetime.now().astimezone()
            ep_info.update(
                posix_username=pwd.getpwuid(os.getuid()).pw_name,
                posix_uid=os.getuid(),
                posix_gid=os.getgid(),
                posix_groups=os.getgroups(),
                posix_pid=os.getpid(),
                posix_sid=os.getsid(os.getpid()),
                config_raw=endpoint_config.source_content,
                start_iso=now_tz.isoformat(),
                start_unix=now_tz.timestamp(),
            )

            Endpoint.daemon_launch(
                endpoint_uuid,
                endpoint_dir,
                endpoint_config,
                reg_info,
                result_store,
                parent_pid,
                ep_info,
            )

    @staticmethod
    def daemon_launch(
        endpoint_uuid,
        endpoint_dir,
        endpoint_config: UserEndpointConfig,
        reg_info,
        result_store: ResultStore,
        parent_pid: int,
        ep_info: dict,
    ):
        log.info(f"\n\n========== Endpoint begins: {endpoint_uuid}")

        interchange = EndpointInterchange(
            config=endpoint_config,
            reg_info=reg_info,
            endpoint_id=endpoint_uuid,
            endpoint_dir=endpoint_dir,
            result_store=result_store,
            logdir=endpoint_dir,
            parent_pid=parent_pid,
            ep_info=ep_info,
        )

        interchange.start()

        log.info(f"\n---------- Endpoint shutdown: {endpoint_uuid}\n")

    @staticmethod
    def stop_endpoint(
        endpoint_dir: pathlib.Path,
        endpoint_config: BaseConfig | None,
        remote: bool = False,
    ):
        pid_path = endpoint_dir / "daemon.pid"
        ep_name = endpoint_dir.name

        if remote is True:
            endpoint_id = Endpoint.get_endpoint_id(endpoint_dir)
            if not endpoint_id:
                raise ValueError(f"Endpoint <{ep_name}> could not be located")

            fx_client = Endpoint.get_funcx_client(endpoint_config)
            fx_client.stop_endpoint(endpoint_id)

        ep_status = Endpoint.check_pidfile(endpoint_dir)
        if ep_status["exists"] and not ep_status["active"]:
            Endpoint.pidfile_cleanup(endpoint_dir)
            return
        elif not ep_status["exists"]:
            log.info(f"Endpoint <{ep_name}> is not active.")
            return

        log.debug(f"{ep_name} has a daemon.pid file")

        if isinstance(endpoint_config, UserEndpointConfig):
            teardown = endpoint_config.endpoint_teardown
            if teardown:
                Endpoint._run_command("endpoint_teardown", teardown)

        pid = int(pid_path.read_text().strip())
        try:
            log.debug(f"Signaling process: {pid}")
            # For all the processes, including the deamon and its descendants,
            # send SIGTERM, wait for 10s, and then SIGKILL any still alive.
            grace_period_s = 10
            parent = psutil.Process(pid)
            processes = parent.children(recursive=True)
            processes.append(parent)
            for p in processes:
                p.send_signal(signal.SIGTERM)
            terminated, alive = psutil.wait_procs(processes, timeout=grace_period_s)
            for p in alive:
                try:
                    p.send_signal(signal.SIGKILL)
                except psutil.NoSuchProcess:
                    pass

            if pid_path.exists():
                log.warning(f"Endpoint <{ep_name}> did not gracefully shutdown")
                # Do cleanup anyway, TODO figure out where it should have done that
                pid_path.unlink(missing_ok=True)
            else:
                log.info(f"Endpoint <{ep_name}> is now stopped")
        except OSError:
            log.warning(f"Endpoint <{ep_name}> could not be terminated")
            log.warning(f"Attempting Endpoint <{ep_name}> cleanup")
            pid_path.unlink(missing_ok=True)
            exit(-1)

    @staticmethod
    def delete_endpoint(
        ep_dir: pathlib.Path | None,
        ep_config: BaseConfig | None = None,
        force: bool = False,
        ep_uuid: str | None = None,
    ):
        ep_name = None

        if ep_dir:
            ep_name = str(ep_dir)
        elif ep_uuid:
            ep_name = ep_uuid
        else:
            log.warning("Name or UUID is needed to delete an Endpoint")
            exit(-1)

        # If we have the UUID, do the online status check/deletion first
        if ep_uuid:
            try:
                gc_client = Endpoint.get_funcx_client(ep_config)
                status = gc_client.get_endpoint_status(ep_uuid)
                if status.get("status") == "online":
                    if not force:
                        log.warning(
                            f"Endpoint {ep_uuid} is currently running.  To "
                            "proceed with deletion, first stop the endpoint, "
                            "or ignore the current status with `delete --force`"
                        )
                        exit(-1)
                    if ep_dir:
                        # Stop endpoint to handle process cleanup
                        Endpoint.stop_endpoint(ep_dir, ep_config, remote=False)

                gc_client.delete_endpoint(ep_uuid)
                log.info(f"Endpoint {ep_uuid} has been deleted from the web service")
            except AuthAPIError:
                # Send to the exception handler
                raise
            except GlobusAPIError as e:
                # GlobusAPIError is more known, handled slightly differently
                log.warning(
                    f"Endpoint {ep_uuid} could not be deleted from the web service: "
                    f"[{e.text}]"
                )
                if ep_dir and not force:
                    log.critical("Exiting without deleting the local endpoint")
                    exit(os.EX_UNAVAILABLE)
            except NetworkError as e:
                # NetworkError is unexpected, user can probably retry
                log.warning(
                    f"Endpoint {ep_uuid} could not be deleted from the web service: "
                    f"  [{e}]"
                )
                if not force:
                    msg = (
                        "globus-compute-endpoint is unable to reach the Globus "
                        "Compute service due to a network error.\n"
                        "Please ensure the Globus Compute service address is "
                        "reachable, then attempt to delete the endpoint again."
                    )
                    print(msg)
                    log.critical(msg)
                    exit(os.EX_TEMPFAIL)

        if ep_dir and ep_dir.exists():
            # Delete endpoint directory
            shutil.rmtree(ep_dir)
            log.info(f"Endpoint {ep_name} has been deleted from {ep_dir}")

    @staticmethod
    def check_pidfile(endpoint_dir: pathlib.Path):
        """Helper function to identify possible dead endpoints

        Returns a record with 'exists' and 'active' fields indicating
        whether the pidfile exists, and whether the process is active if it does exist
        (The endpoint can only start correctly if there is no pidfile)

        Parameters
        ----------
        endpoint_dir : pathlib.Path
            Configuration directory of the endpoint
        """
        status = {"exists": False, "active": False}
        pid_path = endpoint_dir / "daemon.pid"
        if not pid_path.exists():
            return status

        status["exists"] = True

        try:
            pid = int(pid_path.read_text().strip())
            psutil.Process(pid)
            status["active"] = True
        except ValueError:
            # Invalid literal for int() parsing
            pass
        except psutil.NoSuchProcess:
            pass

        return status

    @staticmethod
    def pidfile_cleanup(endpoint_dir: pathlib.Path):
        (endpoint_dir / "daemon.pid").unlink(missing_ok=True)
        log.info(f"Endpoint <{endpoint_dir.name}> has been cleaned up.")

    @staticmethod
    def get_endpoints(funcx_conf_dir: pathlib.Path | str) -> dict[str, dict]:
        """
        Gets a dictionary that contains information about all locally
        known endpoints.

        "status" can be one of:
            ["Initialized", "Running", "Disconnected", "Stopped"]

        Example output:
        {
            "default": {
                 "status": "Running",
                 "id": "123abcde-a393-4456-8de5-123456789abc"
            },
            "my_test_ep": {
                 "status": "Disconnected",
                 "id": "xxxxxxxx-xxxx-1234-abcd-xxxxxxxxxxxx"
            }
        }
        """
        ep_statuses = {}

        for ep_path in Endpoint._get_ep_dirs(pathlib.Path(funcx_conf_dir)):
            ep_status = {
                "status": "Initialized",
                "id": Endpoint.get_endpoint_id(ep_path),
            }
            ep_statuses[ep_path.name] = ep_status
            if not ep_status["id"]:
                continue

            pid_check = Endpoint.check_pidfile(ep_path)
            if pid_check["active"]:
                ep_status["status"] = "Running"
            elif pid_check["exists"]:
                ep_status["status"] = "Disconnected"
            else:
                ep_status["status"] = "Stopped"

        return ep_statuses

    @staticmethod
    def get_running_endpoints(conf_dir: pathlib.Path):
        return {
            ep_name: ep_status
            for ep_name, ep_status in Endpoint.get_endpoints(conf_dir).items()
            if ep_status["status"].lower() == "running"
        }

    @staticmethod
    def print_endpoint_table(conf_dir: pathlib.Path, ofile=None):
        """
        Converts locally configured endpoint list to a text based table
        and prints the output.
          For example format, see the texttable module
        """
        if not ofile:
            ofile = sys.stdout

        endpoints = Endpoint.get_endpoints(conf_dir)
        if not endpoints:
            print(
                "No endpoints configured!\n\n "
                "(Hint: globus-compute-endpoint configure)",
                file=ofile,
            )
            return

        table = texttable.Texttable()
        headings = ["Endpoint ID", "Status", "Endpoint Name"]
        table.header(headings)

        idx_id = 0
        idx_st = 1
        idx_name = 2
        width_st = len(headings[idx_st])
        width_id = len(headings[idx_id])
        for ep_name, ep_info in endpoints.items():
            table.add_row([ep_info["id"], ep_info["status"], ep_name])
            width_id = max(width_id, len(str(ep_info["id"])))
            width_st = max(width_st, len(str(ep_info["status"])))

        tsize = shutil.get_terminal_size()
        max_width = max(68, tsize.columns)  # require at least a reasonable size ...
        table.set_max_width(max_width)  # ... but allow to expand to terminal width

        # trickery here, but don't want to subclass texttable -- a very stable
        # library -- at this time; ensure that the only "fungible" column is the
        # name.  Can redress ~when~ *if* this becomes a problem.  "Simple"
        table._compute_cols_width()
        if table._width[idx_id] < width_id:
            table._width[idx_name] -= width_id - table._width[idx_id]
            table._width[idx_id] = width_id
        if table._width[idx_st] < width_st:
            table._width[idx_name] -= width_st - table._width[idx_st]
            table._width[idx_st] = width_st

        # ensure no mistakes -- width of name (well, _any_) column can't be < 0
        table._width[idx_name] = max(10, table._width[idx_name])

        print(table.draw(), file=ofile)

    @staticmethod
    def get_metadata(config: UserEndpointConfig) -> dict:
        metadata: dict = {
            "endpoint_version": __version__,
            "hostname": socket.getfqdn(),
            # should be more accurate than `getpass.getuser()` in non-login situations
            "local_user": pwd.getpwuid(os.getuid()).pw_name,
            "endpoint_config": config.source_content,
        }

        try:
            metadata["config"] = serialize_config(config)
        except Exception as e:
            log.warning(
                f"Error when serializing config ({type(e).__name__}). Ignoring."
            )
            log.debug("Config serialization exception details", exc_info=e)

        return metadata

    @staticmethod
    def _run_command(name: str, command: str):
        log.info(f"running {name} command: {command}")
        completed_process = subprocess.run(command, shell=True, capture_output=True)

        log.info(f"{name} stdout: {str(completed_process.stdout)}")
        log.info(f"{name} stderr: {str(completed_process.stderr)}")

        returncode = completed_process.returncode
        if returncode:
            log.error(f"{name} failed with exit code {returncode}")
            exit(os.EX_CONFIG)

    @staticmethod
    def _get_ep_dirs(gc_conf_dir: pathlib.Path) -> t.Iterable[pathlib.Path]:
        return (p.parent for p in gc_conf_dir.glob("*/config.*"))
