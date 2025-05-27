import pytest
from unittest.mock import MagicMock
from util import LastFM


@pytest.fixture
def mock_lastfm(mocker):
    """
    Returns a mock LastFM instance
    """
    mocker.patch.object(LastFM, "_connect", return_value=MagicMock())
    lfm = LastFM(
        username="username", password="password", token="token", secret="secret"
    )
    return lfm
