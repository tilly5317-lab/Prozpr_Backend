"""The backend dir must be resolved from the package, not the process cwd."""

import importlib

from app.core import config


def test_backend_dir_is_repo_root_not_app_package():
    # config.py lives at <root>/app/core/config.py
    assert (config._backend_dir / "app" / "core" / "config.py").exists()
    assert config._backend_dir.name != "app"


def test_backend_dir_contains_requirements():
    assert (config._backend_dir / "requirements.txt").exists()


# ``Settings.DEPLOY_ENV`` is a class attribute, so it binds when the module is
# imported — monkeypatching the env after import cannot change it. Reload the
# module inside the patched environment, and reload again to restore.
def _deploy_env_after_reload() -> str:
    importlib.reload(config)
    return config.get_settings().DEPLOY_ENV


def test_deploy_env_defaults_to_development(monkeypatch):
    monkeypatch.delenv("DEPLOY_ENV", raising=False)
    try:
        assert _deploy_env_after_reload() == "development"
    finally:
        importlib.reload(config)


def test_deploy_env_reads_the_environment(monkeypatch):
    monkeypatch.setenv("DEPLOY_ENV", "production")
    try:
        assert _deploy_env_after_reload() == "production"
    finally:
        monkeypatch.delenv("DEPLOY_ENV", raising=False)
        importlib.reload(config)
