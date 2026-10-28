import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
from estatesnapshot.model import Estate  # noqa: E402


@pytest.fixture
def prod(tmp_path):
    return Estate(fixtures.build(str(tmp_path / "prod"), "prod"))


@pytest.fixture
def snapshot_dirs(tmp_path):
    return {env: fixtures.build(str(tmp_path / env), env, registry="acrdev.azurecr.io") for env in ("dev", "prod")}
