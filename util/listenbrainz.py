import musicbrainzngs as mbz
import liblistenbrainz as liblbz
import liblistenbrainz.errors as lbz_errors

# TODO: decide how to keep track of newly added loves

class ListenBrainz:
    def __init__(self, token: str | None, username: str | None):
        self.token = token
        self.username = username
        if not all(
            val is not None and
            val != "" 
            for val in (self.token, self.username)
        ):
            raise RuntimeError(
                "One or more ListenBrainz variables are missing.\n"
                "If you intended to use ListenBrainz, " 
                "make sure all environment variables are set."
            )
        self.client = self._connect()
        self.loves = self._get_loves()
        self.new_count = 0

    def _connect(self) -> liblbz.ListenBrainz:
        """
        Creates a connection to ListenBrainz
        :return None if any environment variables are missing,
        the ListenBrainz client otherwise
        """
        client = liblbz.ListenBrainz()
        client.set_auth_token(self.token)
        client.is_token_valid(self.token)
        return client

    def love(self, track: dict):
        """
        Track should have keys `title` and `artist`
        """
        mbid = self._get_track_mbid(track)
        if mbid and not self._already_loved(mbid):
            self.client.submit_user_feedback(1, mbid)
            self.new_count += 1

    def new_loves(self, track_list: list[dict]) -> list[dict]:
        """
        Compares the list of tracks from Plex to already loved
        ListenBrainz tracks; returns the tracks that have not yet been loved
        """
        # Tracks from Plex don't have the MBID, so create a new set without the MBID
        old_loves = {(title.lower(), artist.lower()) for mbid, title, artist in self.loves}
        new = [
            track for track in track_list
            if (track["title"].lower(), track["artist"].lower())  not in old_loves
        ]
        return new



    def _get_loves(self) -> set[tuple]:
        """
        Retrieve all tracks the user has already loved
        """
        all_loves = set()
        offset = 0
        count = 100
        while True:
            user_loves = self.client.get_user_feedback(
                username=self.username,
                score=1,
                count=count,
                offset=offset,
                metadata=True
            )
            user_loves = user_loves['feedback']
            for track in user_loves:
                mbid = track['recording_mbid']
                title = track['track_metadata']['track_name']
                artist = track['track_metadata']['artist_name']
                track_tuple = (mbid, title, artist)
                all_loves.add(track_tuple)
            if len(user_loves) < count:
                break   # No more feedback to fetch
            offset += count
        return all_loves

    def _get_track_mbid(self, track: dict) -> str | None:
        """
        Queries MusicBrainz and retrieves matching result
        """
        query = " ".join(str(val) for val in track.values())
        mbz.set_useragent(
            'RatingRelay',
            'v0.1',
            contact='https://codeberg.org/chunned/ratingrelay'
        )
        track_search = mbz.search_recordings(
            query=query,
            artist=track["artist"],
            recording=track["title"]
        )
        return self._find_mbid_match(track, track_search['recording-list'])

    @staticmethod
    def _find_mbid_match(
            track: dict,
            track_search: list[dict]
        ) -> str | None:
        """
        Attempts to find a matching MBID given a track dict
        and MusicBrainz search results
        """
        for result in track_search:
            # find matching title+artist pair
            try:
                track_title = track['title'].lower()
                candidate_title = result['title'].lower()

                track_artist = track['artist'].lower()
                candidate_artist = result['artist-credit'][0]['name'].lower()
                if track_title == candidate_title and track_artist == candidate_artist:
                    mbid = result['id']
                    return mbid
            except (IndexError, KeyError):
                return None
        return None

    def _already_loved(self, mbid: str) -> bool:
        """
        Check if user has already loved this MBID
        """
        return True if mbid in self.loves else False

