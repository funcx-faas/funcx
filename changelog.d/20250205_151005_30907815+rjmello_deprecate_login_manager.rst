New Functionality
^^^^^^^^^^^^^^^^^

- Added an optional ``authorizer`` parameter to the ``Client`` initializer to support
  using a ``GlobusAuthorizer`` for authentication. This parameter is mutually exclusive
  with ``app``.

Deprecated
^^^^^^^^^^

- The ``LoginManager`` and ``AuthorizerLoginManager`` classes are now deprecated. Use
  `GlobusApp <https://globus-compute.readthedocs.io/en/stable/sdk.html#globusapps>`_
  objects from the Globus SDK instead.

- The ``Client.login_manager`` attribute is now deprecated.