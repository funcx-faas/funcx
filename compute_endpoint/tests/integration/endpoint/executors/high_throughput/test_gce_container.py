import uuid

import pytest
from globus_compute_endpoint.engines import GlobusComputeEngine


def platinfo():
    import platform
    import sys

    return platform.uname(), sys.version_info


def test_docker(tmp_path):
    gce = GlobusComputeEngine(
        address="127.0.0.1",
        label="GCE_TEST",
        container_type="docker",
        container_uri="funcx/kube-endpoint:main-3.10",
        container_cmd_options="--FABRICATED",
    )
    gce.start(endpoint_id=uuid.uuid4(), run_dir="/tmp")
    container_launch_cmd = gce.executor.launch_cmd
    expected = (
        "docker run --FABRICATED -v /tmp:/tmp -t "
        "funcx/kube-endpoint:main-3.10 process_worker_pool.py"
    )
    assert container_launch_cmd.startswith(expected)

    gce.shutdown()


def test_apptainer(tmp_path):
    gce = GlobusComputeEngine(
        address="127.0.0.1",
        label="GCE_TEST",
        container_type="apptainer",
        container_uri="APPTAINER_PATH",
        container_cmd_options="--FABRICATED",
    )
    gce.start(endpoint_id=uuid.uuid4(), run_dir="/tmp")
    container_launch_cmd = gce.executor.launch_cmd
    expected = "apptainer run --FABRICATED APPTAINER_PATH process_worker_pool.py"
    assert container_launch_cmd.startswith(expected)

    gce.shutdown()


def test_singularity(tmp_path):
    gce = GlobusComputeEngine(
        address="127.0.0.1",
        max_workers=1,
        label="GCE_TEST",
        container_type="singularity",
        container_uri="/home/yadunand/kube-endpoint.py3.9.sif",
        container_cmd_options="",
    )
    gce.start(endpoint_id=uuid.uuid4(), run_dir="/tmp")
    container_launch_cmd = gce.executor.launch_cmd
    expected = (
        "singularity run /home/yadunand/kube-endpoint.py3.9.sif"
        " process_worker_pool.py"
    )
    assert container_launch_cmd.startswith(expected)

    gce.shutdown()


def test_custom_missing_options(tmp_path):
    gce = GlobusComputeEngine(
        address="127.0.0.1",
        max_workers=1,
        label="GCE_TEST",
        container_type="custom",
    )
    with pytest.raises(AssertionError):
        gce.start(endpoint_id=uuid.uuid4(), run_dir="/tmp")


def test_custom(tmp_path):
    gce = GlobusComputeEngine(
        address="127.0.0.1",
        max_workers=1,
        label="GCE_TEST",
        container_type="custom",
        container_cmd_options="FOO {EXECUTOR_RUNDIR} {EXECUTOR_LAUNCH_CMD}",
    )

    gce.start(endpoint_id=uuid.uuid4(), run_dir="/tmp")

    container_launch_cmd = gce.executor.launch_cmd
    expected = f"FOO {gce.run_dir} process_worker_pool.py"
    assert container_launch_cmd.startswith(expected)

    gce.shutdown()
