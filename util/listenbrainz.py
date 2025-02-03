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

    def _get_loves(self) -> set[str]:
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
                metadata=None
            )
            user_loves = user_loves['feedback']
            mbids = {
                track['recording_mbid'] 
                for track in user_loves
            }
            all_loves.update(mbids)
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
            contact='https://github.com/chunned/ratingrelay'
        )
        track_search = mbz.search_recordings(
            query=query,
            artist=track["artist"],
            recording=track["title"]
        )
        return self._find_mbid_match(track, track_search['recording-list'])

    def _find_mbid_match(
            self,
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
                    mbid = result[0]['id']
                    return mbid
            except (IndexError, KeyError):
                return None
        return None

    def _already_loved(self, mbid: str) -> bool:
        """
        Check if user has already loved this MBID
        """
        return True if mbid in self.loves else False

