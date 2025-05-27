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
    return lbz


class TestHandleFeedback:
    """Tests for _handle_feedback()"""

    @pytest.fixture
    def mock_lbz_feedback(self, mocker):
        mocker.patch.object(ListenBrainz, "_connect", return_value=MagicMock())
        mocker.patch.object(ListenBrainz, "all_loves", return_value=[])
        mocker.patch.object(ListenBrainz, "all_hates", return_value=[])
        lbz = ListenBrainz(token="fake_token", username="fake_username")
        track1 = Track(title="1", artist="1")
        track2 = Track(title="2", artist="2")
        lbz.loves = [track1, track2]
        lbz.hates = [
            track1,
            track2,
        ]  # for testing purposes doesnt matter that these are the same
        lbz._get_track_mbid = MagicMock()
        lbz._get_track_mbid.return_value = "test-mbid-1234"
        lbz.client.submit_user_feedback = MagicMock()
        return lbz

    @pytest.mark.parametrize("feedback", ["love", "hate"])
    def test_success(self, mock_lbz_feedback, feedback):
        track = Track(title="3", artist="3")
        mock_lbz_feedback._handle_feedback(feedback, track)

        mock_lbz_feedback._get_track_mbid.assert_called_once_with(track)
        expected_feedback_value = 1 if feedback == "love" else -1
        mock_lbz_feedback.client.submit_user_feedback.assert_called_once_with(
            expected_feedback_value, "test-mbid-1234"
        )
class TestNew:
    """Tests for _new()"""

    def test_success(self, mock_lbz):
        track1 = Track(title="1", artist="1")
        track2 = Track(title="2", artist="2")
        mock_lbz.loves = [track1]
        result = mock_lbz._new(rating="love", track_list=[track1, track2])
        assert result == [track2]


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
