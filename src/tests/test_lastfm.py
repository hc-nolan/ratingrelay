import pytest
from unittest.mock import MagicMock
from util import LastFM, Track


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


class TestNewLoves:
    """Tests for new_loves()"""

    def test_success(self, mock_lastfm, mocker):
        track1 = Track(title="1", artist="1")
        track2 = Track(title="2", artist="2")

        mocker.patch.object(LastFM, "all_loves", return_value=[track1])
        result = mock_lastfm.new_loves(track_list=[track1, track2])
        assert result == [track2]
