Changelog
=========

.. scriv-insert-here

.. _changelog-2.32.0:

globus-compute-sdk & globus-compute-endpoint v2.32.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added ipv6 support for GlobusComputeEngine by upgrading Parsl to 2024.11.25

Bug Fixes
^^^^^^^^^

- ``Client.logout()`` no longer raises an ``AttributeError``.

Changed
^^^^^^^

- Bumped dependency on ``globus-sdk-python`` to at least `version 3.47.0 <https://github.com/globus/globus-sdk-python/releases/tag/3.47.0>`_.
  This version includes changes to detect ``EOFErrors`` when logging in with the command
  line, obviating the need for existing ``globus-compute-sdk`` code that checks if a
  user is in an interactive terminal before logging in. The old Compute code raised a
  ``RuntimeError`` in that scenario; the new code raises a
  ``globus_sdk.login_flows.CommandLineLoginFlowEOFError`` if Python's ``input``
  function raises an ``EOFError`` - which, in Compute, can happen if a previously
  command-line-authenticated endpoint tries to re-authenticate but no longer has access
  to a STDIN.

- Bumped ``jsonschema`` version to at least 4.21, but relax the upper bound to
  version 5 so as to allow other projects to coexist in the same virtual
  environment more easily.

- Bumped ``parsl`` dependency version to `2024.11.25 <https://pypi.org/project/parsl/2024.11.25/>`_.

.. _changelog-2.31.0:

globus-compute-sdk & globus-compute-endpoint v2.31.0
------------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- ``GlobusComputeEngine``, ``ThreadPoolEngine``, and ``ProcessPoolEngine`` can
  now be configured with ``working_dir`` to specify the tasks working directory.
  If a relative path is specified, it is set in relation to the endpoint
  run directory (usually ``~/.globus_compute/<endpoint_name>``). Here's an example
  config file:

  .. code-block:: yaml

    engine:
      type: GlobusComputeEngine
      working_dir: /absolute/path/to/tasks_working_dir

- Function docstrings are now read and used as the description for the function when it
  is uploaded. This will support future UI changes to the webapp.

- The ``globus-compute-sdk`` and ``globus-compute-endpoint`` packages now support
  Python version 3.12.

- Added a new runtime check to ``globus_compute_endpoint.engines`` that will raise a `RuntimeError`
  if a task is submitted before ``engine.start()`` was called.

Bug Fixes
^^^^^^^^^

 - Fixed a bug where functions run with ``ThreadPoolEngine`` and ``ProcessPoolEngine``
   create and switch into the ``tasks_working_dir`` creating endless nesting.

Deprecated
^^^^^^^^^^

- Before this version, the ``function_name`` argument to ``Client.register_function``
  was not used, so it has now been deprecated. As before, function names are
  determined by the function's ``__name__`` and cannot be manually specified.

.. _changelog-2.30.1:

globus-compute-sdk & globus-compute-endpoint v2.30.1
----------------------------------------------------

Bug Fixes
^^^^^^^^^

- In cases where stdin is closed or not a TTY, we now only raise an error
  if the user requires an interactive login flow (i.e., does not have cached
  credentials).

.. _changelog-2.30.0:

globus-compute-sdk & globus-compute-endpoint v2.30.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Add runtime type-checking to |Batch| class; per user typo-induced question.

Bug Fixes
^^^^^^^^^

- Fixed a ``KeyError`` that occurred when using an ``AuthorizerLoginManager`` with
  a ``Client``, or when calling the ``AuthorizerLoginManager.get_auth_client()``
  method directly.

Changed
^^^^^^^

- Bumped ``globus-sdk`` dependency to at least 3.46.0.

- Bumped ``parsl`` dependency version to `2024.10.21 <https://pypi.org/project/parsl/2024.10.21/>`_.

- Drop support for Python 3.8, which entered the end-of-life phase on
  10-07-2024 (https://peps.python.org/pep-0569/).

.. _changelog-2.29.0:

globus-compute-sdk & globus-compute-endpoint v2.29.0
----------------------------------------------------

Bug Fixes
^^^^^^^^^

- Fix ``function_id`` in error message that previously referenced ``None``

Deprecated
^^^^^^^^^^

- ``globus-compute-endpoint``'s ``self-diagnostic`` sub-command has been
  deprecated and now just redirects to ``globus-compute-diagnostic``.

Changed
^^^^^^^

- The Globus Compute self-diagnostic is now available as a stand-alone console
  script installed as part of the globus-compute-sdk package, instead of only
  as the ``self-diagnostic`` sub-command of the globus-compute-endpoint CLI.

  For more information, see ``globus-compute-diagnostic --help``.

  Note that the new diagnostic command creates a gzipped file by default,
  whereas previously it printed the output to console by default and
  only created the compressed file if the -z argument is provided.

- The Executor now implements a bursty rate-limit in the background submission
  thread.  The Executor is designed to coalesce up to ``.batch_size`` of tasks
  and submit them in a single API call.  But if tasks are supplied to the
  Executor at just the right frequency, it will send much smaller batches more
  frequently which is "not nice" to the API.  This change allows "bursts" of up
  to 4 API calls in a 16s window, and then will back off to submit every 4
  seconds.  Notes:

  - ``.batch_size`` currently defaults to 128 but is user-settable

  - If the Executor is able to completely fill the batch of tasks sent to the
    API, that call is not counted toward the burst limit

- Prevent unintended hogging of resources (e.g., login nodes) by setting the default
  endpoint configuration (which uses |LocalProvider|_) to only use a single worker
  (``max_workers_per_node=1``).

.. _changelog-2.28.0:

globus-compute-sdk & globus-compute-endpoint v2.28.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- The multi-user endpoint now saves user-endpoint standard file streams (aka
  ``stdout`` and ``stderr``) to the UEP's ``endpoint.log``.  This makes it much
  easier to identity implementation missteps that affect the early UEP boot
  process, before the UEP's logging is bootstrapped.

- The SDK ``Client`` and ``WebClient`` now support using a ``GlobusApp`` for authentication.
  For standard interactive login flows, users can leave the ``app`` argument blank when
  initializing the ``Client``, or pass in a custom ``UserApp``. For client authentication,
  users can leave the ``app`` argument blank and set the ``GLOBUS_COMPUTE_CLIENT_ID`` and
  ``GLOBUS_COMPUTE_CLIENT_SECRET`` environment variables, or pass in a custom ``ClientApp``.

  For more information on how to use a ``GlobusApp``, see the `Globus SDK documentation
  <https://globus-sdk-python.readthedocs.io/en/stable/authorization/globus_app/apps.html>`_.

  Users can still pass in a custom ``LoginManager`` to the ``login_manager`` argument, but
  this is mutually exclusive with the ``app`` argument.

  E.g.,

  .. code-block:: python

     from globus_compute_sdk import Client, UserApp

     gcc = Client()

     # or

     my_app = UserApp("my-app", client_id="...")
     gcc = Client(app=my_app)

- Added a new data serialization strategy, ``JSONData``, which serializes/deserializes
  function args and kwargs via JSON. Usage example:

  .. code-block:: python

    from globus_compute_sdk import Client, Executor
    from globus_compute_sdk.serialize import JSONData

    gcc = Client(
        data_serialization_strategy=JSONData()
    )

    with Executor(<your endpoint UUID>, client=gcc) as gcx:
        # do something with gcx

Bug Fixes
^^^^^^^^^

- We no longer raise an exception if a user defines the ``GLOBUS_COMPUTE_CLIENT_ID``
  environment variable without defining ``GLOBUS_COMPUTE_SECRET_KEY``. The reverse,
  however, will still raise an exception.

Removed
^^^^^^^

- Removed ``http_timeout``, ``funcx_home``, and ``task_group_id`` arguments to
  :doc:`Client <reference/client>`, that were previously deprecated in
  :ref:`v2.3.0 <changelog-2.3.0>` (Aug 2023)

Deprecated
^^^^^^^^^^

- The ``WebClient.user_app_name`` attribute has been marked for deprecation and
  will be removed in a future release. Please directly use ``WebClient.app_name``
  instead.

Changed
^^^^^^^

- Bumped ``parsl`` dependency version to `2024.9.9 <https://pypi.org/project/parsl/2024.9.9/>`_.

.. _changelog-2.27.1:

globus-compute-sdk & globus-compute-endpoint v2.27.1
----------------------------------------------------

Bug Fixes
^^^^^^^^^

- Set upper bound for ``pyzmq`` dependency to ``v26.1.0`` to avoid bug with ``libzmq`` installation.

.. _changelog-2.27.0:

globus-compute-sdk & globus-compute-endpoint v2.27.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added ``Client.get_allowed_functions`` for retrieving the list of functions that are
  allowed to be executed on an endpoint.

Removed
^^^^^^^

- The ``add_to_whitelist``, ``delete_from_whitelist``, and ``get_whitelist`` functions
  have been removed from the ``Client``. Use the ``allowed_functions`` endpoint config
  option instead of the add/remove functions, and ``Client.get_allowed_functions``
  instead of ``get_whitelist``.

- Remove forgotten ``webockets`` dependency from setup requirements; the SDK
  does not use the websockets library as of :ref:`v2.3.0 <changelog-2.3.0>`.

Deprecated
^^^^^^^^^^

- The ``HighThroughputEngine`` is now marked for deprecation. All users should migrate to
  |GlobusComputeEngine|.

  To help with migration, we suggest checking out our many :doc:`endpoint configuration
  examples <endpoints/endpoint_examples>`, all of which use |GlobusComputeEngine|.

.. _changelog-2.26.0:

globus-compute-sdk & globus-compute-endpoint v2.26.0
----------------------------------------------------

Bug Fixes
^^^^^^^^^

- The endpoint CLI will now raise an error if the endpoint configuration includes
  both the ``container_uri`` field and a provider that manages containers internally
  (``AWSProvider``, ``GoogleCloudProvider``, or ``KubernetesProvider``). This prevents
  conflicts in container management.

Changed
^^^^^^^

- Bumped ``parsl`` dependency version to 2024.8.12.

.. _changelog-2.25.0:

globus-compute-sdk & globus-compute-endpoint v2.25.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added a new |ShellFunction| class to support remote execution of commandline strings.

  .. code:: python

      bf = ShellFunction("echo '{message}'")
      future = executor.submit(bf, message="Hello World!")
      shell_result = future.result()  # ShellFunction returns a ShellResult
      print(shell_result.returncode)  # Exitcode
      print(shell_result.cmd)         # Reports the commandline string executed
      print(shell_result.stdout)      # Snippet of stdout captured
      print(shell_result.stderr)      # Snippet of stderr captured

- Adding |GlobusMPIEngine| with better support for MPI applications.
  |GlobusMPIEngine| uses Parsl's |MPIExecutor|_ under the hood to dynamically partition
  a single batch job to schedule MPI tasks.

  Here's an example endpoint configuration that uses |GlobusMPIEngine|

  .. code-block:: yaml

    display_name: MPIEngine@Expanse.SDSC
    engine:
      type: GlobusMPIEngine
      mpi_launcher: srun

      provider:
         ...

- Added a new |MPIFunction| class to support MPI applications.
  |MPIFunction| extends |ShellFunction| to use an MPI launcher to use a
  subset of nodes within a batch job to run MPI applications. To partition a
  batch job, |MPIFunction| must be sent to an endpoint configured with
  |GlobusMPIEngine|.  Here is a usage example:

  .. code-block:: python

     from globus_compute_sdk import MPIFunction, Executor

     mpi_func = MPIFunction("hostname")
     with Executor(endpoint_id=<ENDPOINT_ID>) as ex:
          ex.resource_specification = {
              "num_nodes": 2,
              "ranks_per_node": 2
          }
          future = ex.submit(mpi_func)
          print(future.result().stdout)

     # Example output:
     node001
     node001
     node002
     node002

Bug Fixes
^^^^^^^^^

- Pulling tasks from RabbitMQ is now performed via a thread within the main
  endpoint process, rather than a separate process. This reduces the endpoint's
  overall memory footprint and fixes sporadic issues in which the formerly
  forked process would inherit thread locks.

Deprecated
^^^^^^^^^^

- ``globus-compute-sdk`` and ``globus-compute-endpoint`` drop support for
  Python3.7.  Python3.7 reached `end-of-life on 2023-06-27
  <https://devguide.python.org/versions/>`_. We discontinue support for
  Python3.7 since Parsl, an upstream core dependency, has also dropped support
  for it (in ``parsl==2024.7.1``).

.. _changelog-2.24.0:

globus-compute-sdk & globus-compute-endpoint v2.24.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- The engine that renders user endpoint config files now receives information about
  the runtime environment used to submit tasks, such as Python environment and Globus
  Compute SDK version, via the ``user_runtime`` variable. For a complete list of the
  fields that are sent, please reference the |UserRuntime| class documentation.

- Added the ``globus-compute-endpoint python-exec`` command to run Python modules as scripts
  from the Globus Compute endpoint CLI. The primary use case is to launch Parsl processes
  without requiring additional commands in the user's ``PATH`` (e.g., ``process_worker_pool.py``).

Changed
^^^^^^^

- Worker nodes no longer need to resolve the ``process_worker_pool.py`` command.

- Unless manually specified, all |Executor| objects in the same process will
  share the same task group ID.

.. _changelog-2.23.0:

globus-compute-sdk & globus-compute-endpoint v2.23.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- The ``delete`` command can now delete endpoints by name or UUID from the
  Compute service remotely when local config files are not available.  Note
  that without the ``--force`` option the command may exit early if the
  endpoint is currently running or local config files are corrupted.

- Included the paths to the ``globus-compute-endpoint`` and ``process_worker_pool.py``
  executables in the ``self-diagnostic`` command output.

Bug Fixes
^^^^^^^^^

- We no longer raise an exception when using the |GlobusComputeEngine| with Parsl
  providers that do not utilize ``Channel`` objects (e.g., ``KubernetesProvider``).

Changed
^^^^^^^

- Bumped ``parsl`` dependency version to 2024.6.10.

- ``GlobusComputeEngine.working_dir`` now defaults to ``tasks_working_dir``
   * When ``working_dir=relative_path``, tasks run in a path relative to the endpoint.run_dir.
     The default is ``tasks_working_dir`` set relative to endpoint.run_dir.
   * When ``working_dir=absolute_path``, tasks run in the specified absolute path

.. _changelog-2.22.0:

globus-compute-sdk & globus-compute-endpoint v2.22.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- |GlobusComputeEngine| now supports a ``working_dir`` keyword argument that sets the directory in which
  all functions will be executed. Relative paths, if set, will be considered relative to the endpoint directory
  (``~/.globus_compute/<endpoint_name>``). If this option is not set, |GlobusComputeEngine| will use the
  endpoint directory as the working directory. Set this option using ``working_dir: <working_dir_path>``
  Example config:

  .. code-block:: yaml

    display_name: WorkingDirExample
    engine:
      type: GlobusComputeEngine
      # Run functions in ~/.globus_compute/<EP_NAME>/TASKS
      working_dir: TASKS

- |GlobusComputeEngine| now supports function sandboxing, where each function is executed within a
  sandbox directory for better isolation. When this option is enabled by setting ``run_in_sandbox: True``
  a new directory with the function UUID as the name is created in the working directory (configurable with
  the ``working_dir`` kwarg). Example config:

  .. code-block:: yaml

    display_name: WorkingDirExample
    engine:
      type: GlobusComputeEngine
      # Set working dir to /projects/MY_PROJ
      working_dir: /projects/MY_PROJ
      # Enable sandboxing to have functions run under /projects/MY_PROJ/<function_uuid>/
      run_in_sandbox: True

- Implement ``debug`` as a top-level config boolean for a Compute Endpoint.  This flag
  determines whether debug-level logs are emitted |nbsp| --- |nbsp| the same
  functionality as the ``--debug`` command line argument to the
  ``globus-compute-endpoint`` executable.  Note: if this flag is set to
  ``False`` when the ``--debug`` CLI flag is specified, the CLI wins.

Bug Fixes
^^^^^^^^^

- Fixed bug where |GlobusComputeEngine| set the current working directory to the directory
  from which the endpoint was started. Now, |GlobusComputeEngine| will set the working directory
  to the endpoint directory (``~/.globus_compute/<endpoint_name>``) by default. This can be configured
  via the endpoint config.

Changed
^^^^^^^

- Updated the Compute hosted services to use AMQP over port 443 by default, instead of
  the standard 5671. This can still be overridden in both the SDK and the Endpoint via
  ``amqp_port``.

.. _changelog-2.21.0:

globus-compute-sdk & globus-compute-endpoint v2.21.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- MEPs now pass their configuration to UEP config templates via the ``parent_config``
  variable.  Please reference the :ref:`user configuration template
  <user-config-template-yaml-j2>` for more information.

- Added multi-user endpoint related files to the ``self-diagnostic`` command output.

Bug Fixes
^^^^^^^^^

- Teach MEP to shutdown on an (unrecoverable) AMQP authentication error, rather
  than attempting to reconnect multiple times.

Changed
^^^^^^^

- The default user configuration template filename will use a ``.j2`` file extension to
  clarify that we will treat the file as a Jinja template. Both ``user_config_template.yaml``
  and ``user_config_template.yaml.j2`` are now valid, but the latter will take precedence.

.. _changelog-2.20.0:

globus-compute-sdk & globus-compute-endpoint v2.20.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added ``enable-on-boot`` and ``disable-on-boot`` commands to the
  ``globus-compute-endpoint`` CLI, which contain packaged commands and configuration
  for managing systemd units for Compute endpoints.

Bug Fixes
^^^^^^^^^

- Addressed a hanging bug at endpoint shutdown.

- Make Executor shutdown idempotent -- if a user manually shut down the
  Executor within a ``with`` block, the Executor shutdown could hang if there
  were outstanding task futures.  Now the Executor recognizes that it has
  already been shutdown once, and the function returns early.

Changed
^^^^^^^

- Improve Executor shutdown performance by no longer attempting to join the
  task submitting thread.  This thread is already set to ``daemon=True`` and
  will correctly stop at Executor shutdown, so observe that ``.join()`` is
  strictly a waiting operation.  It is not a clue to the Python interpreter to
  clean up any resources.

.. _changelog-2.19.0:

globus-compute-sdk & globus-compute-endpoint v2.19.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Expanded support for ``pyzmq`` dependency to include versions up to ``26.x.x``.

Bug Fixes
^^^^^^^^^

- We now raise an informative error when a user sets the ``strategy`` configuration field
  to an incorrect value type for a given engine. For example, the |GlobusComputeEngine|
  expects ``strategy`` to be a string or null, not an object.

.. _changelog-2.18.1:

globus-compute-sdk & globus-compute-endpoint v2.18.1
----------------------------------------------------

Bug Fixes
^^^^^^^^^

- Fixed a bug that caused endpoints using the old ``HighThroughputExecutor`` to fail
  silently.

.. _changelog-2.18.0:

globus-compute-sdk & globus-compute-endpoint v2.18.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added ``GLOBUS_COMPUTE_CLIENT_ID`` and ``GLOBUS_COMPUTE_CLIENT_SECRET`` environment
  variables to configure client logins.

Bug Fixes
^^^^^^^^^

- Fixed a bug in |GlobusComputeEngine| where a faulty endpoint-config could result in
  the endpoint repeatedly submitting jobs to the batch scheduler.  The endpoint will
  not shut down, reporting the root cause in ``endpoint.log``

- Fixed bug where |GlobusComputeEngine| lost track of submitted jobs that failed to
  have workers connect back. The endpoint will now report a fault if multiple jobs
  have failed to connect back and shutdown, tasks submitted to the endpoint will
  return an exception.

Deprecated
^^^^^^^^^^

- ``FUNCX_SDK_CLIENT_ID`` and ``FUNCX_SDK_CLIENT_SECRET`` have been deprecated in favor
  of their ``GLOBUS_COMPUTE_*`` cousins.

Changed
^^^^^^^

- |GlobusComputeEngine|'s ``strategy`` kwarg now only accepts ``str``, valid options are
  ``{'none', 'simple'}`` where ``simple`` is the default.
- The maximum duration that workers are allowed to idle when using |GlobusComputeEngine|
  can now be configured with the new kwarg ``max_idletime`` which accepts a float and defaults
  to 120s.

.. _changelog-2.17.0:

globus-compute-sdk & globus-compute-endpoint v2.17.0
------------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Add support for Pydantic V2.

Bug Fixes
^^^^^^^^^

- Address a race-condition in detecting Endpoint stability.  Previously, the EP
  could keep resetting an internal fail counter, potentially allowing the EP to
  stay up indefinitely in a half-working state.  The EP logic now more
  faithfully detects an unrecoverable error and will shutdown rather than
  giving an appearance of being alive.

Changed
^^^^^^^

- Update AMQP reconnection handling; previously the reopen-connection logic was
  woefully optimistic of service or network downtime, assuming connectivity
  would be restored in ~a minute.  Reality is that a network can be down for
  hours and a service can take multiple minutes to update.  Consequently,
  update the number of retry attempts from 3 or 5 to 7,200.  (For context,
  reconnection attempts occur randomly between every 0.5s and 10s, so this
  means than an endpoint that has lost connectivity will attempt to reconnect
  to the web-services for somewhere between 1 and 20 hours.)  Hopefully, this
  is an adequate value to ensure that Compute endpoints weather most relevant
  connectivity outages.

- Bump ``globus-compute-common`` requirement to version ``0.4.1``.

.. _changelog-2.16.0:

globus-compute-sdk & globus-compute-endpoint v2.16.0
------------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added ``login`` command to ``globus-compute-endpoint`` CLI. This command triggers the
  existing login flow that is automatically triggered when starting an endpoint.

- Added the following arguments to ``globus-compute-endpoint configure``, which allow
  on-the-fly creation of Globus authentication policies while configuring Compute
  endpoints. See ``globus-compute-endpoint configure --help`` for more details.

  - ``--auth-policy-project-id``
  - ``--auth-policy-display-name``
  - ``--auth-policy-description``
  - ``--allowed-domains``
  - ``--excluded-domains``
  - ``--auth-timeout``

Changed
^^^^^^^

- Endpoint ``LoginManager`` s now request the ``AuthScopes.manage_projects`` scope, in
  order to create auth projects during the auth policy creation flow.

- The minimum version of ``globus-sdk`` that is compatible with ``globus-compute-sdk``
  and ``globus-compute-endpoint`` is now 3.35.0.

- Update Parsl from ``2024.3.4`` to ``2024.3.18``

.. _changelog-2.15.0:

globus-compute-sdk & globus-compute-endpoint v2.15.0
----------------------------------------------------

Bug Fixes
^^^^^^^^^

- Fixed a bug that caused errors on containerized endpoints when certain
  configuration fields (e.g., ``address_probe_timeout``) were not defined.

- Logs from ``parsl`` (providers, etc.) are now showing in ``endpoint.log``.

Changed
^^^^^^^

- Update ``globus-identity-mapping`` dependency to v0.3.0

- Update ``globus-sdk`` dependency to at least 3.28.0

- Bumped parsl pinned version from ``2024.02.05`` to ``2024.3.4``
  This version bump brings in following fixes:

  - HTEX to support ``max_workers_per_node`` as a keyword argument
  - Better stdout/err reporting from failed tasks
  - Support for detecting MISSING jobs
  - Better HTEX interchange shutdown logic to avoid hung processes

Security
^^^^^^^^

- Bump ``jinja2`` dependency to 3.1.3

.. _changelog-2.14.0:

globus-compute-sdk & globus-compute-endpoint v2.14.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added support for the new Globus subscription management service. An endpoint can be
  associated with a subscription group via the ``--subscription-id`` flag to
  ``globus-compute-endpoint configure``, or via the ``subscription_id`` option in
  ``config.yaml``:

  .. code-block:: yaml

    subscription_id: 12345678-9012-3456-7890-123456789012
    engine:
      type: GlobusComputeEngine
      ...

.. _changelog-2.13.0:

globus-compute-sdk & globus-compute-endpoint v2.13.0
------------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Upgraded Parsl to version ``2024.02.05`` to enable encryption for the |GlobusComputeEngine|.
  Under the hood, Parsl uses CurveZMQ to encrypt all communication channels between the engine
  and related nodes.

  We enable encryption by default, but users can disable it by setting the ``encrypted``
  configuration variable under the ``engine`` stanza to ``false``.

  E.g.,

  .. code-block:: yaml

    engine:
      type: GlobusComputeEngine
      encrypted: false

  Depending on the installation, encryption might noticeably degrade throughput performance.
  If this is an issue for your workflow, please refer to `Parsl's documentation on encryption
  performance <https://parsl.readthedocs.io/en/stable/userguide/execution.html#encryption-performance>`_
  before disabling encryption.

Bug Fixes
^^^^^^^^^

- Improved handling of unexpected errors in the ``HighThroughputEngine``.

- Fixed ``Skipping analyzing "globus_compute_sdk"`` error when running ``mypy`` on
  code dependent on ``globus_compute_sdk``

.. _changelog-2.12.0:

globus-compute-sdk & globus-compute-endpoint v2.12.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Implement ability to launch workers in containerized environments, with support for
  Docker, Singularity, and Apptainer.  Use by setting ``container_type``, ``container_uri``
  and  additional options may be specified via ``container_cmd_options``.
  Sample configuration:

  .. code-block:: yaml

    display_name: Docker
    engine:
      type: GlobusComputeEngine
      container_type: docker
      container_uri: funcx/kube-endpoint:main-3.10
      container_cmd_options: -v /tmp:/tmp

Removed
^^^^^^^

- Remove the funcx-* wrappers, per rebrand-to-Globus-Compute deprecation in
  Apr, 2024.

Changed
^^^^^^^

- Changed the default engine type for new endpoints to |GlobusComputeEngine|, which
  utilizes the Parsl |HighThroughputExecutor|_ under the hood.

- Pin Parsl version requirement to ``2024.01.22``.

.. _changelog-2.11.0:

globus-compute-sdk & globus-compute-endpoint v2.11.0
----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added ``Executor.get_worker_hardware_details`` helper function to retrieve
  information on the hardware an endpoint is running on

  - Added ``Client.get_worker_hardware_details`` for the same functionality on the
    Client

Changed
^^^^^^^

- Newly created endpoints now use 443 by default for communicating via AMQPS; this can
  be changed via the ``amqp_port`` config option.

.. _changelog-2.10.0:

globus-compute-sdk & globus-compute-endpoint v2.10.0
----------------------------------------------------

Bug Fixes
^^^^^^^^^

- Improved handling of communication issues related to receiving tasks
  from the Compute web services.

Changed
^^^^^^^

- Pin Parsl version requirement to ``2023.12.18``.

Development
^^^^^^^^^^^

-   Update the ``daily`` workflow.
    -   Add a timeout to the smoke test job.
    -   Use virtual environments to isolate dependencies that Safety is checking.
    -   Enforce a singular Python version across all configured jobs.

.. _changelog-2.9.0:

globus-compute-sdk & globus-compute-endpoint v2.9.0
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- |GlobusComputeEngine| can now be configured to automatically retry task failures when
  node failures (e.g nodes are lost due to batch job reaching walltime) occur. This option
  is set to 0 by default to avoid unintentional resource wastage from retrying tasks.
  Traceback history from all prior attempts is supplied if the last retry attempt fails.
  Here's a snippet from config.yaml:

.. code-block:: yaml

   engine:
      type: GlobusComputeEngine
      max_retries_on_system_failure: 2

Deprecated
^^^^^^^^^^

- The ``funcx_client`` argument to the ``Executor`` has been deprecated and replaced with ``client``.

Changed
^^^^^^^

- Parsl version requirements updated from ``2023.7.3`` to ``2023.12.4``

.. _changelog-2.7.0:

globus-compute-sdk & globus-compute-endpoint v2.7.0
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added a new ``AuthorizerLoginManager`` to create a login_manager from
  existing tokens.  This removes the need to implement a custom login manager
  to create a client from authorizers.

- The Executor can now be told which port to use to listen to AMQP results, via
  either the amqp_port keyword argument or the amqp_port property.

- Endpoints can be configured to talk to RMQ over a different port via the
  amqp_port configuration option.

- Added support for endpoint status reports when using |GlobusComputeEngine|.
  The report includes information such as the total number of active workers,
  idle workers, and pending tasks.

Bug Fixes
^^^^^^^^^

- The engine configuration variable ``label``, which defines the name of
  the engine log directory, now works with |GlobusComputeEngine|.

- The |GlobusComputeEngine| worker logs will appear in the ``~/.globus_compute/``
  directory rather than the current working directory.

.. _changelog-2.6.0:

globus-compute-sdk & globus-compute-endpoint v2.6.0
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Expand cases in which we return a meaningful exit code and message after endpoint
  registration failures when calling ``globus-compute-endpoint start``.

Bug Fixes
^^^^^^^^^

- The |GlobusComputeEngine|, ``ProcessPoolEngine``, and ``ThreadPoolEngine``
  now respect the ``heartbeat_period`` variable, as defined in ``config.yaml``.

- The |GlobusComputeEngine| has been updated to fully support the
  ``heartbeat_period`` parameter.

Changed
^^^^^^^

- Renamed the ``heartbeat_period_s`` attribute to ``heartbeat_period`` for
  |GlobusComputeEngine|, ``ProcessPoolEngine``, and ``ThreadPoolEngine``
  to maintain parity with the ``HighThroughputEngine`` and Parsl's
  |HighThroughputExecutor|_.

- Changed ``heartbeat_period`` type from float to int for |GlobusComputeEngine|,
  ``ProcessPoolEngine``, and ``ThreadPoolEngine`` to maintain parity with the
  ``HighThroughputEngine`` and Parsl's |HighThroughputExecutor|_.

.. _changelog-2.5.0:

globus-compute-sdk & globus-compute-endpoint v2.5.0
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Endpoint admins can now define a Globus authentication policy directly in an
  endpoint's configuration or by using the ``--auth-policy`` flag when running
  the ``globus-compute-endpoint configure`` command.

  Users are evaluated against the policy when submitting tasks, retrieving endpoint
  information, etc. For more information regarding Globus authentication policies,
  visit https://docs.globus.org/api/auth/developer-guide/#authentication-policies.
  Please note that we do not currently support HA policies.

Bug Fixes
^^^^^^^^^

- Defining ``worker_ports``, ``worker_port_range``, or ``interchange_port_range``
  in an endpoint's YAML config no longer raises an error.

Security
^^^^^^^^

- Add a Dependabot config to keep GitHub action versions updated.

.. _changelog-2.4.0:

globus-compute-sdk & globus-compute-endpoint v2.4.0
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added a ``Client.get_function`` method to submit a request for details about a registered
  function, such as name, description, serialized source code, python version, etc.

Bug Fixes
^^^^^^^^^

- Fix an innocuous bug during cleanup after having successfully shutdown an
  Endpoint using the |GlobusComputeEngine|.

- Configuration using |GlobusComputeEngine| now properly serializes and
  registers with the Globus Compute web services.

.. _changelog-2.3.3:

globus-compute-sdk & globus-compute-endpoint v2.3.3
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Teach the endpoint to include the Python and Dill versions, as metadata to Result objects, as well as other useful fields. If the task execution fails, the SDK will use the metadata to highlight differing versions as a possible cause.

- The SDK now supports defining metadata (Python and SDK versions) when registering
  a function. This information is automatically included when using the ``Executor``.

- Added web service version information to the output of the ``self-diagnostic`` endpoint command.

- A helpful message will be printed to the terminal in the event of an auth API error.

- Added steps to the `self-diagnostic` endpoint command that print the local system's
  OpenSSL version and attempt to establish SSL connections with the Globus Compute
  web services.

Bug Fixes
^^^^^^^^^

- Expired or unknown tasks queried using Client.get_batch_result() method will display the appropriate unknown response instead of producing a stack trace

Security
^^^^^^^^

- Require requests >= 2.31.

.. _changelog-2.3.2:

globus-compute-sdk & globus-compute-endpoint v2.3.2
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- In the ``globus-compute-endpoint`` CLI, commands which operate on registered endpoints
  can now accept UUID values in addition to names.

  - The following sub-commands can now accept either a name or a UUID:

    - ``delete``

    - ``restart``

    - ``start``

    - ``stop``

    - ``update_funcx_config``

  - (The other sub-commands either do not accept endpoint name arguments, like ``list``,
    or cannot accept UUID arguments, like ``configure``.)

- An informative error message will print to stdout when attempting to start or delete an
  endpoint while the Globus Compute web service is unreachable.

.. _changelog-2.3.1:

globus-compute-sdk & globus-compute-endpoint v2.3.1
---------------------------------------------------

Bug Fixes
^^^^^^^^^

- Fixed ``Executor.reload_tasks``, which was broken in v2.3.0 after changes
  related to using the new upstream submission route.

.. _changelog-2.3.0:

globus-compute-sdk & globus-compute-endpoint v2.3.0
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added a ``globus-compute-endpoint self-diagnostic`` command, which runs several
  diagnostic commands to help users and Globus Support troubleshoot issues.

  By default, all output prints to the terminal. The ``--gzip`` (or ``-z``) flag
  redirects the output to a Gzip-compressed file that the user can easily share
  with Globus Support.

  Endpoint log files can be quite large, so we cap the data taken from each file
  at 5,120 KB (5 MB). A user can modify this with the ``--log-kb`` option. For
  example, if a user wants to include 1,024 KB (1 MB) of data per log file, they
  would use ``--log-kb 1024``.

Bug Fixes
^^^^^^^^^

- Previously, starting an endpoint when it is already active or is currently locked
  will exit silently when ``globus-compute-endpoint start`` is run, with the only
  information available as a log line in endpoint.log.  Now, if start fails, a console
  message will display the reason on the command line.

- The ``data_serialization_strategy`` argument of ``Client`` is now properly respected
  when creating batches

- For those who use multiple task groups, address race-condition where tasks
  could be mis-associated.

- Fixes a bug where the |GlobusComputeEngine| sets the stdout and stderr capture
  filepaths incorrectly on the Providers, causing batch jobs to fail.

Removed
^^^^^^^

- When submitting functions, it is no longer possible to specify a ``task_group_id``
  which does not already exist on the services. If this happens, the services will
  respond with an error.

  - Note that it is still possible to associate a task with an existing
    ``task_group_id``, with the correct authorization.

- The following arguments to ``Client``, which were previously deprecated, have been
  removed:

  - ``asynchronous``

  - ``loop``

  - ``results_ws_uri``

  - ``warn_about_url_mismatch``

  - ``openid_authorizer``

  - ``search_authorizer``

  - ``fx_authorizer``

- Various internal classes relating to the former "asynchronous" mode of operating the
  ``Client``, such as ``WebSocketPollingTask`` and ``AtomicController``, have been
  removed alongside the removal of the ``asynchronous`` argument to the ``Client``.

Deprecated
^^^^^^^^^^

- The following arguments to ``Client``, which were previously unused, have been deprecated:

  - ``http_timeout``

  - ``funcx_home``

- The ``task_group_id`` argument to ``Client`` has been deprecated as a result of the
  new Task Group behavior.

Changed
^^^^^^^

- Following the updated route and schema of the ``submit`` route
  (``v3/endpoint/ENDPOINT_UUID/submit``), tasks in a batch are now associated
  with a single endpoint and the endpoint is selected via the route at
  submission time.  (Previously, tasks within a batch could be sent to
  heterogeneous endpoints.)

  - The signature of ``Client.create_batch`` has been adjusted to match.

  - The signature of ``WebClient.submit`` has been adjusted to match

- The return type of ``Client.batch_run`` has been updated to reflect the schema returned
  by the ``v3/submit`` route of the Compute API.

  - Concretely, ``Client.batch_run`` now returns a dictionary with information such as
    task group ID, submission ID, and a mapping of function IDs to lists of task IDs.

.. _changelog-2.2.4:

globus-compute-sdk & globus-compute-endpoint v2.2.4
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

* Auto-scaling support for |GlobusComputeEngine|.  Here is an example configuration in
  python:

.. code-block:: python

  engine = GlobusComputeEngine(
        address="127.0.0.1",
        heartbeat_period_s=1,
        heartbeat_threshold=1,
        provider=LocalProvider(
            init_blocks=0,  # Start with 0 blocks
            min_blocks=0,   # 0 minimum blocks
            max_blocks=4,   # scale upto 4 blocks
        ),
        strategy=SimpleStrategy(
            # Shut down blocks idle for more that 30s
            max_idletime=30.0,
        ),
    )

- Reimplemented ``ProcessPoolEngine``, which wraps ``concurrent.futures.ProcessPoolExecutor``,
  for concurrent local execution. We temporarily removed the former implementation because of a
  critical bug.

- Added support for deleting functions via the ``Client.delete_function`` method.

Bug Fixes
^^^^^^^^^

- The ``provider`` field was required in the endpoint YAML configuration but is
  not accepted by the ``ThreadPoolEngine``, rendering it unusable. The ``provider``
  field is now optional.

Changed
^^^^^^^

- Update Parsl requirement to version ``2023.7.3``

- As part of Parsl upgrade, drop support for Python 3.7.  Supported versions
  are now 3.8, 3.9, 3.10, and 3.11

.. _changelog-2.2.3:

globus-compute-sdk & globus-compute-endpoint v2.2.3
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added ``endpoint_setup`` and ``endpoint_teardown`` options to endpoint config, which,
  if present, are run by the system shell during the endpoint initialization process and
  shutdown process, respectively.

- The engine ``type`` field is now supported in ``config.yaml``. Here you can
  specify |GlobusComputeEngine| or ``HighThroughputEngine``, which is designed
  to bridge any backward compatibility issues.

Deprecated
^^^^^^^^^^

- The ``HighThroughputExecutor`` is now marked for deprecation.
  Importing and using this class will raise a warning.
  Upgrade to the ``globus_compute_endpoint.engines.GlobusComputeEngine`` which
  supercedes the ``HighThroughputExecutor``.

  Please note that the |GlobusComputeEngine| has the following limitations:

  #. It binds to all network interfaces instead of binding to a single interface
     to limit incoming worker connections to the internal network.

  #. Does not support dynamically switching containers are runtime, and requires
     containers to be specified at the time the endpoint is started.

  #. Pending support for auto-scaling with ``strategy``

  If the above limitations affect you, consider using ``globus_compute_endpoint.engines.HighThroughputEngine``
  which is a designed to bridge backward compatibility issues.

.. _Changelog-2.2.2:

globus-compute-sdk & globus-compute-endpoint v2.2.2
---------------------------------------------------

Bug Fixes
^^^^^^^^^

- Address bug in which adding a `strategy` stanza to a YAML config prohibits an
  endpoint from starting.

.. _changelog-2.2.0:

globus-compute-sdk & globus-compute-endpoint v2.2.0
-----------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added support for defining an endpoint's configuration in a config.yaml file.

  For backward compatibility, we will continue to support using a config.py file
  and ignore the config.yml file when a config.py file is in the endpoint directory.

- Users can now import the ``Config`` object via:
  ``from globus_compute_endpoint.endpoint.config import Config``

  For backwards compatibility, we continue to support importing from the old path:
  ``from globus_compute_endpoint.endpoint.utils.config import Config``

- The strategies used to serialize functions and arguments are now selectable at the
  ``Client`` level via constructor arguments (``code_serialization_strategy`` and
  ``data_serialization_strategy``)

  - For example, to use ``DillCodeSource`` when serializing functions:
    ``client = Client(code_serialization_strategy=DillCodeSource())``

  - This functionality is available to the ``Executor`` by passing a custom client.
    Using the client above: ``executor = Executor(funcx_client=client)``

- Added ``check_strategies`` method to ``ComputeSerializer`` for determining whether
  serialization strategies are compatible with a given use-case

Removed
^^^^^^^

- The SDK no longer sends ``entry_point`` when registering a function. (This field was
  unused elsewhere.)

Changed
^^^^^^^

- To avoid confusion, UUIDs will no longer be allowed as the name of an Endpoint.

- Simplified the logic used to select a serialization strategy when one isn't specified -
  rather than try every strategy in order, Globus Compute now simply defaults to
  ``DillCode`` and ``DillDataBase64`` for code and data respectively

.. _changelog-2.1.0:

globus-compute-sdk & globus-compute-endpoint v2.1.0
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Support for 3 new execution ``Engines``, designed to replace the ``HighThroughputExecutor``

  - |GlobusComputeEngine|: Wraps Parsl's ``HighThroughputExecutor`` to match the current
    default executor (globus-computes' fork of ``HighThroughputExecutor``)
  - ``ProcessPoolEngine``: Wraps ``concurrent.futures.ProcessPoolExecutor`` for concurrent
    local execution
  - ``ThreadPoolEngine``: Wraps ``concurrent.futures.ThreadPoolEngine`` for concurrent
    local execution on MacOS.

Bug Fixes
^^^^^^^^^

- Add validation logic to the endpoint ``configure`` subcommand to prevent
  certain classes of endpoint names.  That is, Compute Endpoints may have
  arbitrary _display_ names, but the name for use on the filesystem works best
  without, for example, spaces.  Now, the ``configure`` step will exit early
  with a (hopefully!) helpul error message explaining the problem.

.. _changelog-2.0.3:

globus-compute-sdk & globus-compute-endpoint v2.0.3
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Enable users to specify a custom Globus Compute directory (i.e., ``.globus_compute/``)
  via the environment variable ``GLOBUS_COMPUTE_USER_DIR``.

Removed
^^^^^^^

- Removed the ``check`` method from ``globus_compute_sdk.serialize.base.BaseSerializer``,
  and consequently also from ``globus_compute_sdk.serialize.ComputeSerializer``

Bug Fixes
^^^^^^^^^

- Address a concurrent data structure modification error that resulted in
  stalled processing and lost tasks

Changed
^^^^^^^

- The API ``https://api2.funcx.org/..`` URL has been updated to ``https://compute.api.globus.org/..``

.. _changelog-2.0.1:

globus-compute-sdk & globus-compute-endpoint v2.0.1
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

 - Support for timing out tasks that exceed a walltime limit on the globus-compute-endpoint.
   Use global variable ``GC_TASK_TIMEOUT`` which accepts a float to set the limit.
 - Add a ``--display-name`` option to endpoint configure to use as a human
   readable name for the endpoint. If not specified, the ``display_name``
   defaults to the endpoint name.

Bug Fixes
^^^^^^^^^

- Required fields were missing from the final endpoint status update that
  is sent when an endpoint is gracefully shutting down, causing issues when
  getting the status of an endpoint.

.. _changelog-2.0.0:

globus-compute-sdk & globus-compute-endpoint v2.0.0
---------------------------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- funcx and funcx-endpoint have been rebranded as globus-compute-sdk and globus-compute-endpoint.

- For the SDK, ``funcx.FuncXClient`` and ``funcx.FuncXExecutor`` have been renamed to ``globus_compute_sdk.Client``
  and ``globus_compute_sdk.Executor``

- The endpoint agent command is now ``globus-compute-endpoint`` instead of ``funcx-endpoint``.

- The above should be sufficient for many users.  If other classes from the old packages were
  in use, please see https://globus-compute.readthedocs.io/en/2.18.1/funcx_upgrade.html for more
  detailed change information and for additional upgrade requirements, if any.

Deprecated
^^^^^^^^^^

- The funcx and funcx-endpoint packages have been deprecated.

.. _changelog-1.0.13:

funcx & funcx-endpoint v1.0.13
------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Add two items to the ``Config`` object: ``idle_heartbeats_soft`` and
  ``idle_heartbeats_hard``.  If set, the endpoint will auto-shutdown after the
  specified number of heartbeats with no work to do.

Bug Fixes
^^^^^^^^^

- Address broken login-flow, introduced in v1.0.12 when attempting to start an
  endpoint.  This affected users with invalid or missing credentials.  (e.g.,
  new users or new installs).

Removed
^^^^^^^

- Removed all Search-related functionality.

Deprecated
^^^^^^^^^^

- Deprecated all Search-related arguments to ``FuncXClient`` methods.

.. _changelog-1.0.12:

funcx & funcx-endpoint v1.0.12
------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Implement client credentials for Kubernetes Endpoint Helm chart

Changed
^^^^^^^

- Updated package dependencies.
- Simplified format of endpoint status reports.
- Streamlined API function registration

.. _changelog-1.0.11:

funcx & funcx-endpoint v1.0.11
------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Created ``FuncxWebClient`` and ``FuncXClient`` methods to delete endpoints
  from the web service.
- Added a ``--force`` flag for the ``funcx-endpoint delete`` command, which
  ensures that the endpoint is deleted locally even if the web service
  returns an error or is unreachable.

Bug Fixes
^^^^^^^^^

- For new installs, handle unusual umask settings robustly.  Previously, a
  umask resulting in no execute or write permissions for the main configuration
  directory would result in an unexpected traceback for new users.  Now we
  ensure that the main configuration directory at least has the write and
  executable bits set.

- The ``funcx-endpoint delete`` command now deletes the endpoint both locally and
  from the web service.
- If a user attempts to start an endpoint that has already been marked as
  deleted in the web service, the process will exit with an error.

Security
^^^^^^^^

- Previously, the main configuraton directory (typically ``~/.funcx/``) would
  be created honoring the users umask, typically resulting in
  world-readability.  In a typical administration, this may be mitigated by
  stronger permissions on the user's home directory, but still isn't robust.
  Now, the group and other permissions are cleared.  Note that this does _not_
  change existing installs, and only address newly instantiated funcX endpoint
  setups.

.. _changelog-1.0.10:

funcx & funcx-endpoint v1.0.10
------------------------------

Bug Fixes
^^^^^^^^^

- Fix idle-executor handling in manager that was broken in v1.0.9

.. _changelog-1.0.9:

funcx & funcx-endpoint v1.0.9
-----------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- 'whoami' has been added to the cli to show the current logged in
  identity and linked identities.
  - A --linked-identities optional argument shows all linked identities
  - ie. `funcx-endpoint whoami` or `funcx-endpoint whoami --linked-identities`

Bug Fixes
^^^^^^^^^

- FuncXExecutor no longer ignores the specified ``container_id``.  The same
  function may now be utilized in containers via the normal workflow:

  .. code-block:: python

      import funcx

      def some_func():
          return 1
      with funcx.FuncXExecutor() as fxe:
          fxe.endpoint_id = "some-endpoint-uuid"
          fxe.container_id = "some-container_uuid"
          fxe.submit(some_func)
          fxe.container_id = "some-other-container-uuid"
          fxe.submit(some_func)  # same function, different container!
          # ...

Changed
^^^^^^^

- Initiate shutdown of any currently running FuncXExecutor objects when the main
  thread ends (a.k.a., "end of script").  This follows the same behavior as
  both ``ThreadPoolExecutor`` and ``ProcessPoolExecutor``.

.. _changelog-1.0.8:

funcx & funcx-endpoint v1.0.8
-----------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- The endpoint can now register metadata such as IP, hostname, and configuration values
  with the funcX services.

Changed
^^^^^^^

- Pin Parsl version required by the funcX Endpoint to v2023.1.23

.. _changelog-1.0.7:

funcx & funcx-endpoint v1.0.7
-----------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- When an API auth error is raised by a ``FuncXClient`` method, a new auth flow
  will be initiated.

- The funcX Endpoint will now shutdown after 5 consecutive failures to
  initialize.  (The previous behavior was to try indefinitely, even if the
  error was unrecoverable.)

- Add API Calls to request a docker image build and to check on the status of a
  submitted build

Changed
^^^^^^^

- The exceptions raised by ``FuncXClient`` when the web service sends back an
  error response are now instances of ``globus_sdk.GlobusAPIError`` and the
  FuncX specific subclass FuncxAPIError has been removed.

  Previous code that checked for FuncxAPIError.code_name should now check for
  GlobusAPIError.code

In prior versions of the ``funcx`` package:

.. code-block:: python

    import funcx

    client = funcx.FuncXClient()
    try:
        client.some_method(...)
    except funcx.FuncxAPIError as err:
        if err.code_name == "invalid_uuid":
            ...

In the new version:

.. code-block:: python

    import funcx
    import globus_sdk

    client = funcx.FuncXClient()
    try:
        client.some_method(...)
    except globus_sdk.GlobusAPIError as err:
        if err.code == "INVALID_UUID":
            ...

- Renamed the ``FuncXClient`` method ``lock_endpoint`` to ``stop_endpoint``.

- Renamed the ``Endpoint.stop_endpoint()`` parameter ``lock_uuid`` to ``remote``.

- ``HighThroughputExecutor.address`` now accepts only IPv4 and IPv6. Example
  configs have been updated to use ``parsl.address_by_interface`` instead of
  ``parsl.address_by_hostname``.  Please note that following this change,
  endpoints that were previously configured with
  ``HighThroughputExecutor(address=address_by_hostname())`` will now raise a
  ``ValueError`` and will need updating.

- For better security, ``HighThroughputExecutor`` now listens only on a
  specific interface rather than all interfaces.

.. _changelog-1.0.6:

funcx & funcx-endpoint v1.0.6
-----------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Add a '--remote' option when stopping endpoints to create a temporary lock such that any running endpoints with the same UUID will get a locked response and exit.

- Added `get_endpoints` methods to `FuncXWebClient` and `FuncXClient`, which retrieve
  a list of all endpoints owned by the current user

.. _changelog-1.0.5:

funcx & funcx-endpoint v1.0.5
-----------------------------

Bug Fixes
^^^^^^^^^

- Prevent Endpoint ID from wrapping in ``funcx-endpoint list`` output.

Changed
^^^^^^^

- Updated minimum Globus SDK requirement to v3.14.0

- Reorder ``funcx-endpoint list`` output: ``Endpoint ID`` column is now first
  and ``Endpoint Name`` is now last.

.. _changelog-1.0.5a0:

funcx & funcx-endpoint v1.0.5a0
-------------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added ``.get_result_amqp_url()`` to ``FuncXClient`` to acquire user
  credentials to the AMQP service.  Globus credentials are first verified
  before user-specific AMQP credentials are (re)created and returned.  The only
  expected use of this method comes from ``FuncXExecutor``.

- Captures timing information throughout the endpoint by reporting
  TaskTransitions.

Bug Fixes
^^^^^^^^^

- General and specific attention to the ``FuncXExecutor``, especially around
  non-happy path interactions
  - Addressed the often-hanging end-of-script problem
  - Address web-socket race condition (GH#591)

Deprecated
^^^^^^^^^^

- ``batch_enabled`` argument to ``FuncXExecutor`` class; batch communication is
  now enforced transparently.  Simply use ``.submit()`` normally, and the class
  will batch the tasks automatically.  ``batch_size`` remains available.

- ``asynchronous``, ``results_ws_uri``, and ``loop`` arguments to
  ``FuncXClient`` class; use ``FuncXExecutor`` instead.

Changed
^^^^^^^

- Refactor ``funcx.sdk.batch.Batch.add`` method interface.  ``function_id`` and
  ``endpoint_id`` are now positional arguments, using language semantics to
  enforce their use, rather than (internal) manual ``assert`` checks.  The
  arguments (``args``) and keyword arguments (``kwargs``) arguments are no
  longer varargs, and thus no longer prevent function use of ``function_id``
  and ``endpoint_id``.

- ``FuncXExecutor`` no longer creates a web socket connection; instead it
  communicates directly with the backing AMQP service.  This removes an
  internal round trip and is marginally more performant.

- ``FuncXExecutor`` now much more faithfully implements the
  ``_concurrent.futures.Executor`` interface.  In particular, the
  ``endpoint_id`` and ``container_id`` items are specified on the executor
  _object_ and not per ``.submit()`` invocation.  See the class documentation
  for more information.

.. _changelog-1.0.4:

funcx & funcx-endpoint v1.0.4
-----------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Add `.task_count_submitted` member to FuncXExecutor.  This value is useful
  for determining in client code how many tasks have *actually* made it to the
  funcX Web Services.

- Add a flag to avoid creating websocket queues on batch runs, the new default is not to create.
  Note that if the queue is not created, results will have to be retrieved directly instead of
  via background polling of the websocket

Bug Fixes
^^^^^^^^^

- gh#907 - Enable concurrent access to the token store by manually serializing
  access to the SQLite DB.

Deprecated
^^^^^^^^^^

- The `batch_interval` keyword argument to the FuncXExecutor is no longer
  utilized.  Internally, the executor no longer waits to coalesce tasks.
  Instead, it pulls them as fast as possible until either the input queue lags
  or the count of tasks in the batch reaches `batch_size`.

Changed
^^^^^^^

- The `funcx_client` argument to `FuncXExecutor()` has been made optional. If nothing
  is passed in, the `FuncXExecutor` now creates a `FuncXClient` for itself.

.. _changelog-1.0.3:

funcx & funcx-endpoint v1.0.3
-----------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Add logic to support Globus Auth client credentials. This allows users to
  specify FUNCX_SDK_CLIENT_ID and FUNCX_SDK_CLIENT_SECRET environment variables
  to use a client credential.

- Endpoints now report their online status immediately on startup (previously,
  endpoints waited ``heartbeat_period`` seconds before reporting their status).

- In order to support the new endpoint status format, endpoints now report their
  heartbeat period as part of their status report package.

- Add `--log-to-console` CLI flag to the endpoint.  This is mostly to entertain
  additional development styles, but may also be useful for some end-user
  workflows.

- funcX Endpoint: Implement ANSI escape codes ("color") for log lines emitted
  to the console.  This is currently targeted to aid the development and
  debugging process, so color is strictly to the console, not to logs.  Use
  the `--log-to-console` and `--debug` flags together.

- Added logout command for funcx-endpoint to revoke cached tokens

Changed
^^^^^^^

- Changed the way that endpoint status is stored in the services - instead of storing a
  list of the most recent status reports, we now store the single most recent status
  report with a TTL set to the endpoint's heartbeat period. This affects the formatting
  of the return value of ``FuncXClient.get_endpoint_status``.

.. _changelog-1.0.0:

funcx & funcx-endpoint v1.0.2
-----------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- New `ResultStore` class, that will store backlogged result messages to
  `<ENDPOINT_DIR>/unacked_results/`

- Upon disconnect from RabbitMQ, the endpoint will now retry connecting
  periodically while the executor continues to process tasks

Bug Fixes
^^^^^^^^^

- Fixed issue with `quiesce` event not getting set from the SIGINT handler,
  resulting in cleaner shutdowns

- DillCodeSource updated to use dill's lstrip option to serialize
  function definitions in nested contexts.

Removed
^^^^^^^

- `ResultsAckHandler` is removed, and `unacked_results.p` files are now
  obsolete.

Changed
^^^^^^^

- DillCodeSource will now be used ahead of DillCode

funcx & funcx-endpoint v1.0.1
-----------------------------

Bug Fixes
^^^^^^^^^

- Fix bug where stored credentials would fail to be loaded (manifesting in an
  EOF error for background processes while unnecessarily attempting to
  recollect credentials)

funcx & funcx-endpoint v1.0.0
-----------------------------

Bug Fixes
^^^^^^^^^

 - Now using the correct HighThroughputExecutor constructor arg to set the log dir for workers

New Functionality
^^^^^^^^^^^^^^^^^

- ``FuncXClient`` now warns you if it thinks you may have supplied ``funcx_service_address``
  and ``results_ws_uri`` that point to different environments. This behavior can be
  turned off by passing ``warn_about_url_mismatch=False``.

Removed
^^^^^^^

- The off_process_checker, previously used to test function serialization methods, was removed

Changed
^^^^^^^

- [Breaking] funcx and funcx-endpoint both require v1.0.0+ to connect to cloud-hosted
  services, and older versions will no longer be supported.

- [Breaking] funcx-endpoint now connects to the cloud-hosted services with RabbitMQ
  over port:5671 instead of ZeroMQ which previously used ports (55001-55003).

- [Breaking] Communication with the services are now encrypted and go over AMQPS
  (TLS/SSL encrypted AMQP).

- Pickle module references were replaced with dill

- The order of serialization method attempts has been changed to try dill.dumps first

- Alter the FuncXEndpoint to include a timestamp with each task state change.
  This is mostly for the development team so as to support retrospective log
  analyses of where tasks get stuck in the pipeline.

- The Parsl dependency has been upgraded to a more recent
  parsl master, from the older parsl 1.1 release.
  This allows recent changes to provider functionality to
  be accessed by funcX endpoint administrators.

.. _changelog-0.4.0a2:

funcx & funcx-endpoint v0.4.0a2
-------------------------------

Added
^^^^^

- The ``FuncXWebClient`` now sends version information via ``User-Agent`` headers
  through the ``app_name`` property exposed by ``globus-sdk``

  - Additionally, users can send custom metadata alongside this version
    information with ``user_app_name``

- The funcx-endpoint service now interfaces with RabbitMQ.

  - As previously, the endpoint registers with the FuncX web service upon
    startup, but now receives endpoint-specific RabbitMQ connection
    configuration.

Removed
^^^^^^^

- The config file in ``~/.funcx/config.py`` has been removed from any
  application logic. The file will not be automatically cleaned up but is
  ignored by the funcx-endpoint application.

Changed
^^^^^^^

- The CLI interface for ``funcx-endpoint`` has been updated in several ways:

  - ``-h`` is supported as a help option

  - ``funcx-endpoint --version`` has been replaced with ``funcx-endpoint version``

- The ``funcx`` error module has been renamed from ``funcx.utils.errors`` to
  ``funcx.errors``

funcx & funcx-endpoint v0.4.0a1
-------------------------------

Added
^^^^^

* ``TaskQueueSubscriber`` class added that allows receiving tasks over RabbitMQ
* ``ResultQueuePublisher`` class added that allows publishing results and status over RabbitMQ
* ``TaskQueuePublisher`` class added for testing
* ``ResultQueueSubscriber`` class added for testing
* A bunch of tests are added that test the above classes described above

- Implement Task Group reloading on the FuncXExecutor.  Look for ``.reload_tasks()``

- FuncXExecutor.submit returns futures with a .task_id attribute
  that will contain the task ID of the corresponding FuncX task.
  If that task has not been submitted yet, then that attribute
  will contain None.

- The ``FuncXClient`` may now be passed ``do_version_check=False`` on init,
  which will lead to faster startup times

- The ``FuncXClient`` now accepts a new argument ``login_manager``, which is
  expected to implement a protocol for providing authenticated http client
  objects, login, and logout capabilities.

- The login manager and its protocol are now defined and may be imported as in
  ``from funcx.sdk.login_manager import LoginManager, LoginManagerProtocol``.
  They are internal components but may be used to force a login or to implement
  an alternative ``LoginManagerProtocol`` to customize authentication

Removed
^^^^^^^

- The following arguments to ``FuncXClient`` are no longer supported:
  ``force_login``

- The ``SearchHelper`` object no longer exposes a method for searching for
  endpoints, as this functionality was never fully implemented.

- The custom response type provided by the SearchHelper object has been
  removed. Instead, callers to function search will get the Globus Search
  response object directly

Deprecated
^^^^^^^^^^

- The following arguments to ``FuncXClient`` are deprecated and will emit
  warnings if used: ``fx_authorizer``, ``search_authorizer``,
  ``openid_authorizer``. The use-cases for these arguments are now satisfied by
  the ability to pass a custom ``LoginManager`` to the client class, if desired.

- The ``openid_authorizer`` argument to FuncXClient is now deprecated. It can
  still be passed, but is ignored and will emit a ``DeprecationWarning`` if
  used

Changed
^^^^^^^

- The endpoint has a new log level, TRACE, which is more verbose than DEBUG

- The ``FuncXClient`` constructor has been refactored. It can no longer be
  passed authorizers for various sub-services. Instead, a new component, the
  ``LoginManager``, has been introduced which makes it possible to pass
  arbitrary globus-sdk client objects for services (by passing a customized
  login manager). The default behavior remains the same, checking login and
  doing a new login on init.

- Tokens are now stored in a new location, in a sqlite database, using
  ``globus_sdk.tokenstorage``. Users will need to login again after upgrading
  from past versions of ``funcx``.

- Remove support for python3.6

- Endpoint logs have been reduced in verbosity. A number of noisy log lines have been
  lowered to TRACE level. [PREFIXES] have been removed from many messages as they
  contain information more reliably availale in log metadata.

- `FuncXExecutor <https://funcx.readthedocs.io/en/latest/executor.html>`_
  now uses batched submission by default.  This typically significantly
  improves the task submission rate when using the executor interface (for
  example, 3 seconds to submit 500 tasks vs 2 minutes, in an informal test).
  However, individual task submission latency may be increased.

  To use non-batched submission mode, set `batch_mode=False` when instantiating
  the `FuncXExecutor <https://funcx.readthedocs.io/en/latest/executor.html>`_
  object.

.. _changelog-0.3.9:

funcx & funcx-endpoint v0.3.9
-----------------------------

Bug Fixes
^^^^^^^^^

- Improve performance in endpoint interchange->manager dispatch,
  by fixing a race condition in worker status processing.
  In an example kubernetes setup, this can double throughput of
  5 second tasks on 6 workers.

- Pin the version of ``click`` used by ``funcx-endpoint``. This resolves issues
  stemming from ``typer`` being incompatible with the latest ``click`` release.

Removed
^^^^^^^

- FuncXFuture was removed. This functionality has been superseded by
  code in FuncXExecutor which uses plain Futures.

Changed
^^^^^^^

- Endpoint logs now have richer metadata on each log line

- Endpoint threads and processes now have human readable names, for logging metadata

funcx & funcx-endpoint v0.3.8
-----------------------------

New Functionality
^^^^^^^^^^^^^^^^^

- Added option for pinning workers to different accelerators
- Log standard error and output from workers to disk

Changed
^^^^^^^

- ``FuncXExecutor`` is now importable from the top-level namespace, as in
  ``from funcx import FuncXExecutor``

funcx & funcx-endpoint v0.3.7
-----------------------------

Bug Fixes
^^^^^^^^^

- When a provider raised an exception, that exception was then mishandled
  and presented as an AttributeError. This handling now no longer corrupts
  the exception. https://github.com/globus/globus-compute/issues/679

New Functionality
^^^^^^^^^^^^^^^^^

- Capture, log, and report execution time information. The time a function takes to execute is now logged in worker debug logs and reported to the funcX service.

- Added Helm options to specify Kuberenetes workerDebug, imagePullSecret and maxIdleTime values.

Changed
^^^^^^^

- Kubernetes worker pods will now be named funcx-worker-*
  instead of funcx-* to clarify what these pods are to
  observers of 'kubectl get pods'

- Logging for funcx-endpoint no longer writes to ``~/.funcx/endpoint.log`` at any point.
  This file is considered deprecated. Use ``funcx-endpoint --debug <command>`` to
  get debug output written to stderr.
- The output formatting of ``funcx-endpoint`` logging has changed slightly when
  writing to stderr.

funcx & funcx-endpoint v0.3.6
-----------------------------

Released on February 1, 2022.


Bug Fixes
^^^^^^^^^

- Updates the data size limit for WebSockets from 1MB to 11MB to
  address issue:https://github.com/globus/globus-compute/issues/677

- Fixed an issue in which funcx-endpoint commands expected the ``~/.funcx/``
  directory to exist, preventing the endpoint from starting on new installs

Changed
^^^^^^^

- The version of ``globus-sdk`` used by ``funcx`` has been updated to v3.x .

- ``FuncXClient`` is no longer a subclass of ``globus_sdk.BaseClient``, but
  instead contains a web client object which can be used to prepare and send
  requests to the web service

- ``FuncXClient`` will no longer raise throttling-related errors when too many
  requests are sent, and it may sleep and retry requests if errors are
  encountered

- The exceptions raised by the ``FuncXClient`` when the web service sends back
  an error response are now instances of ``funcx.FuncxAPIError``. This
  means that the errors no longer inherit from ``FuncxResponseError``. Update
  error handling code as follows:

In prior versions of the ``funcx`` package:

.. code-block:: python

    import funcx
    from funcx.utils.response_errors import (
        FuncxResponseError, ResponseErrorCode
    )

    client = funcx.FuncXClient()
    try:
        client.some_method(...)
    except FuncxResponseError as err:
        if err.code == ResponseErrorCode.INVALID_UUID:  # this is an enum
            ...

In the new version:

.. code-block:: python

    import funcx

    client = funcx.FuncXClient()
    try:
        client.some_method(...)
    except funcx.FuncxAPIError as err:
        if err.code_name == "invalid_uuid":  # this is a string
            ...

funcx & funcx-endpoint v0.3.5
-----------------------------


Released on January 12th, 2021

funcx v0.3.5 is a minor release that includes contributions (code, tests, reviews, and reports) from:
Ben Clifford <benc@hawaga.org.uk>, Ben Galewsky <bengal1@illinois.edu>,
Daniel S. Katz <d.katz@ieee.org>, Kirill Nagaitsev <knagaitsev@uchicago.edu>
Michael McQuade <michael@giraffesyo.io>, Ryan Chard <rchard@anl.gov>,
Stephen Rosen <sirosen@globus.org>, Wes Brewer <whbrew@gmail.com>
Yadu Nand Babuji <yadudoc1729@gmail.com>, Zhuozhao Li <zhuozhl@clemson.edu>

Bug Fixes
^^^^^^^^^

* ``MaxResultSizeExceeded`` is now defined in ``funcx.utils.errors``. Fixes `issue#640 <https://github.com/globus/globus-compute/issues/640>`_

* Fixed Websocket disconnect after idling for 10 mins. See `issue#562 <https://github.com/globus/globus-compute/issues/562>`_
  funcX SDK will not auto-reconnect on remote-side disconnects

* Cleaner logging on the ``funcx-endpoint``. See `PR#643 <https://github.com/globus/globus-compute/pull/643>`_
  Previously available ``set_stream_logger``, ``set_file_logger`` methods are now removed.
  For debugging the SDK use standard logging methods, as described in the
  `Python Logging HOWTO <https://docs.python.org/3/howto/logging.html>`_, on
  the logger named ``"funcx"``.

  For example:

  .. code-block::

    import logging

    logger = logging.getLogger("funcx")
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    funcx_logger.addHandler(ch)

* Warn and continue on failure to load a results ack file. `PR#616 <https://github.com/globus/globus-compute/pull/616>`_


New Functionality
^^^^^^^^^^^^^^^^^

* Result size raised to 10MB from 512KB. See `PR#647 <https://github.com/globus/globus-compute/pull/647>`_

* Version match constraints between the ``funcx-endpoint`` and the ``funcx-worker`` are now relaxed.
  This allows containers of any supported python3 version to be used for running tasks.
  See `PR#637 <https://github.com/globus/globus-compute/pull/637>`_

* New example config for Polaris at Argonne Leadership Computing Facility

* Simplify instructions for installing endpoint secrets to cluster. `PR#623 <https://github.com/globus/globus-compute/pull/623>`_

* Webservice and Websocket service URLs are resolved by the names "production" and
  "dev". These values can be passed to FuncX client init as in ``environment="dev"``,
  or by setting the ``FUNCX_SDK_ENVIRONMENT`` environment variable.

* Support for cancelling tasks in ``funcx_endpoint.executors.HighThroughputExecutor``. To cancel a
  task, use the ``best_effort_cancel`` method on the task's ``future``. This method differs from the
  concurrent futures ``future.cancel()`` method in that a running task can be cancelled.
  ``best_effort_cancel`` returns ``True`` only if the task is cancellable with no guarantees that the
  task will not execute. If the task is already complete, it returns ``False``

  .. note:: Please note that this feature is not yet supported on the SDK.

  Example:

     .. code-block:: python

        from funcx_endpoint.executors import HighThroughputExecutor
        htex = HighThroughputExecutor(passthrough=False)
        htex.start()

        future = htex.submit(slow_function)
        future.best_effort_cancel()


funcx & funcx-endpoint v0.3.4
-----------------------------

Released on October 14th, 2021

funcx v0.3.4 is a minor release that includes contributions (code, tests, reviews, and reports) from:

Ben Galewsky <bengal1@illinois.edu>, Kyle Chard <chard@uchicago.edu>,
Stephen Rosen <sirosen@globus.org>, and Yadu Nand Babuji <yadudoc1729@gmail.com>

Bug Fixes
^^^^^^^^^

* Updated requirements to exclude ``pyzmq==22.3.0`` due to unstable wheel. `Issue#577 <https://github.com/globus/globus-compute/issues/611>`_

* Updated requirements specification to ``globus-sdk<3.0``

New Functionality
^^^^^^^^^^^^^^^^^

* Docs have been restructured and updated to use a cleaner theme

* New smoke_tests added to test hosted services



funcx & funcx-endpoint v0.3.3
-----------------------------

Released on September 20th, 2021

funcx v0.3.3 is a minor release that includes contributions (code, tests, reviews, and reports) from:

Ben Galewsky <bengal1@illinois.edu>, Kyle Chard <chard@uchicago.edu>,
Kirill Nagaitsev <knagaitsev@uchicago.edu>, Stephen Rosen <sirosen@globus.org>,
Uriel Mandujano <uriel@globus.org>, and Yadu Nand Babuji <yadudoc1729@gmail.com>


Bug Fixes
^^^^^^^^^

* An exception is raised if results arrive over WebSocket result when no future is available to receive it `PR#590 <https://github.com/globus/globus-compute/pull/590>`_

* Example configs have been updated to use ``init_blocks=0`` as a default. `PR#583 <https://github.com/globus/globus-compute/pull/583>`_

* Log result passing to forwarder only for result messages `PR#577 <https://github.com/globus/globus-compute/pull/577>`_

* Fix zmq option setting bugs `PR#565 <https://github.com/globus/globus-compute/pull/565>`_

New Functionality
^^^^^^^^^^^^^^^^^

* Endpoints will now stay running and retry connecting to funcX hosted services in a disconnection event `PR#588 <https://github.com/globus/globus-compute/pull/588>`_, `PR#572 <https://github.com/globus/globus-compute/pull/572>`_

* Endpoints will now use ACK messages from the forwarder to confirm that results have been received `PR#571 <https://github.com/globus/globus-compute/pull/571>`_

* Endpoints will persist unacked results and resend them during disconnection events `PR#580 <https://github.com/globus/globus-compute/pull/580>`_

* Result size limits have been revised from 10MB to 512KB. If result size exceeds 512KB, a ``MaxResultSizeExceeded`` exception is returned. `PR#586 <https://github.com/globus/globus-compute/pull/586>`_

* Add additional platform info to registration message `PR#592 <https://github.com/globus/globus-compute/pull/592>`_

* All endpoint logs, (EndpointInterchange.log, interchange.stderr, interchange.stdout) will now be collated into a single log: ``endpoint.log`` `PR#582 <https://github.com/globus/globus-compute/pull/582>`_

funcx & funcx-endpoint v0.3.2
-----------------------------

Released on August 11th, 2021

funcx v0.3.2 is a minor release that includes contributions (code, tests, reviews, and reports) from:
Ben Galewsky <bengal1@illinois.edu>, Rafael Vescovi <ravescovi@gmail.com>, Ryan <rchard@anl.gov>,
Yadu Nand Babuji <yadudoc1729@gmail.com>, Zhuozhao Li <zhuozhl@clemson.edu>


New Functionality
^^^^^^^^^^^^^^^^^

* Streamlined release process `PR#569 <https://github.com/globus/globus-compute/pull/569>`_, `PR#568 <https://github.com/globus/globus-compute/pull/568>`_

* Added a new funcX config for ``Cooley`` at ALCF. `PR#566 <https://github.com/globus/globus-compute/pull/566>`_


funcx & funcx-endpoint v0.3.1
-----------------------------

Released on July 26th, 2021

funcx v0.3.1 is a minor release that includes contributions (code, tests, reviews, and reports) from:
Ben Galewsky <bengal1@illinois.edu>, Kirill Nagaitsev <knagaitsev@uchicago.edu>, Ryan Chard <rchard@anl.gov>, and Yadu Nand Babuji <yadudoc1729@gmail.com>

Bug Fixes
^^^^^^^^^

* Removed process check from endpoint status check for better cross platform support `PR#559 <https://github.com/globus/globus-compute/pull/559>`_

* Fixes to ensure that ``container_cmd_options`` propagate correctly `PR#555 <https://github.com/globus/globus-compute/pull/555>`_



funcx & funcx-endpoint v0.3.0
-----------------------------

Released on July 08th, 2021

funcx v0.3.0 is a major release that includes contributions (code, tests, reviews, and reports) from:
Ben Galewsky <bengal1@illinois.edu>, Kyle Chard <chard@uchicago.edu>,
Kirill Nagaitsev <knagaitsev@uchicago.edu>, Daniel S. Katz <d.katz@ieee.org>,
Stephen Rosen <sirosen@globus.org>, Yadu Nand Babuji <yadudoc1729@gmail.com>,
Yongyan Rao <yongyan.rao@gmail.com>, and Zhuozhao Li <zhuozhao@uchicago.edu>

Bug Fixes
^^^^^^^^^

* ``FuncXClient.get_result(<TASK_ID>)`` will now raise a ``TaskPending`` with an expanded failure reason.  See `PR#502 <https://github.com/globus/globus-compute/pull/502>`_

* funcx-endpoint start and stop commands are now improved to report broken/disconnected states and handle them better. See `issue#327 <https://github.com/globus/globus-compute/issues/327>`_

* Fixed ManagerLost exceptions triggering failures.  See `issue#486 <https://github.com/globus/globus-compute/issues/486>`_

* Several fixes and tests for better error reporting. See `PR#523 <https://github.com/globus/globus-compute/pull/523>`_



New Functionality
^^^^^^^^^^^^^^^^^

* Support added for websockets to minimize result fetching latency.

* ``FuncXClient(asynchronous=True)`` now enables asynchronous result fetching using Asycio library.

  Here's an example:

    .. code-block:: python

        from funcx import FuncXClient

        def hello():
            return "Hello World!"

        fxc = FuncXClient(asynchronous=True)
        fn_id = fxc.register_function(hello, description="Hello")

        # In asynchronous mode, function run returns asyncio futures
        async_future = fxc.run(endpoint_id=<ENDPOINT_ID>, function_id=fn_id)
        print("Result : ", await async_future)

* A new ``FuncXExecutor`` class exposes funcX functionality using the familiar executor interface from the ``concurrent.futures`` library.

  Here's an example:

    .. code-block:: python

        from funcx import FuncXClient
        from funcx.sdk.executor import FuncXExecutor

        def hello():
            return "Hello World!"

        funcx_executor = FuncXExecutor(FuncXClient())

        # With the executor, functions are auto-registered
        future = funcx_executor.submit(hello, endpoint_id=<ENDPOINT_ID>)

        # You can check status of your task without blocking
        print(future.done())

        # Block and wait for the result:
        print("Result : ", future.result())


* Endpoint states have been renamed to ``running``, ``stopped``, and ``disconnected``. See `PR#525 <https://github.com/globus/globus-compute/pull/525>`_

* Container routing behavior has been improved to support ``soft`` and ``hard`` routing strategies. See `PR#324 <https://github.com/globus/globus-compute/pull/324>`_

funcx & funcx-endpoint v0.2.3
-----------------------------

Released on May 19th, 2021

funcx v0.2.3 is a minor release that includes contributions (code, tests, reviews, and reports) from:
Ben Galewsky <ben@peartreestudio.net>, Ryan Chard <rchard@anl.gov>, Weinan Si <siweinan@gmail.com>,
Yongyan Rao <yongyan.rao@gmail.com> Yadu Nand Babuji <yadudoc1729@gmail.com> and Zhuozhao Li <zhuozhao@uchicago.edu>


Bug Fixes
^^^^^^^^^

* Fixed a missing package in the ``requirements.txt`` file

* Updated version requirements in ``funcx-endpoint`` to match the ``funcx`` version

* ``funcx-endpoint`` commandline autocomplete has been fixed. See `issue#496 <https://github.com/globus/globus-compute/issues/496>`_

* ``funcx-endpoint restart`` failure is fixed. See `issue#488 <https://github.com/globus/globus-compute/issues/488>`_

* Several fixes and improvements to worker terminate messages which caused workers to crash silently. See `issue#462 <https://github.com/globus/globus-compute/pull/462>`_

* Fixed ``KubernetesProvider`` to use a default of ``init_blocks=0``. See `issue#237 <https://github.com/globus/globus-compute/issues/237>`_



New Functionality
^^^^^^^^^^^^^^^^^


* ``FuncXClient.get_result(<TASK_ID>)`` will now raise a ``TaskPending`` exception if the task is not complete.

* Multiple improvement to function serialization. See `issue#479 <https://github.com/globus/globus-compute/pull/479>`_

  * ``FuncXSerializer`` has been updated to prioritize source-based function serialization methods that offer
    more reliable behavior when the python version across the client and endpoint do not match.

  * ``FuncXSerializer`` now attempts deserialization on an isolated process to preempt failures on a remote worker.

* More consistent worker task message types. See `PR#462 <https://github.com/globus/globus-compute/pull/462>`_

* Better OS agnostic path joining. See `PR#458 <https://github.com/globus/globus-compute/pull/458>`_



funcx & funcx-endpoint v0.2.2
-----------------------------

Released on April 15th, 2021

funcx v0.2.2 is a hotfix release that includes contributions (code, tests, reviews, and reports) from:

Yadu Nand Babuji <yadudoc1729@gmail.com> and Zhuozhao Li <zhuozhao@uchicago.edu>


Bug Fixes
^^^^^^^^^

* Fixed a missing package in the ``requirements.txt`` file

* Updated version requirements in ``funcx-endpoint`` to match the ``funcx`` version


funcx & funcx-endpoint v0.2.1
-----------------------------

Released on April 15th, 2021

funcx v0.2.1 includes contributions (code, tests, reviews, and reports) from:

Daniel S. Katz <d.katz@ieee.org>, Yadu Nand Babuji <yadudoc1729@gmail.com>,
Yongyan Rao <yongyan.rao@gmail.com>, and Zhuozhao Li <zhuozhao@uchicago.edu>

New Features
^^^^^^^^^^^^

* Cleaner reporting when an older non-compatible ``Config`` object is used. Refer: `issue 427 <https://github.com/globus/globus-compute/issues/427>`_

* Better automated checks at SDK initialization to confirm that the SDK and Endpoint versions are supported by the web-service.

* Updated Kubernetes docs and example configs.


Bug Fixes
^^^^^^^^^

* Fixed a bug in funcx-endpoint that caused the ZMQ connections to timeout and crash, terminating the endpoint.

* Fixed an unsafe string based version comparison check.

* Fixed an issue with poor error reporting when starting non-existent endpoints. Refer: `issue 432 <https://github.com/globus/globus-compute/issues/432>`_

* Fixed a bug in incorrectly passing the ``funcx_service_address`` to the EndpointInterchange.

* Several updates to the docs for clarity.

* JSON serializer is removed from the FuncXSeralizer mechanism due to issues with not preserving types over serialization (tuples/lists)


funcx & funcx-endpoint v0.2.0
-----------------------------

Released on April 8th, 2021

funcx v0.2.0 includes contributions (code, tests, reviews, and reports) from:

Ariel Rokem <arokem@gmail.com>, Ben Blaiszik <blaiszik@uchicago.edu>, Ben Galewsky <ben@peartreestudio.net>, Ben Glick <glick@glick.cloud>, Joshua Bryan <josh@globus.org>, Kirill Nagaitsev <knagaitsev@uchicago.edu>, Kyle Chard <chard@uchicago.edu>, pratikpoojary <pratik.poojary@somaiya.edu>, Ryan <rchard@anl.gov>, Yadu Nand Babuji <yadudoc1729@gmail.com>, yongyanrao <yongyan.rao@gmail.com>, and Zhuozhao Li <zhuozhao@uchicago.edu>

Known Issues
^^^^^^^^^^^^

There is an ongoing stability issue with ``pyzmq`` wheels that causes endpoint crashes.
Read more about this `here <https://github.com/zeromq/libzmq/issues/3313>`_.
To address this issue, we recommend the following:

.. code-block:: bash

   # Ensure you are using a GCC version older than v7
   gcc --version

   # Install pyzmq without the binaries from Pypi:
   pip install --no-binary :all: --force-reinstall pyzmq


New Functionality
^^^^^^^^^^^^^^^^^

* The security architecture has been overhauled. The current sequence of endpoint registration is as follows:

  * funcx-endpoint will connect to the funcx web-service and register itself
  * Upon registration, the endpoint receives server certificates and connection info.
  * funcx-endpoint connects to a forwarder service over an encrypted (Curve25519 elliptic curve) ZMQ channel using the server certificates.
  * If the connection is terminated this whole process repeats.

* Significant changes to the `Config object`. All options related to executors have been moved from the top level Config object to the executor object.
  Refer to the `configuration <configuration>`_ section for more details. Here's an example of the config change:

    This is the old style config:

    .. code-block:: python

       from funcx_endpoint.endpoint.utils.config import Config
       from parsl.providers import LocalProvider

       config = Config(
           # Options at the top-level like provider and max_workers_per_node
           # are moved to the executor object
           scaling_enabled=True,
           provider=LocalProvider(
               init_blocks=1,
               min_blocks=1,
               max_blocks=1,
           ),
           max_workers_per_node=2,
           funcx_service_address='https://api.funcx.org/v1'
       )

    Here is a sample config based on the updated Config object:

    .. code-block:: python

       from funcx_endpoint.endpoint.utils.config import Config
       from funcx_endpoint.executors import HighThroughputExecutor
       from parsl.providers import LocalProvider

       config = Config(
           executors=[HighThroughputExecutor(
               provider=LocalProvider(
                   init_blocks=1,
                   min_blocks=0,
                   max_blocks=1,
               ),
           )],
           detach_endpoint=True,
           funcx_service_address='https://compute.api.globus.org/v2'
       )

* The endpoint will now log to `~/.funcx/<ENDPOINT_NAME>/EndpointInterchange.log`.

* Several updates to logging make logs more concise and cleaner.

* The serialization mechanism has been updated to use multiple serialization libraries (dill, pickle)

* The funcx-endpoint CLI tool will raise an error message to screen if endpoint registration fails rather than log to a file

* Richer HTTP error codes and responses for failure conditions and reporting.

* The `/submit` route response format has changed. Previously, this route would return an error after the first failed task submission attempt. Now, the service will attempt to submit all tasks that the user sends via this route.

    This is the old response format, assuming all tasks submit successfully:

    .. code-block:: json

        {
          "status": "Success",
          "task_uuids": ["task_id_1", "task_id_2", "..."]
        }

    This is the new response format, where some task submissions have failed:

    .. code-block:: json

        {
          "response": "batch",
          "results": [
            {
              "status": "Success",
              "task_uuid": "task_id_1",
              "http_status_code": 200
            },
            {
              "status": "Failed",
              "code": 1,
              "task_uuid": "task_id_2",
              "http_status_code": 400,
              "error_args": ["..."]
            },
            "..."
          ]
        }


* ``get_batch_status`` has been renamed to ``get_batch_result``

.. |nbsp| unicode:: 0xA0
   :trim:

.. |Batch| replace:: :class:`Batch <globus_compute_sdk.sdk.batch.Batch>`
.. |Executor| replace:: :class:`Executor <globus_compute_sdk.sdk.executor.Executor>`
.. |MPIFunction| replace:: :class:`MPIFunction <globus_compute_sdk.sdk.mpi_function.MPIFunction>`
.. |ShellFunction| replace:: :class:`ShellFunction <globus_compute_sdk.sdk.shell_function.ShellFunction>`
.. |UserRuntime| replace:: :class:`UserRuntime <globus_compute_sdk.sdk.batch.UserRuntime>`

.. |GlobusComputeEngine| replace:: :class:`GlobusComputeEngine <globus_compute_endpoint.engines.globus_compute.GlobusComputeEngine>`
.. |GlobusMPIEngine| replace:: :class:`GlobusMPIEngine <globus_compute_endpoint.engines.globus_mpi.GlobusMPIEngine>`

.. |MPIExecutor| replace:: ``MPIExecutor``
.. _MPIExecutor: https://parsl.readthedocs.io/en/stable/stubs/parsl.executors.MPIExecutor.html
.. |HighThroughputExecutor| replace:: ``HighThroughputExecutor``
.. _HighThroughputExecutor: https://parsl.readthedocs.io/en/stable/stubs/parsl.executors.HighThroughputExecutor.html
.. |LocalProvider| replace:: ``LocalProvider``
.. _LocalProvider: https://parsl.readthedocs.io/en/stable/stubs/parsl.providers.LocalProvider.html
