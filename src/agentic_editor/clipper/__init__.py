"""The Kick clipper (feature "kick_clipper"): short clips cut from Kick streams.

A hunt reads one stretch of a stream (a past one, or the one live now): its chat, the
clips its viewers made and its sound (moments.py), picks the moments that made people
react, and cuts each one's 15-30 s clip from the stream (hunt.py). Every clip then becomes
an ordinary Job with the chosen preset, kept whole (the preset dresses it), so captions,
titles, folders, posting and the rest work as for any other video. Followed streamers are
clipped while they are live, a stretch at a time (hunt.check_live).
"""
