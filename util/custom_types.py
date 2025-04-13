class Track:
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
        if isinstance(other, Track):
            return (self.title, self.artist) == (other.title, other.artist)
        return False

    def __repr__(self):
        return f"Track(title={self.title!r}, artist={self.artist!r})"

    def __str__(self):
        return f"{self.artist} {self.title}"
