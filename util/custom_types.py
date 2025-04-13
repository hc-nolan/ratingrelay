from typing import Optional, Any
from abc import ABC, abstractmethod


class Track:
    """
    Tuple class to represent tracks as (track_title, artist_name) tuples
    """

    def __init__(self, title: str, artist: str, mbid: Optional[str] = None):
        self.title = title
        self.artist = artist
        self.mbid = mbid

    def __iter__(self):
        return iter((self.title, self.artist))

    def __hash__(self):
        return hash((self.title, self.artist))

    def __eq__(self, other):
        if isinstance(other, Track):
            return (self.title, self.artist) == (other.title, other.artist)
        return False

    def __repr__(self):
        return f"Track(title={self.title!r}, artist={self.artist!r})"

    def __str__(self):
        return f"{self.artist} {self.title}"


class Service(ABC):
    """
    Abstract base class for any classes that interact with external services
    """

    def __init__(self, user: Optional[str], token: Optional[str]):
        self.username = user
        self.token = token
        self.client = self._connect()
        self.new_count = 0

    @abstractmethod
    def _connect(self) -> Any:
        """
        Connect to the external service and return a client.
        Must be implemented by subclasses.
        """
        pass

    @abstractmethod
    def love(self, track: Track):
        """
        Submit the provided Track as a Loved Track to the external service.
        Must be implemented by subclasses.
        """
        pass

    @abstractmethod
    def new_loves(self, track_list: list[Track]) -> list[Track]:
        """
        Compare track_list to the tracks currently loved on the external service.
        Return only the tracks from track_list which are not present on the external service.
        Must be implemented by subclasses.
        """  # noqa:E501
        pass

    @abstractmethod
    def all_loves(self) -> set[Track]:
        """
        Returns a set containing all the currently loved tracks on the external service.
        Must be implemented by subclasses.
        """  # noqa:E501
