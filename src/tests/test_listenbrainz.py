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
