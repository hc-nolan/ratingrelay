import pytest
from unittest.mock import MagicMock
from util import Plex


@pytest.fixture
def mock_plex(mocker) -> Plex:
    mocker.patch.object(Plex, "_verify_auth", return_value=MagicMock())
    mocker.patch.object(Plex, "_get_music_library", return_value=MagicMock())
    mocker.patch("util.env.get_required", return_value="ASDF")
    plex = Plex(
        url="http://localhost:32400",
        music_library="Music",
        love_threshold=10,
        hate_threshold=0,
    )
    return plex
