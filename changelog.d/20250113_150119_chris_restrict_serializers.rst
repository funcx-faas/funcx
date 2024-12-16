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
