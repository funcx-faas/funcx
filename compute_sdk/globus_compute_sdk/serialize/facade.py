from __future__ import annotations

import importlib
import inspect
import logging
import textwrap
import typing as t
from enum import Enum

from globus_compute_sdk.errors import (
    DeserializationError,
    SerdeError,
    SerializationError,
)
from globus_compute_sdk.serialize.base import IDENTIFIER_LENGTH, SerializationStrategy
from globus_compute_sdk.serialize.concretes import (
    DEFAULT_STRATEGY_CODE,
    DEFAULT_STRATEGY_DATA,
    SELECTABLE_STRATEGIES,
    STRATEGIES_MAP,
)

logger = logging.getLogger(__name__)


class AllowlistWildcard(str, Enum):
    CODE = "globus_compute_sdk.*Code"
    DATA = "globus_compute_sdk.*Data"


DeserializerAllowlist = t.Iterable[
    t.Union[type[SerializationStrategy], str, AllowlistWildcard]
]


def assert_strategy_type_valid(
    strategy_type: type[SerializationStrategy], for_code: bool
) -> None:
    if strategy_type not in SELECTABLE_STRATEGIES:
        raise SerdeError(
            f"{strategy_type.__name__} is not a known serialization strategy"
            f" (must be one of {SELECTABLE_STRATEGIES})"
        )

    if strategy_type.for_code != for_code:
        etype = "code" if for_code else "data"
        gtype = "code" if strategy_type.for_code else "data"
        raise SerdeError(
            f"{strategy_type.__name__} is a {gtype} serialization strategy,"
            f" expected a {etype} strategy"
        )


def validate_strategy(
    strategy: SerializationStrategy, for_code: bool
) -> SerializationStrategy:
    assert_strategy_type_valid(type(strategy), for_code)
    return strategy


def parse_allowlist(
    unvalidated: DeserializerAllowlist | None,
) -> set[type[SerializationStrategy]]:
    if not unvalidated:
        return set()

    validated: set[type[SerializationStrategy]] = set()
    wildcards: set[AllowlistWildcard] = set()
    for value in unvalidated:
        try:
            AllowlistWildcard(value)
        except ValueError:
            pass
        else:
            # value is either an instance of AllowlistWildcard or the .value of
            # such an instance. mypy is not smart enough to know that
            wildcards.add(value)  # type: ignore
            continue

        resolved_strategy_class = None
        if isinstance(value, str):
            try:
                mod_name, class_name = value.rsplit(".", 1)
                mod = importlib.import_module(mod_name)
                resolved_strategy_class = getattr(mod, class_name)
            except Exception as e:
                raise SerdeError(f"`{value}` is not a valid path to a strategy") from e
        else:
            resolved_strategy_class = value

        if not inspect.isclass(resolved_strategy_class) or not issubclass(
            resolved_strategy_class, SerializationStrategy
        ):
            raise SerdeError(
                "Allowed deserializers must either be SerializationStrategies"
                f" or valid paths to them (got {value})"
            )

        assert_strategy_type_valid(
            resolved_strategy_class, resolved_strategy_class.for_code
        )
        validated.add(resolved_strategy_class)

    for wildcard in wildcards:
        for_code = wildcard == AllowlistWildcard.CODE
        if any(s.for_code == for_code for s in validated):
            raise SerdeError(
                f"Cannot mix '{wildcard}' with specific deserializers"
                f" (got {[unvalidated]})"
            )
        validated.update(s for s in SELECTABLE_STRATEGIES if s.for_code == for_code)

    if len({s.for_code for s in validated}) != 2:
        raise SerdeError(
            "Deserialization allowlists must contain at least one code and one data"
            f" deserializer/wildcard (got: {[unvalidated]})"
        )

    return validated


class ComputeSerializer:
    """Provides uniform interface to underlying serialization strategies"""

    def __init__(
        self,
        strategy_code: SerializationStrategy | None = None,
        strategy_data: SerializationStrategy | None = None,
        *,
        allowed_deserializer_types: DeserializerAllowlist | None = None,
    ):
        """Instantiate the appropriate classes"""

        self.code_serializer = validate_strategy(
            strategy_code or DEFAULT_STRATEGY_CODE, True
        )
        self.data_serializer = validate_strategy(
            strategy_data or DEFAULT_STRATEGY_DATA, False
        )

        self.allowed_deserializer_types = parse_allowlist(allowed_deserializer_types)

        self.strategies = {
            header: strategy_class()
            for header, strategy_class in STRATEGIES_MAP.items()
        }

    def serialize(self, data):
        if callable(data):
            stype, strategy = "Code", self.code_serializer
        else:
            stype, strategy = "Data", self.data_serializer

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

        self.assert_deserializer_allowed(strategy)

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

    def assert_deserializer_allowed(self, strategy: SerializationStrategy) -> None:
        if (
            not self.allowed_deserializer_types
            or type(strategy) in self.allowed_deserializer_types
        ):
            return

        stype = "Code" if strategy.for_code else "Data"
        payload_type = "function" if strategy.for_code else "arguments"
        allowed_names = ", ".join(
            sorted(t.__name__ for t in self.allowed_deserializer_types)
        )
        msg = (
            f"{stype} serializer {type(strategy).__name__} disabled by current"
            f" configuration. The current configuration requires the *{payload_type}*"
            f" to be serialized with one of the allowed classes:\n\n"
            f"    Allowed serializers: {allowed_names}"
            # note that there is (intentionally) no link to the documentation in this
            # error message - that's because the SDK appends its own hint to any
            # apparent serialization errors coming back from the endpoint. see https://github.com/globus/globus-compute/blob/112dc3ae9d9986f36618976f8806f0bd48702460/compute_sdk/globus_compute_sdk/errors/error_types.py#L74  # noqa: E501
        )

        raise DeserializationError(textwrap.indent(msg, " "))
