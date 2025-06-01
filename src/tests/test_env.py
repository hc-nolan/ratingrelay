import pytest
from pathlib import Path
from util.env import write_var

TEST_ENV_FILE = "test.env"


@pytest.fixture
def mock_env_file(mocker) -> Path:
    """
    Returns pathlib reference to empty env file, and patches
    util.env.get_env_file to point to the same file
    """
    env_file = Path(TEST_ENV_FILE)
    if env_file.exists():
        env_file.unlink()

    env_file.touch()
    mocker.patch("util.env.get_env_file", return_value=env_file)
    yield env_file
    # cleanup; delete the file
    env_file.unlink()


class TestWriteVar:
    """Tests for write_var()"""

    def test_success(self, mock_env_file):
        """Test that variable is successfully written to empty file"""
        assert mock_env_file.stat().st_size == 0
        write_var("TEST", "VALUE")
        with open(mock_env_file, "r") as f:
            envfile_content = f.read()

        assert "TEST=VALUE" in envfile_content

    def test_proper_ordering(self, mock_env_file):
        """Test that variables are written on new lines"""
        assert mock_env_file.stat().st_size == 0
        write_var("TEST", "VALUE")
        write_var("TEST2", "VALUE")
        with open(mock_env_file, "r") as f:
            envfile_content = f.readlines()

        assert "TEST2=VALUE\n" == envfile_content[1]

    def test_newline_handling(self, mock_env_file):
        """
        Test to ensure that, if a user manually
        adds a value to the env file and does not add a newline char,
        the function will write the next variable to a new line
        """
        assert mock_env_file.stat().st_size == 0
        with open(mock_env_file, "w") as f:
            f.write("TEST=VALUE")

        write_var("TEST2", "VALUE")
        with open(mock_env_file, "r") as f:
            envfile_content = f.readlines()

        assert envfile_content == ["TEST=VALUE\n", "TEST2=VALUE\n"]

    def test_update(self, mock_env_file):
        """Test that the function successfully updates existing values"""
        assert mock_env_file.stat().st_size == 0
        write_var("TEST", "VALUE")
        with open(mock_env_file, "r") as f:
            envfile_content = f.readlines()
        assert envfile_content[0] == "TEST=VALUE\n"
        write_var("TEST", "NEWVALUE")
        with open(mock_env_file, "r") as f:
            envfile_content = f.readlines()
        assert envfile_content[0] == "TEST=NEWVALUE\n"
