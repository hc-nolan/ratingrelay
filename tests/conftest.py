import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ratingrelay import setup_services
from ratingrelay.config import settings
from ratingrelay.reset import reset


@pytest.fixture
def services():
    return setup_services(settings)


@pytest.fixture
def cleanup(services):
    """When tests are done, reset all services"""
    reset(services)
