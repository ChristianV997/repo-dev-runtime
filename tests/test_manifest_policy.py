import json

from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.manifest import detect_manifest, load_manifest


def test_detect_python_manifest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert load_manifest(tmp_path).test_command == ("python", "-m", "pytest", "-q")


def test_init_manifest_roundtrip(tmp_path):
    manifest = load_manifest(tmp_path, create_default=True)
    assert json.loads((tmp_path / ".dev-runtime" / "repository.json").read_text()) == manifest.to_dict()


def test_merge_is_always_denied():
    policy = RuntimePolicy()
    try:
        policy.authorize("merge")
    except PermissionError:
        pass
    else:
        raise AssertionError("merge must remain denied")
