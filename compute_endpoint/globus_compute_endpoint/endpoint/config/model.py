from __future__ import annotations

import typing as t
import uuid
from types import ModuleType

from globus_compute_common.pydantic_v1 import (
    BaseModel,
    FilePath,
    root_validator,
    validator,
)
from globus_compute_endpoint import engines, strategies
from globus_compute_endpoint.endpoint.config.pam import PamConfiguration
from parsl import addresses as parsl_addresses
from parsl import channels as parsl_channels
from parsl import launchers as parsl_launchers
from parsl import providers as parsl_providers


def _validate_import(field: str, package: ModuleType):
    def inner(cls, module: str):
        cls = getattr(package, module, None)
        if cls is None:
            raise ValueError(f"{module} could not be found")
        return cls

    return validator(field, allow_reuse=True)(inner)


def _validate_params(field: str):
    def inner(cls, model: t.Optional[BaseModel]):
        if not isinstance(model, BaseModel):
            return model

        fields = model.dict(exclude_none=True)
        cls = fields.pop("type")
        try:
            return cls(**fields)
        except Exception as err:
            raise ValueError(str(err)) from err

    return validator(field, allow_reuse=True)(inner)


class BaseConfigModel(BaseModel):
    class Config:
        extra = "allow"


class AddressModel(BaseConfigModel):
    type: str

    _validate_type = _validate_import("type", parsl_addresses)


class StrategyModel(BaseConfigModel):
    type: str

    _validate_type = _validate_import("type", strategies)


class LauncherModel(BaseConfigModel):
    type: str

    _validate_type = _validate_import("type", parsl_launchers)


class ChannelModel(BaseConfigModel):
    type: str

    _validate_type = _validate_import("type", parsl_channels)


class ProviderModel(BaseConfigModel):
    type: str
    channel: t.Optional[ChannelModel]
    launcher: t.Optional[LauncherModel]

    _validate_type = _validate_import("type", parsl_providers)
    _validate_channel = _validate_params("channel")
    _validate_launcher = _validate_params("launcher")


class EngineModel(BaseConfigModel):
    type: str = "HighThroughputEngine"
    provider: t.Optional[ProviderModel]
    strategy: t.Optional[t.Union[str, StrategyModel]]
    address: t.Optional[t.Union[str, AddressModel]]
    worker_ports: t.Optional[t.Tuple[int, int]]
    worker_port_range: t.Optional[t.Tuple[int, int]]
    interchange_port_range: t.Optional[t.Tuple[int, int]]
    max_retries_on_system_failure: t.Optional[int]
    allowed_code_serializers: t.Optional[t.List[str]]
    allowed_data_serializers: t.Optional[t.List[str]]

    _validate_type = _validate_import("type", engines)
    _validate_provider = _validate_params("provider")
    _validate_strategy = _validate_params("strategy")
    _validate_address = _validate_params("address")

    class Config:
        extra = "allow"
        validate_all = True

    @root_validator(pre=True)
    @classmethod
    def _validate_engine_strategy(cls, values: dict):
        engine_type = values.get("type")
        strategy = values.get("strategy")
        if engine_type == "GlobusComputeEngine" and isinstance(strategy, dict):
            raise ValueError(
                "strategy as an object is incompatible with the GlobusComputeEngine."
                " Please update to the string value 'simple' or null.\n"
                "  E.g.,\n"
                "  strategy: simple\n"
            )
        elif engine_type == "HighThroughputEngine" and isinstance(strategy, str):
            raise ValueError(
                "strategy as a string is incompatible with the HighThroughputEngine."
                " Please update to an object or null.\n"
                "  E.g.,\n"
                "  strategy:\n"
                "      type: SimpleStrategy\n"
                "      max_idletime: 300\n"
            )
        return values

    @root_validator(pre=True)
    @classmethod
    def _validate_provider_container_compatibility(cls, values: dict):
        provider_type = values.get("provider", {}).get("type")
        if provider_type in (
            "AWSProvider",
            "GoogleCloudProvider",
            "KubernetesProvider",
        ):
            if values.get("container_uri"):
                raise ValueError(
                    f"The 'container_uri' field is not compatible with {provider_type}"
                    " because this provider manages containers internally. For more"
                    f" information on how to configure {provider_type}, please refer to"
                    f" Parsl documentation: https://parsl.readthedocs.io/en/stable/stubs/parsl.providers.{provider_type}.html"  # noqa"
                )
        return values


class BaseEndpointConfigModel(BaseModel):
    multi_user: t.Optional[bool]
    display_name: t.Optional[str]
    allowed_functions: t.Optional[t.List[uuid.UUID]]
    authentication_policy: t.Optional[uuid.UUID]
    subscription_id: t.Optional[uuid.UUID]
    amqp_port: t.Optional[int]
    heartbeat_period: t.Optional[int]
    environment: t.Optional[str]
    local_compute_services: t.Optional[bool]
    debug: t.Optional[bool]

    class Config:
        extra = "forbid"


class UserEndpointConfigModel(BaseEndpointConfigModel):
    engine: EngineModel
    heartbeat_threshold: t.Optional[int]
    idle_heartbeats_soft: t.Optional[int]
    idle_heartbeats_hard: t.Optional[int]
    detach_endpoint: t.Optional[bool]
    endpoint_setup: t.Optional[str]
    endpoint_teardown: t.Optional[str]
    log_dir: t.Optional[str]
    stdout: t.Optional[str]
    stderr: t.Optional[str]

    _validate_engine = _validate_params("engine")

    def dict(self, *args, **kwargs):
        # Slight modification is needed here since we still
        # store the engine/executor in a list named executors
        ret = super().dict(*args, **kwargs)

        engine = ret.pop("engine", None)
        ret["executors"] = [engine] if engine else None
        return ret


class ManagerEndpointConfigModel(BaseEndpointConfigModel):
    public: t.Optional[bool]
    identity_mapping_config_path: t.Optional[FilePath]
    force_mu_allow_same_user: t.Optional[bool]
    mu_child_ep_grace_period_s: t.Optional[float]
    pam: t.Optional[PamConfiguration]
