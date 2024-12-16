from __future__ import annotations

import codecs
import inspect
import json
import logging
import pickle
import typing as t
from collections import OrderedDict

import dill
from globus_compute_sdk.errors import DeserializationError, SerializationError
from globus_compute_sdk.serialize.base import SerializationStrategy

logger = logging.getLogger(__name__)


class DillDataBase64(SerializationStrategy):
    identifier = "00\n"
    for_code = False

    def __init__(self):
        super().__init__()

    def serialize(self, data) -> str:
        x = codecs.encode(dill.dumps(data), "base64").decode()
        return self.identifier + x

    def deserialize(self, payload: str):
        chomped = self.chomp(payload)
        data = dill.loads(codecs.decode(chomped.encode(), "base64"))
        return data


class JSONData(SerializationStrategy):
    identifier = "11\n"
    for_code = False

    def __init__(self):
        super().__init__()

    def serialize(self, data) -> str:
        return self.identifier + json.dumps(data)

    def deserialize(self, payload: str):
        chomped = self.chomp(payload)
        data = json.loads(chomped)
        return data


class DillCodeSource(SerializationStrategy):
    """This strategy uses dill's getsource method to extract the function body and
    then serializes it.

    Code from interpreter/main        : Yes
    Code from notebooks               : No
    Works with mismatching py versions: Yes
    Decorated fns                     : No
    """

    identifier = "04\n"
    for_code = True

    def __init__(self):
        super().__init__()

    def serialize(self, data) -> str:
        name = data.__name__
        body = dill.source.getsource(data, lstrip=True)
        x = codecs.encode(dill.dumps((name, body)), "base64").decode()
        return self.identifier + x

    def deserialize(self, payload: str):
        chomped = self.chomp(payload)
        name, body = dill.loads(codecs.decode(chomped.encode(), "base64"))
        exec_ns: dict = {}
        exec(body, exec_ns)
        return exec_ns[name]


class DillCodeTextInspect(SerializationStrategy):
    """This strategy uses the inspect library to extract the function body and
    then serializes it.

    Code from interpreter/main        : ?
    Code from notebooks               : Yes
    Works with mismatching py versions: Yes
    Decorated fns                     : No
    """

    identifier = "03\n"
    for_code = True

    def __init__(self):
        super().__init__()

    def serialize(self, data) -> str:
        name = data.__name__
        body = inspect.getsource(data)
        x = codecs.encode(dill.dumps((name, body)), "base64").decode()
        return self.identifier + x

    def deserialize(self, payload: str):
        chomped = self.chomp(payload)
        name, body = dill.loads(codecs.decode(chomped.encode(), "base64"))
        exec_ns: dict = {}
        exec(body, exec_ns)
        return exec_ns[name]


class PickleCode(SerializationStrategy):
    """
    Deprecated in favor of just using dill, but deserialization is
    supported for legacy functions
    Code from interpreter/main        : No
    Code from notebooks               : Yes
    Works with mismatching py versions: No
    Decorated fns                     : Yes
    """

    identifier = "02\n"
    for_code = True

    def __init__(self):
        super().__init__()

    def serialize(self, data) -> str:
        raise NotImplementedError("Pickle serialization is no longer supported")
        # x = codecs.encode(pickle.dumps(data), "base64").decode()
        # return self.identifier + x

    def deserialize(self, payload: str):
        chomped = self.chomp(payload)
        data = pickle.loads(codecs.decode(chomped.encode(), "base64"))
        return data


class DillCode(SerializationStrategy):
    """This strategy uses dill to directly serialize a function.

    Code from interpreter/main        : No
    Code from notebooks               : Yes
    Works with mismatching py versions: No
    Decorated fns                     : Yes
    """

    identifier = "01\n"
    for_code = True

    def __init__(self):
        super().__init__()

    def serialize(self, data) -> str:
        x = codecs.encode(dill.dumps(data), "base64").decode()
        return self.identifier + x

    def deserialize(self, payload: str):
        chomped = self.chomp(payload)
        function = dill.loads(codecs.decode(chomped.encode(), "base64"))
        return function


class CombinedCode(SerializationStrategy):
    """This strategy uses multiple strategies to serialize a function

    # These should be a superset of the strategies
    Code from interpreter/main        : Yes
    Code from notebooks               : Yes
    Works with mismatching py versions: Yes
    Decorated fns                     : Yes
    """

    identifier = "10\n"
    for_code = True

    # Functions are serialized using the following strategies and the resulting encoded
    # versions are stored as chunks.  Allows redundancy if one of the strategies fails
    # on deserialization (undetectable during previous serialization attempts).
    # Note that, of the 3 categories of failures:
    #   1) Failure on serialization
    #   2) Failure on deserialization
    #   3) Failure on running the strategy
    # We have agreed that we can only assist with 1) and 2).
    _chunk_strategies = OrderedDict(
        [
            (DillCodeSource.identifier, DillCodeSource),
            (DillCode.identifier, DillCode),
            (DillCodeTextInspect.identifier, DillCodeTextInspect),
        ]
    )

    _separator = ":"

    def get_multiple_payloads(self, payload: str) -> tuple[tuple[str, str], ...]:
        parts: list[str] = self.chomp(payload).split(CombinedCode._separator)
        assert len(parts) > 0
        ai = iter(parts)

        # ie. ((04\n', 'gACQ...'), ('01\n', 'sAAbk2...'), ...)
        return tuple(zip(ai, ai))

    def serialize(self, data) -> str:
        chunks = []
        for id, cls in CombinedCode._chunk_strategies.items():
            strategy = cls()
            try:
                serialized = strategy.serialize(data)
            except Exception:
                # Ignore and move on to next
                continue
            id_length = len(id)
            chunks.append(serialized[:id_length])
            chunks.append(serialized[id_length:])

        if len(chunks) == 0:
            raise SerializationError("No serialization methods were successful")

        return self.identifier + CombinedCode._separator.join(chunks)

    def deserialize(self, payload: str, variation: int = 0):
        """
        If a variation is specified, try that strategy/payload if
        present.
        If variation is not specified, loop and return the first
        one that deserializes successfully.  If none works, raise

        Note variation is 1-indexed (ie. 2 means 2nd strategy), not
        the .identifier/serial_id of the strategy like 04\n or 01\n
        """
        count = 0
        for serial_id, encoded_func in self.get_multiple_payloads(payload):
            count += 1
            if variation == 0 or count == variation:
                if serial_id in CombinedCode._chunk_strategies:
                    strategy = CombinedCode._chunk_strategies[serial_id]()
                    try:
                        return strategy.deserialize(serial_id + encoded_func)
                    except Exception:
                        if variation > 0:
                            # Only raise if this variation was specified and fails
                            #  otherwise, just try next available
                            raise DeserializationError(
                                f"Failed to deserialize using variation #{count}"
                            )
                else:
                    raise DeserializationError(f"Invalid serialization id {serial_id}")
        raise DeserializationError(f"Deserialization failed after {count} tries")


STRATEGIES_MAP: dict[str, t.Type[SerializationStrategy]] = {
    DillDataBase64.identifier: DillDataBase64,
    JSONData.identifier: JSONData,
    DillCodeSource.identifier: DillCodeSource,
    DillCode.identifier: DillCode,
    DillCodeTextInspect.identifier: DillCodeTextInspect,
    PickleCode.identifier: PickleCode,
    CombinedCode.identifier: CombinedCode,
}

SELECTABLE_STRATEGIES: list[type[SerializationStrategy]] = [
    DillDataBase64,
    JSONData,
    DillCodeSource,
    DillCode,
    DillCodeTextInspect,
    CombinedCode,
]

DEFAULT_STRATEGY_CODE = DillCode()
DEFAULT_STRATEGY_DATA = DillDataBase64()
