import pytest
from ratingrelay import to_Track_list, Relay
from util import Track
from plexapi.audio import Track as PlexTrack


def mock_track(mocker, title: str, artist: str):
    track = mocker.MagicMock()
    track.title = title
    artist_mock = mocker.MagicMock()
    artist_mock.title = artist
    track.artist.return_value = artist_mock
    return track


class TestToTrackList:
    """Tests for to_Track_list"""

    def test_success(self, mocker):
        plex_track_list = [mock_track(mocker, "1", "2"), mock_track(mocker, "3", "4")]
        expected = [Track(title="1", artist="2"), Track(title="3", artist="4")]
        assert to_Track_list(plex_track_list) == expected


class TestRelay:
    """Tests for Relay"""

    def test_init_requires_service(self, mocker):
        plex = mocker.MagicMock()
        with pytest.raises(RuntimeError):
            relay = Relay(plex=plex, bidirectional=False, lfm=None, lbz=None)
