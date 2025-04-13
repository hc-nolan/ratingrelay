from pylast import LibreFMNetwork as LFM, md5

l = LFM(password_hash=md5("Nw!&DsS!5Ti*%rymNLfp"), username="chunned")

x = l.get_user("chunned").get_loved_tracks()

y = l.get_track("Radiohead", "We Suck Young Blood")

y.love()
