"""Regression coverage for the one-time StateForge project bootstrap."""

from stateforge_dpt.bootstrap import REPOSITORIES


def test_bootstrap_uses_final_provider_repository_identity() -> None:
    """Keep new StateForge projects off the retired Provider repository slug."""
    by_alias = {alias: directory for alias, directory, _ in REPOSITORIES}

    assert by_alias["provider"] == "stateforge-provider-python"
    assert "stateforge-provider-implemntation" not in by_alias.values()
