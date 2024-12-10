import typing as t
from abc import ABC, abstractmethod

from globus_compute_sdk.errors import DeserializationError

# 2 unique characters and a newline
IDENTIFIER_LENGTH = 3


class SerializationStrategy(ABC):
    """A SerializationStrategy is in charge of converting function source code or
    arguments into string data and back again.
    """

    def __init_subclass__(cls):
        super().__init_subclass__()
        if len(cls.identifier) != IDENTIFIER_LENGTH:
            raise ValueError(f"Identifiers must be {IDENTIFIER_LENGTH} characters long")

    identifier: t.ClassVar[str]
    for_code: t.ClassVar[bool]

    def chomp(self, payload: str) -> str:
        """If the payload starts with the identifier, return the remaining block

        Parameters
        ----------
        payload : str
            Payload blob
        """
        s_id, payload = payload.split("\n", 1)
        if (s_id + "\n") != self.identifier:
            raise DeserializationError(
                f"Buffer does not start with identifier:{self.identifier}"
            )
        return payload

    @abstractmethod
    def serialize(self, data):
        raise NotImplementedError("Concrete class did not implement serialize")

    @abstractmethod
    def deserialize(self, payload):
        raise NotImplementedError("Concrete class did not implement deserialize")
