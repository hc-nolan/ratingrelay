import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ratingrelay.relay import check_list_match
from ratingrelay import setup_services
from ratingrelay.config import settings
from ratingrelay.reset import reset
from ratingrelay.track import Track
from ratingrelay.plex import PlexTrack


@pytest.fixture
def services():
    return setup_services(settings)


@pytest.fixture
def cleanup(services):
    """When tests are done, reset all services"""
    reset(services)


def assert_relay_success(expected: int, actual: list, source_getter, rating: str):
    """
    Assert relay success with 90% tolerance
    """
    try:
        assert len(actual) == expected
    except AssertionError as e:
        print("Wrong number of tracks. Checking which are missing.")
        print(f"Expected: {expected} - Got: {len(actual)}")

        source_tracks = source_getter()
        missing = find_missing(source_tracks, actual)

        print(f"Missing tracks ({len(missing)}):")
        for track in missing:
            match track:
                case PlexTrack():
                    print(f"{track.artist().title} - {track.title}")
                case Track():
                    print(f"{track.artist} - {track.title}")

        success_rate = (expected - len(missing)) / expected

        if success_rate < 0.9:
            raise e
        else:
            print(
                f"Success rate: {success_rate:.1%} - Within 90% threshold, considered successful test."
            )


def find_missing(source_list: list, target_list: list) -> list:
    return [
        source_track
        for source_track in source_list
        if not check_list_match(source_track, target_list)
    ]
