# Kick Clipper

The Clipper tab (feature `kick_clipper`, on for `siddhant` and `employee` until the setting
`feature:kick_clipper` says otherwise) cuts 15-30 second clips from Kick streams, from the
moments that made the chat react, and makes each one with a preset like any other video.

## Using it

- **Find clips**: paste a stream's link (`kick.com/<streamer>/videos/<id>`) or the name of a
  streamer who is live now, optionally a stretch (`From 1:00:00 To 1:03:00`), how many clips
  at most, the preset and a folder. The search button lists the streamer's streams to pick
  from. Without a stretch the whole stream is read (a live one: what was streamed so far).
- **Streamers**: the streamers an account clips, each with its preset, folder and "Clip live
  streams by itself". With that on, every 10 minutes of a live stream is read once it is
  streamed (Kick's video runs about 45 s behind) and its strong moments are clipped, up to
  "live clips an hour"; when the stream ends, what is left is read too.
- The clips are jobs: they show on the Jobs page, go to the folder on the piles ("Kick
  <streamer>" unless another is named), and get the preset's text, captions and post caption.
  A download-only preset isn't offered (a clip is cut).

## Facecam layout (presets.Facecam, the "Kick clips (facecam on top)" preset)

The streamer's camera in a panel at the top (760 px), the game under it, both the frame's
width; the one-liner sits where they meet. Per clip:

- **The camera** (analysis/facecam.py): 36 frames read at 960 px; the streamer is the face
  (YuNet) in one place in at least half of them (faces in a game move and come and go).
  The camera box is the rectangle round it whose sides are lasting straight edges (the
  overlay's border, or the step from the camera to what is beside it: other colours or other
  motion) or the frame's own edge, scored together with how a webcam frames a face. When in
  doubt a line inside the room wins over the frame's edge: the panel then shows less of the
  room, never part of the game. A set `facecam.box` (shares of the picture) is used instead.
- **No box**: a face that fills much of the picture is a stream that is all camera (just
  chatting, IRL), and with no face at all there is no camera; both follow the face across the
  whole frame (framing mode face), the text staying where the preset puts it. No camera found
  is noted as a doubt on the video.
- **The cuts** (render/facecam.py): the camera panel shows as much of the box as its shape
  holds, round the face; a camera far narrower or wider than the panel (filling it would cut
  away more than 30%) is shown whole over a blurred copy of itself. The game panel takes the
  picture's whole height across the middle (`facecam.focus`), moved off the camera.
- Text over the video can have an outline (`text_blocks[].outline`). The preview draws a
  webcam and a game stand-in in the same rows and says when text covers where the face is.

Measured on six Kick streams (xQc, Adin Ross, Westcol, Sneako, IcePoseidon, Amouranth): the
camera found in all five streams that have one (Westcol's box a little inside its border), Amouranth's IRL
stream followed as all camera; finding it takes about 4 s a clip on the PC.

## How a moment is found (clipper/moments.py)

Second by second over the stretch, each signal against what is usual in the 5 minutes around
it (a busy chat or a loud streamer is not a moment; a jump is):

- **chat**: messages a second, hype words and emotes counting double (`KEKW`, `LUL`, `W`,
  `LMAO`, `💀`...), "clip it" three times. Chat answers a few seconds after what happened, so
  it is moved back 6 s.
- **sound**: loudness above the usual (shouting, laughing, a blast in the game), read from
  the stream's lightest quality (160p): all of a stretch up to an hour, else only around its
  likely moments (seconds not heard count as usual).
- **viewers' clips** made live: the seconds before each one's end (a clip made later from
  the video can't be placed, so it isn't used).

The strongest moments win, at least 10 s apart. A manual hunt takes the best ones it finds;
a live stretch keeps only strong ones (score 0.8 and up). Each clip is cut around its moment,
18 s before it to 9 s after, both ends moved up to 3 s to the quietest second (a pause between
sentences), so 15-30 s long. The job keeps it whole (the preset dresses it). A planner choosing
its own 15 s of a longer window was tried first and missed the moment the chat reacted to.

## Reading Kick (clipper/kick.py)

Kick's site API is public but undocumented, so answers are read loosely:

- `GET kick.com/api/v2/channels/<slug>`: the channel (id, live stream);
  `.../videos`: its streams, each with its HLS master playlist (`source`), the live one too.
- `GET kick.com/api/v1/video/<uuid>`: one stream by its video's id.
- `GET kick.com/api/v2/channels/<id>/messages?start_time=<ISO>`: the chat of the 5 s from
  that moment (first page only on a very busy second: then only up to its last message
  counts as read). The chat is read 15 s past the stretch (it answers late). A stretch up to
  about 50 minutes is read whole; a longer one is sampled (600 reads), and the likely
  moments the samples show (three times the clips asked for) are read again in full, 90 s
  before to 60 s after each. A short burst between two samples can still be missed on a
  very long stream: give a stretch, or follow the streamer live (live stretches are read
  whole).
- `GET kick.com/api/v2/channels/<slug>/clips?sort=view&time=month` and `sort=date&time=day`:
  viewers' clips; Kick's paging repeats pages, so only first pages are read.
- The stream's playlists are 10 s pieces, each with the wall-clock time it was filmed
  (`#EXT-X-PROGRAM-DATE-TIME`): that is how a chat message's UTC time is placed in the video.
  Only the clips' pieces are fetched in full quality (1080p60), and encoded again so a clip
  starts on the second asked.

Kick answers only browser-like requests (a browser User-Agent is sent); 403/429/5xx from the
API are asked again twice. A 401/403 from the video itself is a subscribers-only stream (it
would need a signed-in Kick account, not built). A streamer with stream videos (VODs) off
has nothing to clip from: the hunt and the live check say so. If Kick changes these routes,
the hunt fails with what Kick answered.

Each clip's `source.json` description names the streamer, the stream's title (emoji taken
out) and what the chat said. The post caption reads it, and for a Kick clip the vision model
is told it too as known for sure (caption.watch `known`): small vision models name a new game
after an older one ("GTA V" for GTA 6), and the live Mac's writer (gemma4) kept the vision
model's name over the title until the vision model was told.

## Where things live

- `KickStreamer`, `KickHunt` (db.py); a hunt runs as the queued task `jobs.hunt`, a restart
  fails a busy one and queues a lost one again (jobs.recover).
- A clip's video is kept as a source, `media/sources/kick-<hunt>-<n>/` with its
  `source.json` (title, streamer, the moment's link on Kick): the job's source is
  `kick:kick-<hunt>-<n>`, kept by cleanup while its job is.
- Live clipping: `hunt.check_live`, every 2 minutes from the server's loop.

## Measured

Live Mac (2026-11-22, employee's account, folder "Kick test"): the same 3-minute stretch, hunt
and three clips (29, 29, 28 s) done in about 3.5 minutes.

PC (2026-11-22):

A 3-minute stretch of a 13.6-hour xQc stream: chat read in 8 s (36 reads), sound in 8 s,
three moments found ("EZ" ×14 / "KEKW" / "LOL" with the streamer loud), each clip cut in
about 20 s; the three jobs (29 s, 29 s, 28 s) with the Kick clips preset then took about
10 minutes on the PC's GPU, side by side.
