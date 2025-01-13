New Functionality
^^^^^^^^^^^^^^^^^

- The ``ComputeSerializer`` can now be told to only deserialize payloads that were
  serialized with specific serialization strategies. For example:

  .. code-block:: python

    import os
    from globus_compute_sdk.serialize import ComputeSerializer, JSONData


    class MaliciousPayload():
        def __reduce__(self):
            # this method returns a 2-tuple (callable, arguments) that dill calls to reconstruct the object
            return os.system, ("<your favorite arbitrary code execution script>",)

    evil_serializer = ComputeSerializer()  # uses DillDataBase64 by default
    payload = evil_serializer.serialize(MaliciousPayload())


    safe_deserializer = ComputeSerializer(
        allowed_data_deserializer_types=[JSONData]
    )
    safe_deserializer.deserialize(payload)
    # globus_compute_sdk.errors.error_types.DeserializationError: Deserialization failed:
    #
    #   Data serializer DillDataBase64 is not allowed in this ComputeSerializer.
    #   The only allowed data serializer is JSONData.
    #
    #   (Hint: reserialize the arguments with JSONData and try again.)

- Compute Endpoints can be configured to only deserialize and execute submissions that
  were serialized with specific serialization strategies. For example, with the
  following config:

  .. code-block:: yaml

    engine:
        allowed_code_serializers:
            - globus_compute_sdk.serialize.DillCodeSource
            - globus_compute_sdk.serialize.DillCodeTextInspect
        allowed_data_serializers:
            - globus_compute_sdk.serialize.JSONData
        type: ThreadPoolEngine

  any submissions that used the default serialization strategies (``DillCode``,
  ``DillDataBase64``) would be rejected, and users would be informed to use one of the
  allowed strategies.
