class TrackTuple:
    """
    Tuple class to represent tracks as (track_title, artist_name) tuples
    """
    def __init__(self, title: str, artist: str):
        self.title = title
        self.artist = artist

    def __iter__(self):
        return iter((self.title, self.artist))

    def __hash__(self):
        return hash((self.title, self.artist))

    def __eq__(self, other):
        if isinstance(other, TrackTuple):
            return (self.title, self.artist) == (other.title, other.artist)
        return False

    def __repr__(self):
        return f"TrackTuple(title={self.title!r}, artist={self.artist!r})"
