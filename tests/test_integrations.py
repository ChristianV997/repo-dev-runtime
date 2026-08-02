from repo_dev_runtime.handoff import render_handoff
from repo_dev_runtime.integrations.obsidian import ObsidianHandoff


def test_obsidian_dry_run_does_not_write(tmp_path):
    handoff = ObsidianHandoff(tmp_path)
    path = handoff.write("session.md", "safe", dry_run=True)
    assert not path.exists()


def test_handoff_redacts_known_secret_names():
    content = render_handoff(repository="r", run_id="1", status="ok", next_action="review", tests={"OPENAI_API_KEY": "secret"})
    assert "OPENAI_API_KEY" not in content
    assert "[REDACTED]" in content
