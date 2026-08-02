import pytest

from repo_dev_runtime.contracts.models import DevTask, SensorRequest, canonical_json


def test_task_hash_is_deterministic():
    kwargs = dict(repository="repo", base_ref="HEAD", role="planner", prompt="inspect")
    assert DevTask(task_id="1", **kwargs).task_hash == DevTask(task_id="1", **kwargs).task_hash


def test_task_rejects_unknown_role():
    with pytest.raises(ValueError):
        DevTask(task_id="1", repository="repo", base_ref="HEAD", role="hacker", prompt="x").validate()


def test_nonfinite_json_is_rejected():
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_sensor_request_is_bounded():
    with pytest.raises(ValueError):
        SensorRequest.create(query="x", objective="y", max_records=0)
