from __future__ import annotations

import logging
import typing as t

from globus_compute_sdk.errors import DeserializationError, SerializationError
from globus_compute_sdk.serialize.base import IDENTIFIER_LENGTH, SerializationStrategy
from globus_compute_sdk.serialize.concretes import (
    DEFAULT_STRATEGY_CODE,
    DEFAULT_STRATEGY_DATA,
    SELECTABLE_STRATEGIES,
    STRATEGIES_MAP,
)

logger = logging.getLogger(__name__)


class ComputeSerializer:
    """Provides uniform interface to underlying serialization strategies"""

    def __init__(
        self,
        strategy_code: SerializationStrategy | None = None,
        strategy_data: SerializationStrategy | None = None,
    ):
        """Instantiate the appropriate classes"""

        def validate(strategy: SerializationStrategy) -> SerializationStrategy:
            if type(strategy) not in SELECTABLE_STRATEGIES:
                raise SerializationError(
                    f"{strategy} is not a known serialization strategy "
                    f"(must be one of {SELECTABLE_STRATEGIES})"
                )

            return strategy

        self.strategy_code = (
            validate(strategy_code) if strategy_code else DEFAULT_STRATEGY_CODE
        )
        self.strategy_data = (
            validate(strategy_data) if strategy_data else DEFAULT_STRATEGY_DATA
        )

        self.strategies = {
            header: strategy_class()
            for header, strategy_class in STRATEGIES_MAP.items()
        }

    def serialize(self, data):
        if callable(data):
            stype, strategy = "Code", self.strategy_code
        else:
            stype, strategy = "Data", self.strategy_data

        try:
            return strategy.serialize(data)
        except Exception as e:
            err_msg = f"{stype} serialization strategy {type(strategy).__name__} failed"
            raise SerializationError(err_msg) from e

    def deserialize(self, payload):
        """
        Parameters
        ----------
        payload : str
           Payload object to be deserialized

        """
        header = payload[:IDENTIFIER_LENGTH]
        strategy = self.strategies.get(header)

        if not strategy:
            raise DeserializationError(f"Invalid header: {header} in data payload")

        return strategy.deserialize(payload)

    @staticmethod
    def pack_buffers(buffers):
        """
        Parameters
        ----------
        buffers : list of \n terminated strings
        """
        packed = ""
        for buf in buffers:
            s_length = str(len(buf)) + "\n"
            packed += s_length + buf

        return packed

    @staticmethod
    def unpack_buffers(packed_buffer):
        """
        Parameters
        ----------
        packed_buffer : packed buffer as string
        """
        unpacked = []
        while packed_buffer:
            s_length, buf = packed_buffer.split("\n", 1)
            i_length = int(s_length)
            current, packed_buffer = buf[:i_length], buf[i_length:]
            unpacked.extend([current])

        return unpacked

    def unpack_and_deserialize(self, packed_buffer):
        """Unpacks a packed buffer and returns the deserialized contents
        Parameters
        ----------
        packed_buffer : packed buffer as string
        """
        unpacked = []
        while packed_buffer:
            s_length, buf = packed_buffer.split("\n", 1)
            i_length = int(s_length)
            current, packed_buffer = buf[:i_length], buf[i_length:]
            deserialized = self.deserialize(current)
            unpacked.extend([deserialized])

        assert len(unpacked) == 3, "Unpack expects 3 buffers, got {}".format(
            len(unpacked)
        )

        return unpacked

    def check_strategies(self, function: t.Callable, *args, **kwargs):
        """
        Check that the given function, args, and kwargs are compatible with this
        ComputeSerializer's serialization strategies.

        Uses the same interface as Executor.submit. Returns a list containing the
        function, args, and kwargs after going through serialization and
        deserialization.
        """

        try:
            ser_fn = self.serialize(function)
            ser_args = self.serialize(args)
            ser_kwargs = self.serialize(kwargs)

            packed = self.pack_buffers([ser_fn, ser_args, ser_kwargs])
        except Exception as e:
            raise SerializationError("check_strategies failed to serialize") from e

        try:
            return self.unpack_and_deserialize(packed)
        except Exception as e:
            raise DeserializationError("check_strategies failed to deserialize") from e
