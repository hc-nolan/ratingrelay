import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ratingrelay import setup_services
from ratingrelay.config import settings


@pytest.fixture
def services():
    return setup_services(settings)
