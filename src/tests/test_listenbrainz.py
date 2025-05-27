import pytest
from unittest.mock import MagicMock
from util import LastFM, ListenBrainz, Track


@pytest.fixture
def mock_lbz(mocker):
    """
    Returns a mock ListenBrainz instance
    """
    mocker.patch.object(ListenBrainz, "_connect", return_value=MagicMock())
    mocker.patch.object(ListenBrainz, "all_loves", return_value=[])
    mocker.patch.object(ListenBrainz, "all_hates", return_value=[])
    lbz = ListenBrainz(token="fake_token", username="fake_username")
    # Reset the mocks so they can be used in tests
    ListenBrainz.all_loves.reset_mock()
    ListenBrainz.all_hates.reset_mock()
    return lbz


class TestFindMbidMatch:
    """Tests for _find_mbid_match()"""

    def test_success(self, mock_lbz):
        """
        Test that function correctly matches a track to the result dictionary
        """
        track = Track(title="Title", artist="Artist")
        track_search = [
            {"id": "1", "title": "Title", "artist-credit": [{"name": "Artist"}]}
        ]
        result = mock_lbz._find_mbid_match(track=track, track_search=track_search)
        assert result == "1"

    @pytest.mark.parametrize(
        "track_search",
        [
            [{"id": "1", "title": "Title", "artist-credit": None}],
            [{"title": "Title", "artist-credit": None}],
            [{"title": "Title"}],
        ],
    )
    def test_malformed_data(self, mock_lbz, track_search):
        """
        Test that malformed data doesn't raise an exception
        """
        track = Track(title="Title", artist="Artist")
        result = mock_lbz._find_mbid_match(track=track, track_search=track_search)
        assert result == None
