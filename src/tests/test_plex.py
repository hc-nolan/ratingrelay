import pytest
from unittest.mock import MagicMock
from util import Plex
from util.plex import similar_enough


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


class TestSimilarEnough:
    """Tests for similar_enough()"""

    def test_similarity_matching(self):
        """
        Test that tracks returned by Plex with a similar enough artist name
        are considered matches
        """
        result = similar_enough(a="0123456789", b="0123456")
        assert result is True
        result = similar_enough(a="0123456789", b="01234")
        assert result is False
