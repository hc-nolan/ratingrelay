import pytest
from ratingrelay.relay import check_list_match
from ratingrelay.track import Track


@pytest.mark.parametrize(
    "test_track,expected",
    [
        (
            Track(title="test1", artist="test1", mbid=None, track_mbid=None),
            Track(title="test1", artist="test1", mbid=None, track_mbid=None),
        ),
        (
            Track(title="test2", artist="test2", mbid=None, track_mbid=None),
            ("test2", "test2"),
        ),
        (
            Track(title="TEST3", artist="TEST3", mbid=None, track_mbid=None),
            Track(title="test3", artist="test3", mbid=None, track_mbid=None),
        ),
        (
            Track(title="tes't4", artist="test4", mbid=None, track_mbid=None),
            Track(title="tes’t4", artist="test4", mbid=None, track_mbid=None),
        ),
        (
            Track(title="test & test5", artist="test5", mbid=None, track_mbid=None),
            Track(title="test and test5", artist="test5", mbid=None, track_mbid=None),
        ),
    ],
)
def test_check_list_match(test_track, expected):
    """
    Test that check_list_match() succeeds at finding matches in the target list
    """
    target_list = [
        "",
        Track(title="test1", artist="test1", mbid=None, track_mbid=None),
        ("test2", "test2"),
        Track(title="test3", artist="test3", mbid=None, track_mbid=None),
        Track(title="tes’t4", artist="test4", mbid=None, track_mbid=None),
        Track(title="test and test5", artist="test5", mbid=None, track_mbid=None),
    ]

    assert check_list_match(test_track, target_list) is not False
