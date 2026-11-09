# Agentic AI Editor

A local video editor for short-form content. Give it a video link or file and one or
more presets; it downloads the video, analyzes it (transcript with word timings, scene
changes, silences, audio energy, faces), plans an edit, renders one video per preset
and checks the result against the preset's rules.

Everything runs on your own machine. Models are served by Ollama, Whisper and the vocal
separation model run in-process.

Only use it on content you own or have permission to use.

## How it works

```
download -> analyze -> plan (EDL) -> snap cuts -> render -> review -> done
                                         ^                     |
                                         +---- fix and redo ---+
```

- Analysis isolates the vocals when a preset asks for them, transcribes with word
  timings, and finds scene cuts, silences, short pauses, loudness and faces.
- Every reel is one continuous stretch of the source, trimmed only at its ends. Speech
  reels start and end on whole sentences: the transcript's punctuation and the pauses
  heard in the audio mark where they begin and end. Translated speech captions are
  translated a sentence at a time and laid back over the original timing.
- Review checks each render (length, framing, loudness, caption sync) and applies
  deterministic fixes, re-rendering up to twice.

- `backends.py` detects the machine at startup and picks the transcription backend
  (faster-whisper on CUDA, mlx-whisper on Apple Silicon, CPU int8 otherwise) and the
  encoder (NVENC, VideoToolbox, or libx264). Everything else is platform-neutral.
- `router.py` (`ModelRouter`) is the only code that knows which model does which job.
  Whisper and vocal separation run in a child process so GPU memory is freed before
  the next stage loads.
- The planner produces an edit decision list (`edl.py`), which `cuts.py` snaps to word
  boundaries. The renderer (`render/`) turns it into ffmpeg filtergraphs; the model
  never writes ffmpeg commands.

## Setup: Linux / WSL2 with an NVIDIA GPU

Requirements: git, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com),
an NVIDIA driver that `nvidia-smi` can see.

```
git clone https://github.com/titan007-A/Agentic-AI-Editor.git
cd Agentic-AI-Editor
scripts/setup-linux.sh
```

The script installs a static ffmpeg build with NVENC (Ubuntu's ffmpeg package has none)
into `~/.local/opt/ffmpeg-nvenc` and points `.env` at it, installs the Python
environment with the CUDA 12.8 build of PyTorch (needed for RTX 50-series/Blackwell
GPUs), pulls the Ollama models, downloads the transcription, vocal separation and face
detection models (`uv run aiedit fetch-models`, about 2.4 GB, logged as it goes) and prints
the detected backends. Build the web app once with `(cd web && npm ci && npm run build)` if
you want the browser interface.

## Setup: macOS on Apple Silicon

Requirements: [Homebrew](https://brew.sh).

```
git clone https://github.com/titan007-A/Agentic-AI-Editor.git
cd Agentic-AI-Editor
scripts/setup-macos.sh
uv run aiedit hash-password        # put the two lines it prints in .env
scripts/macos-service.sh install   # run the editor in the background from every login
```

The setup script installs ffmpeg (Homebrew's `ffmpeg-full`: the plain `ffmpeg` has no
libass, which captions need), uv, Node and Ollama (as a Homebrew service, so the models
are served from every login), the Python environment with mlx-whisper for transcription,
the Ollama models and the other model weights, and builds the web app. On macOS
`ENCODER=videotoolbox` trades larger files for much faster encodes.

`macos-service.sh` installs a launchd agent that starts the server at login, restarts it if
it stops and logs to `~/Library/Logs/agentic-editor/server.log`; `status`, `restart`,
`logs` and `uninstall` do what they say. While the server runs on power it keeps the Mac
from going to sleep (the display may still turn off), so jobs and overnight autopilot runs
finish. A MacBook with its lid closed sleeps anyway unless it is plugged in with a display
attached. The first start asks whether to accept incoming connections; allow it, then
phones and other computers on the network open `http://<mac name>.local:8000`.

To reach it from anywhere on a domain of your own, put the domain's DNS on Cloudflare (the
free plan is enough) and run a Cloudflare Tunnel; it is much quicker from phones than
Tailscale Funnel's relays:

```
brew install cloudflared
cloudflared tunnel login                                  # pick the domain in the browser
scripts/macos-tunnel.sh setup ai-editor editor.example.com
scripts/macos-tunnel.sh install                           # runs now and at every login
```

After a network drop, cloudflared can stay up without a connection (the domain then shows
Cloudflare's error 530), and Tailscale Funnel's relays can stop reaching the Mac.
`scripts/macos-funnel-watch.sh install` checks both public links every 5 minutes, as a
visitor would, and restarts whichever got stuck while the editor itself runs (log:
`~/Library/Logs/agentic-editor/funnel-watch.log`).

### Moving to a Mac

To make a Mac the editor's home, set it up as above (without starting the service yet),
turn on Remote Login on the Mac (System Settings, General, Sharing), and run this on the
machine the editor lives on now:

```
scripts/move-to-mac.sh you@your-mac.local
```

It copies the jobs, finished videos, downloaded sources, uploads, presets, assets and fonts,
and the login settings, then rewrites the stored paths for the Mac
(`aiedit relocate`). Analysis caches and model weights are not copied: the Mac makes its
own. Autopilot folder feeds still point at folders on the old machine; edit them to a
folder on the Mac. Then start the service on the Mac.

## Usage

```
uv run aiedit presets
uv run aiedit run "https://..." -p vocal-music-9x16 -p vocal-music-es-9x16
uv run aiedit run ~/Videos/talk.mp4 -p shorts-9x16
```

Outputs go to `media/outputs/<source-hash>/<run>/<preset>.mp4`, next to the EDL that
produced them; every run (a job in the web app, each video of an `aiedit run`) has a folder
of its own, so running a video again never replaces earlier versions. Analysis results are
cached per source file in `cache/`.

`-n` (or the notes box in the web app) takes a plain-language instruction:

```
uv run aiedit run talk.mp4 -p shorts-9x16 -n "Spanish subtitles, warm look, under 30 seconds, start where he talks about the FCC"
```

The planning model lists the setting changes the instruction asks for, each with the
instruction's own words that ask for it, and the rest (which moment to use) goes to the
planner. Every change is checked in code before it is applied: its words must be in the
instruction and be about that setting, and a language, look or length must actually be
named. Changes nobody asked for are logged and dropped, so an instruction never turns on
something it didn't mention. Each change shows in the output's edit decisions. An
instruction that says where to start ("start where...", "begin with...") is looked up
on its own: the planning model names the transcript line where that moment begins and
the words that show it, and the answer counts only if those words are in that line. The
reel starts there, `hook.optimize` is off for that job so nothing moves the opening, and
it ends on the last whole sentence that fits, before any long silence.

Instructions can be in Hindi or Hinglish ("mat", "nahi" turn a thing off). Regenerating adds
the new notes after the earlier ones, and where two lines clash (lyric captions on, then
"lyrics generate mat kar"; the video's text kept, then "cover the text in the video"), the
later line wins and the decisions say which line it replaced. Captions are text the app
writes; the text already in the video (burnt-in lyrics, logos) is a separate setting:
keep it in view (no blur, nothing cut for it, `watermarks.cut_text: false`) or hide it.

Every output gets a confidence score. Review issues, a fallback planner, a failed
translation or caption step, a long stretch without voice, or a reel that came out short
each lower it and are listed; an output with none of them is marked ready to post, the
rest "needs a look".

## Web app

```
uv run aiedit hash-password      # prints PASSWORD_HASH and SECRET_KEY lines for .env
(cd web && npm ci && npm run build)   # once, and again after frontend changes
uv run aiedit serve
```

For frontend work, run `npm run dev` in `web/` next to `aiedit serve`; the dev server
proxies `/api` to port 8000. Format with `npm run format`.

To use it from a phone on WSL2, set `networkingMode=mirrored` in `%UserProfile%\.wslconfig`
and open `http://<windows-lan-ip>:8000`. If the phone can't connect, allow the port
through the Windows firewall from an admin PowerShell:

```
New-NetFirewallRule -DisplayName "Agentic AI Editor" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

`serve` runs the API, the job worker and the built frontend in one process on
`HOST:PORT` (default `0.0.0.0:8000`), so any device on the network can reach it. Jobs
are queued in SQLite through Huey, two in progress at once (`MAX_CONCURRENT_JOBS`). They
take turns on the GPU and on the encoder: every model fits on the card only on its own,
and two encodes would only share the CPU. A job gives the GPU up while it encodes, so the
next one transcribes, separates and plans meanwhile instead of waiting for the whole
edit. The turns are file locks too, so a command-line run started while the server is
busy waits its turn rather than running out of GPU memory. Each pasted link becomes its
own job; progress streams to the browser over server-sent events.

Which queued job runs next is fair, not first come first served: when a worker is free it
takes the job whose turn it is. People's own jobs go before autopilot's (an autopilot job
that has waited 30 minutes counts as one of theirs), then the account with the fewest jobs
running, then the account served longest ago, then the oldest job. So one person's 20
queued videos, or an autopilot batch, never hold up someone else's single job: the
accounts take turns. On the Mac (M5 Pro) two workers are right: one x264 encode already
uses every core (two at once take twice as long), and the models take the GPU in turn.

Post captions take the same turns as jobs: they share the workers and the GPU, so a
person's queued captions (one after each render) never hold up someone else's job.

Researches run one at a time, in the same kind of turns: the account served longest ago
first, then the oldest. Two at once search the same sites together, which then refuse
both (HTTP 429/403), and would hold both workers the jobs need; a research waiting for
its turn holds no worker, so jobs keep running meanwhile.

Downloads are keyed by the site's video id and uploads by content hash, and all
analysis (vocal stems, transcripts, scene cuts) is cached by the hash of the source
file, so the same video is never downloaded, separated or transcribed twice.

### Post to Buffer

Everyone, admin or employee, adds their own Buffer API keys on the Buffer page (Buffer:
Settings > API; only the Buffer account's owner can make one), each with a name. One key
reaches every channel of that Buffer account; the page lists them and how many requests went
to Buffer today (its limits are per Buffer account: 600 a day on the free plan). Keys are
never shown again and nobody else uses them. Below the keys, each preset is linked to the
channels it posts to. Links are to channels, not keys, so a replaced key keeps the presets
working; a channel no key shows any more is flagged in red.

A finished reel's card has a Buffer button: it opens with the reel's preset's channels ticked
(other channels folded away; the first post of an unlinked preset can remember its choice).
An admin posting an employee's reel posts with that employee's keys and doesn't change their
links. Pick caption, an Instagram first comment, and when: add to the queue, next in the
queue, now, or at a time. Instagram posts go as Reels shared to the feed, so the Instagram
account must be a Creator or Business one (Buffer refuses personal profiles). Buffer takes no
uploads: it fetches the video from an unguessable link on this editor (/api/pub/...) when
it posts, so the server must be reachable over https then (set PUBLIC_URL in .env if the
editor is opened another way) and Cloudflare must let Buffer's fetch through.

### Accounts

The owner is the admin; employees get accounts of their own. Each employee has presets of
their own and sees only their own jobs, feeds and reels. A new employee starts with no
presets: the admin sends each preset to the employees who should use it ("Send to employees"
on the Presets page), or ticks one employee's presets on People ("Assign presets": ticked
ones are copied to them, unticked ones they have are deleted, their own presets are left
alone, and "replace" refreshes the copies they have with the admin's current version), so
nobody sees presets meant for someone else. The admin sees
everyone's work, with who made each, and manages every employee's presets from the Presets
page ("Whose presets"), or from People.

On Autopilot, the Ready and Needs a look piles show only folders at first, one per preset,
each with its video count and the newest video's picture. Tapping a folder opens it on its
videos; "All folders" or the browser's back button closes it, and the address
(`/autopilot/?tab=look&folder=<preset>`) opens the same folder from a link.

The preset editor keeps a small preview in the corner: a still of what the preset makes,
redrawn a moment after every change (saved or not), on a drawn stand-in video in the shape
you pick (16:9, 4:3, 1:1, 4:5, 9:16), with sample words for {song}, {artist} and the like.
Tap it for a bigger view. The preset's logo and pictures show in it too, and under it
the preview says what looks wrong ("things to check"): text too long for its width, text
squeezed for room, text over the video or over other text, pictures off the frame or over
text, files missing.

"Your watermark" puts your own mark on every video: words ("@yourpage"), a picture (a
logo, drawn above the words), or both, at the size, colour, outline and opacity you set.
Its place is set by dragging it on a small frame, tapping one of nine spots, the arrow
keys, or typing how far across and down it sits (0% flush with the left or top edge,
100% with the right or bottom; it never runs off the frame). For Instagram reels the
frame shades where Instagram draws its buttons and caption, and the preview warns when
the mark sits there. The mark is drawn once into a transparent frame-sized picture that
the render lays over every frame and the preview pastes, so it lands on the same pixels
in both. It is set by hand only: learning a style never sets it.

"Pictures on every video" places any picture (a page's avatar and name, a badge) at a
position and width in frame pixels. With text on one side of the video only, the video
keeps to that text at the gap set, instead of sitting in the middle of the space left.
"Learn from a reel" cuts a page's still branding (avatar, name, handle) out of the
reference reel's padding and keeps it as pictures, where it was.

Branding files (logo, intro and outro clips, background music) and LUTs are uploaded from
the preset editor: each has an Upload button. They are checked (a picture, a video with a
picture, a sound file, a .cube LUT), kept in media/assets by their content, and the preset
names them by that path. A path to a file already on the server can still be typed in. Text, picture band and corners come from the render code itself;
captions show size and place, their letters can differ a little in the real video.

On People the admin adds employees, sets a new password (which signs them out everywhere)
and removes them, keeping their jobs or deleting them too. Accounts made there are always
employees; an admin is made, demoted or removed only on the server:

```
uv run aiedit user add NAME --admin             # asks for a password
uv run aiedit user add NAME --admin --env-password   # the PASSWORD_HASH in .env
uv run aiedit user list | passwd NAME | role NAME member | remove NAME
```

Until an account exists, the password in `.env` signs in as an admin called `admin`, so the
upgrade locks nobody out. Only the admin can watch a folder on the server with Autopilot.

## Autopilot

The Autopilot page in the web app watches feeds and edits each new video once:

- A feed is a folder on this computer, or a playlist or channel link (a bare YouTube
  channel link reads its Videos tab; add its Shorts tab as a feed of its own). Each feed
  has its presets, an optional instruction for every video, and optionally the hours new
  videos may start, such as 23:00 to 07:00 for overnight.
- Feeds are checked every five minutes, and on demand. A new feed notes what is already
  there and edits only what arrives later, unless told to edit that too. Files still
  being copied into a folder are left until they stop changing.
- Every video a feed starts or passes over is remembered, so none is edited twice. Each
  is an ordinary job: one that fails is marked failed and the rest carry on, a few at a
  time per feed.
- Finished videos land in one of two piles: Ready, when the agent has no doubts, and
  Needs a look, with its doubts listed. "Looks fine" moves a video to Ready, "Posted"
  and "Dismiss" take it off the piles.

## Researcher

The Researcher page finds Instagram reels and YouTube Shorts like those of an Instagram
account you post on, or for what you describe ("80s rock singers live on stage, Bryan
Adams, Queen", in
Hinglish too), ranked by how well they fit and how viral they are:

- **Your Instagram accounts.** Each person saves their own accounts (@name or the profile
  link) with the presets they edit for each; nobody else sees them, the admin included.
  The researcher learns an account: it finds its own reels the same way it finds any
  (below), keeps those yt-dlp says are the account's, and the planning model writes what
  it posts (artists, eras, languages, format, captions, what got the most likes). This is
  done when the account is added, on "Learn again", and before a research when it is a
  week old. With none of its reels found, a research goes by the presets.
- **Questions first.** Before searching, the researcher asks up to 6 short questions whose
  answers change what to find (live or studio, eras, languages, artists, hits or hidden
  gems, which preset), each with suggested answers to tap and room for your own words;
  never about what the presets decide (captions, format, length, the sound). The answers
  become part of the request; "Skip questions" (or the box on the form) searches at once.
- **Understanding first.** The planning model writes a one-line English brief of what to
  find (from the account, its presets and an optional focus, or from the description) and
  about 15 searches for it: artists, songs, concerts, hashtags, languages. The request's
  own words aren't searched ("italian gaane" finds Hindi songs).
- Instagram has no open search, so it searches the way a chat assistant does: web search
  engines with `site:instagram.com/reel ...`, through the free `ddgs` library (several
  engines, no key), a second apart, so a research takes a few minutes.
- Each reel found (up to 40) is looked up with yt-dlp without signing in: account name
  and @handle, caption, likes, comments, date and picture. Views and length aren't given
  then, and a reel Instagram shows only to signed-in people keeps what the search engine
  said.
- The planning model rates how well each fits (Fit, 0-100%), by its artist and song
  rather than its caption's language, names the song, says what of the account or request
  it matches and which of your presets it suits. Below 50% it is left out. Viral compares
  likes and comments, in total and per day since posting, among the reels found. The
  order is Fit times a virality bonus, so a reel that doesn't fit stays low however viral.
- A research gives the best 10, never a reel an earlier research showed, one you edited or
  the account's own, and at most 2 from one account and 2 of one singer or band. Another
  reel of the same song is fine. Reels you marked good show your taste, not all you want:
  about two thirds of the searches look for more like them and the rest for something
  else that fits (other artists, songs, eras or styles), and a fitting reel isn't rated
  lower for being unlike them, so a research keeps some variety. "Explain reasoning" under each says why it was chosen: the search that found it,
  the fit and why, what it matches, the song, the preset and why, likes and comments per
  day, and its score against the best reel left out.
- Choose up to 5 to edit: each comes with the preset it suits (change it if you like),
  and "Edit selected" starts a job for each, listed on the research with links.
- **Chat.** Under a finished research, ask the researcher about its picks ("why #2?",
  "which is the most viral?"), answered from the reasoning it stored, in the language you
  wrote in; or ask for something else ("more Italian", "older concerts") and it starts a
  follow-up research, linked both ways, which keeps the earlier answers and never shows a
  reel shown before.
- **Feedback.** Every reel has "Good pick" and "Not for me" (with a reason to tap or your
  own words). Later researches learn from it: for an Instagram account, the reels you liked
  or edited and those you didn't want go to the planning and rating model ("find more like
  these", "fewer like these"); for a description alone only those you didn't want, since
  its topic is another each time. "Never show @x again" hides an account from all your
  researches; hidden accounts are listed on the page, each with a button to show it again.
- **YouTube Shorts.** "Search" on the form picks Instagram reels, YouTube Shorts or both
  (both by default). Shorts are found the same way ("site:youtube.com/shorts ...") and
  rated and ranked with the reels; a link to a full video, or one YouTube says is longer
  than 3 minutes, is left out. A Short's card shows its views and a "YouTube Short" badge.
- **Source pages.** Each of your accounts can list pages to take reels from (an Instagram
  @name or a YouTube channel link, up to 10). Every research for the account looks at
  their latest too (5 each): a YouTube channel's Shorts tab lists them; an Instagram page
  can't be listed without signing in, so it is searched for by name and only its own
  reels are kept. They are rated and ranked with the rest, never shown twice.
- **Trend radar.** From the reels your researches of the last two weeks found (morning
  ones included): the songs several accounts posted reels of lately, and the reels
  posted then that gain likes and comments fastest. No searching of its own: it fills up
  as researches run.
- **Page performance.** Each time one of your accounts is learned, a snapshot of its own
  reels found (likes, comments, when posted) is kept, the latest 30. "Page performance"
  on the account shows the likes a reel against the snapshot before, and each reel's
  likes, growth and likes a day. Search engines find only part of a page: a sample.
- **Every morning.** "Research every morning" on an Instagram account starts a research
  for it (Instagram and YouTube Shorts) each day at that hour, server time; one the
  server was off for starts when it is back, the same day. It shows in the list as
  "Daily", with reels new since the day before, as for any research.
- **Quality check.** Each reel's best version is read when it is looked up (no download):
  under 480p on its short side it is left out ("low quality"), SD (480-719p) ranks a
  little lower than HD, and its card shows the resolution. A Short's card also shows its
  length, in red when it is shorter than its preset's shortest reel.
- **From a PDF.** Instead of a description, give a PDF that lists anything to make reels
  of (songs, films, people, places, events...), laid out any way. It is read first, and
  nothing is searched until you choose:
  - A numbered list ("1. Name (1966) - Singer", a note under each) is read by rule,
    however long, with each item's number, year, the words under it and its section (the
    heading above where the numbering starts again at 1: a decade, a genre). Any other
    layout (a table, prose) is read whole by the planning model, a few pages at a time
    ("reading").
  - Then the research waits, "choosing": tick the items to research (all are ticked at
    first), whole sections at once, or keep only the numbers and years you give (say 31
    to 100 of each decade); or say it in your own words ("only the 60s to the 90s, the
    top 30 are done, leave out Manzanero") and the planning model ticks them for you to
    check: sections, numbers and years are applied in code, anything else (such as "only
    female singers") is judged item by item. Name what the list is of ("songs" is guessed
    when the PDF says so): songs are searched for as performances (the singer singing it,
    live), anything else as itself. Give a preset for each section, say where the words
    under each item go (above the video, below it, or nowhere; lines labelled "Top
    text:" or "Bottom text:" go where they say), swap name and singer when the list has
    them the other way round, and how many items each research takes (30 at first): the
    chosen items are researched that many at a time, one research after another.
  - Each item is searched for and rated (up to 4 reels each, never one twice). The
    research shows the reels item by item, with its words; choose a reel for each (its
    section's preset is already picked), and "Edit selected" starts each edit with the
    notes lines `song:` and `artist:` (for songs), `year:` and `top text:`/`bottom
    text:`, so the reel shows your words in place of the preset's own above and below the
    video (in the look of the preset's first block there), and `{year}` in a preset is
    the item's year: the one the PDF writes beside its name ("Song (1966) - Singer",
    "Song - Singer - 1966", "Song - Singer, 1966"), which isn't asked for then and wins
    over the post's caption and MusicBrainz. A preset whose block there writes `{headline}` takes the words as its
    headline instead: they are wrapped like one, and the block's other words ("SONG:
    {song}", the year) stay. Your words, from the PDF or the notes, always win over the
    preset's own. A scanned PDF is only pictures and can't be read.
- Search engines index only part of Instagram, mostly popular reels a few days old or
  more, so a research finds plenty, never everything; the engines may also turn it away
  for a while when asked too often.

## Presets

A preset is a YAML file in `presets/` whose file name matches its `name`. It sets the
frame (`aspect_ratio`, `resolution`, `fps`), the length range, and how each part of the
render behaves:

| Section | Controls |
| --- | --- |
| `captions` | what they say (`speech`, `lyrics`, `translated` into `language`, or `visual`, written from the picture), font, size, colours, position, animation; kept off faces and off text already in the picture (name banners, titles) while it is on screen; lines break where they read well, never after a linking word like "the" or "de" when a better place is near, and each sentence starts a new line; on songs, presets that keep the original audio take their words from the isolated vocals when the full mix gives almost none; an .srt file is saved next to the video |
| `one_liner` | a single line the vision model writes about the video, burned in; `hint` steers it, `language` sets what it is written in |
| `text_blocks` | fixed text drawn on every frame, such as a series tagline; `{song}` and `{artist}` come from a video named "Song - Artist" or, for a link, which can't be renamed, from notes lines `song: ...` and `artist: ...` (they also become the reel's title and the song its post caption is about), `{headline}` is written for each video (see `headline`), and any other word in braces is the preset's own, filled per reel from a notes line like `day: 103` for `{day}` (`{song_number}` from `song number: 5`); `{year}` can also come from the video's name, "Song (1980) - Artist", and otherwise from the post's caption (a line naming the song with its year) or MusicBrainz (the song's first release), so the job form doesn't need it |
| `auto_name` | `true`: when neither the video's name nor the notes say the `{song}` and `{artist}`, they are recognised from its sound with Shazam (free, no key, through the unofficial `shazamio` library, so it can stop working). Shazam's notes about the take go, "(Live)", "(feat. ...)", "- Remastered 2011"; a bracket that doesn't say what it is stays only if MusicBrainz knows the song by the name with it, "(I Can't Get No) Satisfaction". Still nothing, and the job page asks for the song and artist and makes the reel with them |
| `post_caption` | the post's caption (see below): `about` the `song` (from Wikipedia and Wikidata) or the `video` (from watching it, what is said, its post and Wikipedia); `enabled` writes it after every render, otherwise the video's card has a Write caption button; `language`, `style`, word range |
| `headline` | how the planning model writes `{headline}` from the video's title and description (`hint` with examples, `max_chars`, `uppercase`); a notes line `headline: ...` gives it instead. Long headlines wrap into even lines without breaking a quoted song name |
| `framing` | how the picture fills the frame (see below); `picture_x`/`picture_y` move a picture shown whole px right/down (minus: left/up), set on the editor's "Video position" card with arrows; what would run into the text above or below, or off the frame, is cut off (at least a quarter stays), so the text never moves for it; background colour, or `auto_background` to take it from the footage, or `background_image`, a picture behind everything (a starry sky), filling the frame |
| `grade` | colour: `auto` corrects a cast or a flat, dim picture; `look` (warm, cool, teal-orange, film, moody, vivid, mono, faded); a `.cube` LUT; `strength` |
| `audio` | `original` or `vocals` (instruments removed), high-pass, loudness target; `language`, what is sung or said (`es`), for the speech recognition, which left to tell can take a Spanish song for an Italian one and write Italian lyrics |
| `silence` | what counts as silence when trimming a quiet start or end |
| `beat_sync` | start and end on the beat (bar lines from the original mix), and an optional zoom pulse on each bar or beat |
| `hook` | `optimize` lets the editor choose where the reel starts: the strongest opening line (the planning model reads them for speech; the strongest entrance wins for music), and the strongest stretch when the plan runs long. Off, the planned opening stays and a long plan is cut from the end; `variants` makes up to five versions per video from different strong segments, saved as `preset-b.mp4` and so on |
| `watermark` | your own mark (words, a picture or both) with `x` and `y` as shares of the room beside it (0 left/top, 1 right/bottom), `opacity`, `size`, `width`; off until `enabled`. Not the same as `watermarks`, which blurs marks burnt into the source |
| `title_overlay`, `logo`, `music`, `intro`, `outro` | optional extras |
| `style_notes` | plain-language guidance the planner reads before choosing the edit |

Text keeps its end of the frame. A top one-liner, title, hook or caption stays at the
top and a bottom one at the bottom: to keep off a face or the picture it only moves a
little nearer its own edge (at most 300 px, never under Instagram's header or buttons),
and never to the other end. Captions keep the preset's place even when the planner
would pick another. The video makes way instead: with framing `none`, a one-liner up
for the whole reel holds its rows like a text block (at its longest, `max_words`), and
the picture is cut or fitted between it and the other text. A banner that still runs
long is made smaller, down to three quarters of its size, then moved toward its edge.
The preset preview lays these out the same way and says when it had to shrink or move
it. It starts on a video of the preset's own shape (9:16 for a 9:16 preset, as reels
are), since the video's shape decides how it is cut; the shape you pick is kept per
preset.

The one-liner's `language` ("Spanish" or "es") says what it is written in; unset, it
follows translated captions, else the model's choice. Learning a style turns words on
the reel that are about that one video, a sentence long, with no song or year to fill
in (a page's description of each clip), into the one-liner: its place, font, size,
colour, length and language as the reel has them, written afresh for every video.
Text is learned as text: a mark mostly covered by words is never cut out as a picture.

Framing `smart` (the default) frames each shot so nothing that matters is cut. Heads
come from the face detector; the vision model points out what else must stay whole (the
microphone or instrument in use, a logo), and OCR finds the text on screen. A shot whose essentials fit a
full-height 9:16 window is cropped, following the subject if it moves; one that doesn't is
widened and padded above and below instead of cut, a name banner sliding in only widens
the seconds it is on screen, and when most of a reel is too wide every shot gets the same
framing so it doesn't jump at each cut. Fresh moments are then re-checked by geometry and
any shot whose edge would cut something is widened before rendering. `face`, `center` and
`none` (show the whole picture) remain for fixed looks.

`framing.picture_width` shows the whole picture narrower than the frame (0.74 of its
width, say), centred on the background, for pages that inset their videos.

### Post captions

The caption to post with a reel is written by the local planning model, for free, from what
the song's Wikipedia articles (in the caption's language and in English) and its Wikidata
entry say; it isn't asked to remember anything. The song is named from the video's
headline, title and description, and an article only counts if Wikidata (or, without that,
its first sentence) names the artist as the song's performer. The model is told to use only
those sources; every capitalised name and every number of 10 or more in what it writes must
appear in them, which is checked in code; and for each sentence stating a fact it must copy
the sources' words that say so, which are then looked up in the sources. A caption that
fails is written again once with what was wrong; what still fails is shown under it as
"Check before posting", next to links to the articles it came from. A song Wikipedia has no
article on gets no caption.

A preset whose posts aren't about a song sets `post_caption.about: video` ("About" in the
editor). Then the caption is written from the video itself: the vision model watches six
frames of the stretch the reel uses and says plainly what it shows; with what is said in
it, the post's own description and the reel's one-liner, the planning model names what the
reel is about, the facts it gives and up to three English Wikipedia articles on them
(read in the caption's language too when Wikipedia has the same article there). What is
seen, said and posted counts as a source like the articles, so the same checks apply. The
preset's `style` then says what the page wants from the caption, on top of a default shape
(three paragraphs, a few emojis, no hashtags). On Vanshaj's beach reel it wrote about the
two AH-64 Apaches over Myrtle Beach on 4 July 2026 and the Semiquincentennial, in 58 s on
the Mac, with nothing left to check.

### Learning a style from a reel

Give a reel that already looks the way you want, and a preset is drafted from it:

```
uv run aiedit learn "https://www.instagram.com/reel/..." --name raw-vocals-es
uv run aiedit learn reference.mp4 --name raw-vocals-es --save
```

or "Learn from a reel" on the web app's Presets page, which opens the draft in the editor
with what was found listed above it. Nothing is saved until you save it.

- Layout, from the pixels: whether the picture fills the frame, sits on a flat background
  (and its colour), or sits on a blurred copy of itself (drawn here as a colour taken from
  the footage); how wide the picture is, and the gaps to the text.
- Text: the vision model reads every text on six frames (two in the opening seconds, the
  rest timed to words being said or sung, since captions only show then). Frames are read
  padded to a square: on a tall frame its boxes drift down by up to a tenth of the height.
  Text that stays the whole reel becomes a text block, captions that change become caption
  settings, text only in the opening seconds becomes the hook banner. Whether text on the
  padding stays is also checked in the pixels of eight frames across the reel: the model
  reads the same line differently from frame to frame (a line more, a line less), which
  made a page's line about the song look like captions. Text that stays is split into the
  blocks it is drawn in (a sentence, then a blank line, then "SONG: ..." over the year) by
  OCR's line boxes, its words taken from the model's readings, which keep the accents. A username in a
  corner and text that belongs to the video shown (inside a letterboxed picture) are left
  out. The model's boxes are only roughly right, so each text is then found in the pixels:
  its exact colour, position, line breaks, an outline and a highlighted word's colour.
  The vision model also says whether the letters have serifs; measuring can't tell a
  serif from a plain font of the same width and weight, so only fonts of that kind are
  tried (a serif, Source Serif 4, is bundled). Each is tried at the size that matches the
  measured widths, and the one whose heights and stroke thickness match best is chosen; every reading of a text is
  measured and they vote, so one bad box can't decide it. Caption sizes and margins are
  measured against libass, which draws captions smaller than Pillow draws text blocks.
- Captions are `speech` or `lyrics` when they are the words heard, `translated` when they
  are in another language, `visual` otherwise. Accents don't count in the comparison, and
  when the speech recognition heard another language than the captions', it listens again
  told theirs; the preset then keeps that language (`audio.language`) for its edits.
- A padding that isn't a flat colour (stars, a texture) becomes the preset's background
  picture: the reel's still pixels, with the rows of the picture and text filled from the
  rows around them.
- Look: black and white is recognised; other looks are left for you.
- Sound: speech or singing (the planning model reads the transcript and what the frames
  show), and for singing, whether the instruments were taken out, in which case the
  preset isolates the vocals. The loudness target follows the reel's.
- The length range is set around the reel's length, and the planning model writes the
  label and the style notes.

Text that changes from video to video becomes the words the editor fills for each video:
the song's name `{song}`, the singer `{artist}`, its year `{year}`, and a sentence about
the video `{headline}`, written for the next videos like the reel's own (its hint quotes
it). The planning model makes the template and it is kept only if, filled back with what
it says each word stood for, it gives the text read; otherwise the text is flagged so you
can edit it ("Day 28"). Words to read (six or more) are never cut out as branding.
Logos, your watermark, music beds, intros and outros are not learned.

The quickest way to make one by hand is to duplicate an existing preset on the web app's
Presets page and edit it there; the server validates every save. Editing the YAML by
hand works too: `uv run aiedit presets` loads and validates all of them. Fonts are
looked up by file name (without extension) in `fonts/`. Text those fonts can't draw, such
as Hindi, Japanese, Arabic or emoji, is drawn in the Noto font for its script, downloaded
into `models/fonts` the first time a reel needs it; a character no font has stops the
render with an error rather than showing as an empty box.

## Models

| Job | Setting | Default |
| --- | --- | --- |
| Planning the edit | `LLM_MODEL` | `qwen3.5:9b-q4_K_M` (Ollama) |
| Checking frames, thumbnails | `VISION_MODEL` | `qwen3.5:4b-q4_K_M` (Ollama) |
| Transcription | `WHISPER_MODEL` / `MLX_WHISPER_MODEL` | `large-v3-turbo` |
| Vocal isolation | `SEPARATION_MODEL` | `UVR-MDX-NET-Voc_FT.onnx` |
| Text on screen | (fixed) | PaddleOCR PP-OCRv6 small, through RapidOCR on the CPU |

Text on screen (burnt-in lyrics, captions, @handles) is found by OCR, not by the vision
model: on 36 frames of 9 real reels the vision models (`qwen3.5:4b`, `qwen3.5:9b`) found
under half the lines OCR found, their boxes sat a fraction of a line to a whole line off,
and now and then they named a line that wasn't there. OCR boxes sit on the letters, so the
picture band cuts edge text off cleanly and captions keep off it. A watermark candidate is
blurred only when OCR reads letters in it and the vision model agrees it was added on top
(the vision model alone once read a watermark into a trouser leg). The OCR models (30 MB)
come inside the `rapidocr` package of the `ml` extras, so nothing is downloaded; without
it the vision model's text boxes are used as before.

To try another model, pull it (`ollama pull <name>`) or pick one from
`uv run audio-separator --list_models`, set it in `.env`, and run the benchmark and the
end-to-end test. The Ollama models must support JSON-schema output, and the vision model
must accept images. On Apple Silicon a bare tag such as `qwen3.5:9b` fetches an MLX build,
which can't: name the GGUF build (`qwen3.5:9b-q4_K_M`). Changing the separation model re-separates cached sources the next
time they are used. A new kind of model belongs in `router.py`, which is the only place
that maps jobs to models.

## Benchmark

```
uv run aiedit benchmark                 # Commons sample performance, 246 s
uv run aiedit benchmark ~/Videos/x.mp4 -p shorts-9x16 --no-llm
```

It times each stage without the caches. On an RTX 5050 laptop (8 GB) under WSL2:

```
  transcription        11.8s  20.9x realtime, 83 words, faster-whisper
  vocal separation     42.2s  5.8x realtime, vocals_mel_band_roformer.ckpt
  llm qwen3.5:9b        6.7s  31.0 tokens/s over 203 tokens
  llm qwen3.5:4b        3.2s  63.3 tokens/s over 195 tokens
  render               14.3s  1.4x realtime, 42 fps at 1080x1920, libx264
```

## Configuration

All settings come from `.env`; see `.env.example`. Any backend can be forced:

```
TRANSCRIBE_BACKEND=faster-whisper   # or mlx, cpu
ENCODER=nvenc                       # or videotoolbox, x264
```

Ollama keeps a safety margin when it places a model, so on an 8 GB card it runs part of
`qwen3.5:9b` on the CPU. `LLM_GPU_LAYERS=99` puts all of it on the GPU, where it fits:
42 instead of 31 tokens/s on the RTX 5050. If a model ever fails to load that way, the
editor lets Ollama place it for the rest of the run.

By default the final encode uses libx264 (`slower`, CRF 16) even when a GPU encoder is
present: measured with VMAF against lossless renders it kept the same quality as the
NVENC settings it replaced in 30-57% less space. Forcing `ENCODER=nvenc` or
`videotoolbox` is several times faster for larger files.

Vocals are isolated with UVR-MDX-NET-Voc_FT (`SEPARATION_MODEL`), run the way the
owner's earlier reel tool ran it: each chunk twice, once phase-inverted, averaged
(denoise), with 0.75 overlap between chunks. The MelBand Roformer vocal model
(`vocals_mel_band_roformer.ckpt`) scores higher on the MUSDB18 test excerpts, 11.9 dB
median vocal SDR against 8.5 dB, and is faster, but its vocals sounded worse on real reels.
Only the MDX models read the denoise and overlap settings.

### YouTube asks to sign in

A machine that looks up many videos is sometimes told by YouTube to "confirm you're not a
bot", and then every YouTube download fails (a newer yt-dlp doesn't help). yt-dlp gets
past it with the cookies of a browser signed in to YouTube:

1. In a private window, sign in to YouTube with a spare Google account (YouTube may
   block an account used this way; don't use your own), open youtube.com, and export its
   cookies to `cookies.txt` with an extension such as "Get cookies.txt LOCALLY", for
   youtube.com only. Close the private window without signing out.
2. Copy the file to the server, say `~/Agentic-AI-Editor/youtube-cookies.txt`, and add
   `COOKIES_FILE=youtube-cookies.txt` to `.env` (a path relative to the repository, or
   absolute). Restart the server.

Every download, research lookup and feed check then uses them. The file is a sign-in:
git ignores files named `*cookies*.txt`; keep it that way.

## Development

```
uv sync --extra ml
uv run pre-commit install   # ruff and prettier on every commit
uv run pytest               # unit tests, no GPU or models needed
uv run pytest -m e2e        # real pipeline on a short sample video
uv run ruff check . && uv run ruff format --check .
(cd web && npm run lint && npm run format:check)
```

CI runs the Python checks and the web lint, format and build on every pull request.
The macOS job only runs when the workflow is started by hand (Actions, ci, Run
workflow), because GitHub's Apple Silicon runners are often unavailable.
