# Animation Studio: design

Animated, narrated reels made from one line ("the lost city of Ubar"), written, voiced,
scored and rendered on our own machine. First for siddhant-content (feature
`animation_studio`); a prototype, built in phases, to become a full system if it works.

## Requirements (from the owner, 2026-11-21)

| Area | Decision |
|---|---|
| Content | facts/explainers, life lessons, stories, news explainers |
| Language | English |
| Sound | AI voice-over (Kokoro, local) + generated music and sound effects |
| Voices | deep narrator, calm female, energetic young; any can be chosen, the AI recommends one |
| Subtitles | word by word, the spoken word highlighted, in sync with the voice |
| Styles | Neon Glow and Clean Paper in phase 1; Character story and Bold kinetic text in phase 2 |
| Flow | one line -> draft (script + scenes) -> live preview and edits in the browser -> render |
| Editing | scene by scene, changes asked in plain words, one scene made again, undo; timeline editor in phase 2 |
| Length | chosen per reel (auto, 20-35 s, 35-60 s, 60-90 s) |
| Writing model | hybrid: the local planner (gemma4 26B on the Mac) writes, grounded in Wikipedia and web sources; Claude API checks facts for autopilot posts (phase 3) |
| Output | 1080x1920, 30 fps, H.264 + AAC |
| Branding | an animated "Follow for more" ending |
| Autopilot (phase 3) | 1 reel a day per page, topics from the owner's list and from the page's niche (History & mysteries); the first 10-15 approved by hand, then a switch turns on posting by itself |
| Page | a new Instagram page, connected to siddhant-content |
| Success | the reels look professional enough to post as they are; the new page gets views |

## Phases

1. **Engine and studio** (this build): scene player, two styles, Kokoro voices, word
   subtitles, generated music and sound effects, renderer, planner (draft, change in
   words, make one scene again, undo), the Animations tab, render to an ordinary
   video (Post Reel, Buffer, caption, download all work on it).
2. Character story and Bold kinetic text styles; timeline editor (drag scene lengths).
3. Autopilot: topic list + niche topics, 1 a day, approval queue then auto-post, fact
   check before posting, learning from views.

## Architecture

```
one line ──> planner ──> Plan (scenes: narration + visual) ──> voice (Kokoro, per scene)
                │                                                  │ word timings
          sources (Wikipedia, web)                                 v
                                                 timeline (scene starts, word times, beats)
                                                   │                      │
                                  player.html (browser preview)     sound: music + SFX + voice
                                                   │                      │
                                  renderer: headless Chromium frames ─> ffmpeg ─> MP4 ─> Output
```

### Why a browser player

The picture is drawn by one HTML/JS player (`animate/player/`), a pure function of time:
`seek(t)` sets every element for time t (no CSS animations or transitions, seeded
randomness). The studio page plays it live in an iframe; the renderer loads the same file
in headless Chromium and captures each frame. So the preview is the render, frame for
frame. A browser gives proper text shaping, SVG, filters, emoji and fonts for free.
Remotion was the obvious alternative; its free licence stops at small companies, and we
need none of React for this.

Frames: Playwright screenshots of a 1080x1920 page as JPEG at quality 95 (half the time
of PNG, nothing lost that the H.264 encode keeps), four pages at once over frame ranges,
each range encoded to its own file and joined. A 40 s reel renders in about 75 s on the PC
(software drawing in WSL).

### Plan (the document everything else reads)

```
Plan
  v: 1
  title, kind (facts|lesson|story|news), style (glow|paper), voice, mood
  scenes: [Scene]
    narration: what the voice says (1-2 sentences)
    visual: one of the catalogue, with its fields
    hold: extra seconds after the narration (default 0.3)
  caption: the post's caption with hashtags
  sources: [{title, url}]
```

Visual catalogue (phase 1), drawn by every style in its own look:

| kind | for | fields |
|---|---|---|
| title | the hook | text, highlight |
| stat | a number that matters | value, prefix, suffix, label |
| icons | an actor or object doing something | items [{emoji, motion}], label |
| compare | A against B | left {emoji, label, value}, right {...} |
| list | 2-5 points | title, items |
| timeline | dates and events | items [{year, text}] |
| place | where it happened | name, detail |
| quote | a line someone said | text, by |
| reveal | the answer to a mystery | question, answer, emoji |
| dots | a word or number drawn by glowing dots | text, label |

The CTA ending is added by the system, never written by the model.

### Timing

Each scene's voice is synthesized on its own; the scene lasts its voice plus `hold`.
Kokoro gives each word's start and end. The timeline holds scene starts and every word's
time; visual beats (an item appearing, a counter starting) land on the word they belong to
when it is spoken (a number on its digits), else spread over the narration. Subtitles
show 1-4 words at a time, the spoken one highlighted. Sound effects are placed from the
same timeline, so picture, words and sound always agree.

### Sound

- Voice: Kokoro-82M (Apache-2.0) on the Mac's CPU/GPU, 24 kHz, voices mapped to the
  owner's three kinds (and every other Kokoro English voice selectable).
- Music: generated per reel from a mood (mysterious, uplifting, emotional, tense, curious):
  chords, pad, plucks and a bass drawn with numpy, reverb by convolution. No library,
  nothing to clear.
- Sound effects: whoosh on scene changes, pop on items, ticks on counters, a riser before
  reveals, an impact on the hook, a chime on the ending.
- Mix: the voice compressed (3.5:1 above -24 dBFS, as narration is mastered), music
  ducked under it, the whole brought to the presets' loudness (-14 LUFS, -1.5 dBTP).

### Planner

1. Subject and sources: the model names the idea's Wikipedia subject and a short search
   ("the lost city that vanished" finds no article as it is), then Wikipedia (the caption
   code's `gather_topic`) and a web search; none for lessons. One model session for all
   of it (closing it unloads the model), with room for 12k tokens.
2. Draft: the planner model writes the Plan as JSON held to a schema, told the catalogue,
   the length, the safe areas and the sources; it recommends a voice and a mood.
3. Checks: lengths, catalogue fields, the hook first, no "follow"/"Did you know", and what
   is shown is what is said (a stat's number, a place's name, a reveal's answer in the
   narration); a failed check is sent back once with what failed. A scene that only asks
   people to follow is dropped (the system's ending does that).
4. Change in words: the current Plan + the request -> a new Plan (same schema); one scene
   made again: that scene only, the rest given as context. Every change keeps the old
   Plan for undo (20 kept).

### Data and jobs

- `animations` table: id, owner, idea, kind, style, voice, length, status
  (drafting|voicing|ready|failed), message, error, plan (JSON), history (JSON), job_id,
  times.
- Drafting, changes and voicing run on the shared queue (the GPU and CPU are taken in
  turns, like research).
- Rendering is an ordinary Job (`source = "animation:<id>"`); `run_job` hands it to the
  renderer. The Output is a normal video: Jobs list, Post Reel, Buffer, download, delete;
  its caption is the Plan's, written at once.

### API (feature `animation_studio`, owner's own; the primary account sees all)

```
GET    /api/animations                   list
POST   /api/animations                   {idea, kind, style, voice, length} -> drafting
GET    /api/animations/{id}              plan, timeline, status, render
PUT    /api/animations/{id}              {plan} edited by hand -> voicing -> ready
POST   /api/animations/{id}/change       {request, scene?} -> drafting -> ready
POST   /api/animations/{id}/undo
POST   /api/animations/{id}/render       -> job
DELETE /api/animations/{id}
GET    /api/animations/{id}/sound        the preview's mixed sound (WAV)
GET    /api/animation-voices             voices with samples
GET    /api/animation-player/{file}      the player and its fonts
```

### Safe areas (Instagram's buttons and caption)

Top 220 px, bottom 420 px, right 150 px are kept clear of anything that must be read.
Subtitles sit at y 1180-1420; visuals in y 250-1120.

### Quality gates

- Unit tests: plan schema and checks, timeline, sound (length, loudness), subtitles,
  API rules (feature, ownership, undo, render job), run_job branch.
- Browser tests (skipped without Chromium): a two-scene render matches the player at the
  same times; no text overflows its box at any beat.
- Real runs on the PC and the Mac before calling it done; a person looks at frames.

### Risks

| Risk | Mitigation |
|---|---|
| Local model writes weak or wrong scenes | schema, catalogue, examples, checks with one retry; sources in the prompt; edits and change-in-words |
| Wrong facts | sources; the Plan lists them; autopilot fact check (phase 3) |
| Text overflow | the player fits each text to its box (font size search) |
| Chromium missing (CI, a new machine) | `playwright install chromium` in setup; browser tests skip without it |
| Render time | parallel pages; 30 fps |
| Copyright | everything generated; fonts OFL; voices Apache-2.0; emoji Noto (OFL) |

## Phase 2: characters, kinetic text, timeline

- **Character story** (style `story`) and a new visual, `scene`: a setting (room, street,
  park, office, night, school, shop, stage, plain) and 1-2 actors from the Plan's `cast`
  (name + look: man, woman, boy, girl, old man, old woman). Each actor has an action
  (idle, talk, walk_in, walk_out, run_in, wave, point, think, celebrate, sad, shocked,
  angry, shrug, sit, read, give, cry), a mood (face), a prop (one emoji in hand) and a
  line in a speech bubble. The rig (player.js "characters") is SVG seen from the side:
  limbs of two segments turned at the joints, poses as functions of time, blinking,
  mouths moving with the voice when they speak. Looks come from the name (colours,
  hair) so a character looks the same all through; a name like "Grandpa Eli" overrules
  a look that disagrees. Steps are heard as characters walk on; a bubble pops.
- Stories and lessons are written with characters (the planner's guide, a scene
  example); the style follows: Character story when acted, Neon Glow for facts.
- **Bold kinetic text** (style `kinetic`): the narration itself, a few words at a time,
  slammed in on black, key words in colour; numbers keep their counter.
- **Timeline editor**: the reel as a strip of scenes as long as they play; drag a right
  edge to hold a scene longer after its voice (0-3 s), drag a scene onto another to move
  it, tap to play from it; the preview's time moves along it.

## Stick figure style (owner's reference, 2026-11-21)

- Style `stick`: the stick-figure explainer look (a white page, black line people with a
  round white head and a face turned to the front, hair only where it says who someone
  is, the face red when angry and blue when sad or scared; a brown or grey floor band,
  side walls, a few black outlines; a hand-written font, Patrick Hand, OFL).
- Each part's title big on top: the scene's `text` (a person's name, a place), else the
  reel's title.
- For every style: a scene's `detail` is a red note with an arrow at the first actor
  ("83 days"); its `emoji` a big object standing beside them; `crowd` 0-8 more people
  standing around.

## Painted pictures (image model)

- Z-Image-Turbo (Tongyi-MAI, Apache-2.0, 6B), 4-bit (filipstrand/Z-Image-Turbo-mflux-4bit,
  about 5 GB), 9 steps, 720x1280, run by mflux 0.24 on Apple Silicon in its own
  environment (models/image-venv) through animate/paint_runner.py: the model loads once
  per reel, holding the GPU slot like every model. Pictures are kept by what they show.
- The planner writes each scene's `art` (the place, time, light, mood; for a character
  scene the setting empty of people; never names or writing). The style adds its look
  (Character story: anime watercolour storybook; Neon Glow: cinematic dark blue; Clean
  Paper: vintage editorial); "no text" always.
- The player puts the picture behind the scene with a slow push and a veil that keeps the
  style's text readable; in Character story it replaces the drawn place, the characters
  acting in front. Stick figure and Bold kinetic text are never painted.
- A switch per reel and per autopilot (on by default); "Paint it again" in the editor; where
  the model isn't installed the reel is drawn as before, with a note.
- Measured on the M5 Mac: the model loads in 5 s, then 24-27 s a picture; a 6-scene reel
  paints in about 2.5 minutes before its voice is recorded.

## World map style (owner's reference, 2026-11-23)

The reference page posts one format every day: "Most famous X from every country". Its
reels (55-118 s, 1080x1920) fly a camera over a satellite map of the Earth; the country
being talked about is lit in a strong colour with a bright white edge and its name spread
wide and faint across it; a white card with a picture and a dark name label pops up for
each thing named, pinned on the country, so earlier cards slide away as the camera moves
on and all of them show small when it pulls back before flying to the next country.
Captions come one word at a time (a name stays whole) in white Poppins with a soft
shadow, low on the screen. The voice is a clear American man; a bright pop loop sits
about 9 dB under it. The script is always: an optional one-line hook, then one sentence a
country, "<Country> has <thing>, <thing>, and <thing>.", the most famous first, a short
surprising fact after one or two of them, touring from the United States through Europe
and Asia to Australia and Latin America, and no call to action.

Ours (style `map`, "World map"):

- `animate/maps.py`: NASA's Blue Marble (public domain, 24000x12000) cut into tiles at
  four sizes with the ocean turned the reference's deep teal (#00293b) and the land toned
  down; Natural Earth's countries (public domain, 1:10m) as a table of names people say
  ("the UK", "Holland"), label points, main landmass and simplified outlines. Both are
  fetched once into `models/maps` (about 33 MB, then a minute of cutting; `aiedit
  fetch-models` does it too). `attach` adds to the player's data each country scene's
  outline, colour (purple first, then red, green, blue... as the reference), where its
  cards go (inside it, spread apart) and how close the camera comes.
- Visual `country` (text: the country as said; items: the things, each with an emoji and
  `art`, what its card's picture shows). The writer for this style (`animate/mapwriter.py`)
  answers a schema made for the format and checks: at least 5 countries, each once, each
  named in its sentence with every card's name, and every country on the map (a name the
  map doesn't know is sent back once).
- Player: the camera is a pure function of t built from the beats (fly in wide as the
  country is named, move to each card as it is said, pull back to the whole country, fly
  on, with a zoom-out bump on long flights); tiles a frame needs are awaited before the
  renderer takes it, so preview and video match. Captions are timeline.word_captions.
- Pictures: on the Mac each card's picture is painted alone on white (Z-Image, 720x896);
  without the model the card shows the item's emoji. Never a logo, writing or someone's
  character: for a brand, film or character the card shows an object of its kind.
- Sound: mood `upbeat` (123 bpm pop loop, -28 dB); voice Michael (American man).
- The studio offers topic ideas for this style (50 family-friendly "Most famous X from
  every country" topics, those made left out); a World map autopilot takes them in turn,
  then asks the model for more in the same shape, so it needs no niche.
- Every style still draws every visual: a country scene in another style is a list (the
  country, its things with their emoji); other visuals on the map sit on a darkened map.

What stays different on purpose: the reference shows real cartoon characters, brand logos
and photos, and its own watermark; ours paints generic pictures (or emoji) and writes its
own scripts. We keep the system's "Follow for more" ending (a world view with every
country lit), which the reference doesn't have.

## Song story style (owner's reference, 2026-11-26)

The reference page (dreamer.by.night, "Koi Apna Hoga", 20 s, 1440x2560) tells a story to a
song's best-known part: no voice, the song itself is the sound; a flat 2D drawn story in the
dark (side-on people in muted kurtas, a lit room, warm diyas and lamps, phones glowing blue);
each lyric line sits at the top in Devanagari while it is sung, word by word, the small words
light and the key words big and in colour (red for enemies and pain, amber for love and faces,
blue for phones and loneliness); a line fades as the next begins, the picture moves on with it,
the camera pushes in or pans, and the reel fades to black. Its picture tells what each line
means through one visual idea (relatives holding smiling masks over dark faces for a line about
false faces), with the same people from line to line.

The owner's pages post English and Spanish songs, so the story is set in the song's own world
(US/UK everyday life for an English song, Latin America or Spain for a Spanish one) and the
caption is written in its language. Whisper is told the lyrics' language (their letters, or the
everyday words that fill them): left to guess, mlx-whisper on the Mac took a sung Hindi part
for English and heard one word. Tests and prompt examples use made-up lyrics only.

Ours (style `song`, "Song story"): the owner types the song's name (and, after a `|`, a few
words of the part wanted), and:

- `animate/songhook.py` finds the song: LRCLIB (free, open) for lyrics with line times, a
  YouTube search for the song's own upload (never a slowed, lo-fi, cover or live take; the
  length the lyrics' recording has), its sound downloaded once into `cache/songs/<video>`,
  the vocals separated with the editor's own model, and the line times moved onto the upload
  (where singing starts after a pause). The part: runs of whole lines 15-32 s long scored by
  YouTube's "most replayed" graph (how high over the song and over the 30 s around it, and at
  the part's opening), whole sections, no line twice, near 20 s, the first time a part comes,
  and above all the words typed. Four parts that don't overlap are offered. Whisper hears the
  part's vocals and the lyric words are laid over the heard words by syllables. A song LRCLIB
  doesn't know is heard whole and what whisper heard is its lyrics (a note says so).
- `animate/songwriter.py` (the planning model): first the lines as shown (Devanagari for
  Hindi and Urdu, one word for one so every word keeps its time; a line that doesn't come back
  one for one stays as written), their key words and colours (never a small word), and where a
  long line breaks into two; then the story: 2-5 people with a fixed look, one moment a line
  (what its picture shows, a camera move, whether it goes on with the last picture), a title
  and a caption in the page's way (Hinglish lines, "Sound on", song credit, hashtags).
- Pictures: each moment painted with its people in it (the look `song`: flat 2D vector,
  thick outlines, muted colours, navy night, warm lamp light); a person's name in a picture is
  written out as they look; a moment that goes on with the last shares its picture; one seed a
  reel. Without the image model the moments are a dark night with warm lights.
- Timing (`timeline.build_song`): a scene a line, from 0.15 s before it is sung to 0.15 s
  before the next; the caption from its first word until the next line. The sound is the
  song's part, faded in and out, at -14 LUFS. No closing "Follow" scene.
- Player (theme `song`): the picture covers the frame with the camera move over its shot, a
  new picture fading in over the last; dark at the top for the lyrics, a vignette, dust in the
  light, a fade to black at the end; the line at the top left in Poppins (Devanagari), each
  word rising in on its time, key words 1.8x in Poppins Bold and their colour.
- Studio: the form takes the song's name and the part; a song reel shows the song, the part
  playing and the other parts to play (`POST /api/animations/{id}/change {"choice": n}`,
  op `hook`: timed again, a new story); its scenes show the lyric line, the camera and the
  picture; people can be edited; one scene a line (none added, moved or removed); its style
  stays. The autopilot doesn't make Song stories (they start from a song someone names).

## Singer edit (owner's reference, 2026-11-26)

The reference (KiD Boi's "Maa, I Tried", 12 s, 1080x1080 at 60 fps) is a song's hook made
into a lyric edit around the singer: one picture a sung line (about 1.5 s each: eight lines),
each a warm, gritty, cinematic scene (amber light, deep brown shadows, glowing rim light,
film grain, torn-paper edges) with the singer in it, from behind, from the side, in a mirror,
small in a crowd; every sung word slapped on as a torn-paper sticker the moment it is sung
(bold serif on white paper, white on black paper, marker capitals), the line's key words in
red handwriting with a pen underline, red stars and question marks, small side notes ("Kya
achieve kiya?", "Future plan kya hai?") around the line; whip pans with motion blur, punch
zooms, glitches and paper tears between lines; the song alone for sound.

Ours (style `singer`, "Singer edit"; `animate/singer.py`, `player/singer.js`): the owner types
the song's name and gives 1-6 photos of the singer.

- The hook: `songhook` (written beside this, for Song story reels) finds the song, downloads
  it, cuts its catchy part and times every sung word. `singer.find_hook` takes what it gives
  however it is shaped (the hook alone or the whole song with the hook's place, times in the
  song or the hook, words alone or inside lines) and keeps the hook's sound as a WAV. Lyrics
  written in Latin letters for a song shown in another script (romanised Hindi) are used as
  written, as the reference shows them.
- Lines: the lyric's lines when they match the timed words, else the singer's breaths; a line
  sung in under 0.75 s joins the next, one longer than 4.6 s is cut at its widest gap. Each is
  one scene, from just before its first word to the next line.
- Photos: uploaded through `POST /api/animation-photos` (kept with the painted pictures, by
  content, at most 1600 px), then the singer cut out by ISNet (Apache-2.0, 176 MB, fetched the
  first time into `models/segmentation`, run on the CPU, about 1.5 s a photo), stray blobs
  dropped, cropped to them, their face found (MediaPipe), all kept once. A photo nobody stands
  out in is shown whole, as a taped print.
- The writer (the planner model, held to a schema) gives each line a shot, its key word or
  two (a long line), 0-3 side notes in the song's own language and script, a doodle on a few
  lines and the picture behind it, plus the title and the post's caption. What it gets wrong is
  put right by rule: the reel opens on the singer, never the same shot twice running, words at
  most twice, a whole line given back as "key words" picks the longest word that carries
  something, never two doodles running. A writer that doesn't answer still gives an edit.
- Shots (player/singer.js): `hero` (waist up, big), `close` (the face), `far` (small in a big
  place; a full-length photo stands with a shadow and a pool of light), `print` (the photo as a
  torn, taped print), `mirror` (the singer and their reflection in a gold frame), `echo` (three
  copies, two fading), `silhouette` (a dark outline with a glowing rim against a sunset city),
  `words` (no singer, the words huge), `painted` (the singer painted into the picture from
  behind or the side: only where the image model is). A photo cut off at the waist always
  stands on the frame's bottom edge, so its cut never shows.
- Backdrops: the painted picture (Z-Image on the Mac, look "singer": warm amber, gritty poster,
  film grain; nobody in it behind the photo; the singer described by the vision model for a
  painted shot); without the model, drawn: the photo itself blurred and toned warm, an old
  brick wall with paper scraps and a light beam, a sunset city, or rows of grey faceless people.
- Words: a sticker each, landing on its sung time (a slam with a little overshoot, a shake of
  the frame), set in rows as big as the shot's space allows, slightly turned and tucked; key
  words red (Kalam Bold or Permanent Marker) with a pen underline drawn after they land. Side
  notes go in the nearest room the words and the face leave free. Fonts (bundled): Abril
  Fatface, Martel Heavy and Kalam Bold (OFL; Martel and Kalam write Devanagari too), Permanent
  Marker (Apache-2.0).
- Between lines: whip, punch, flash, paper tear or glitch, in turn; grain (12 a second), dust,
  a drifting light leak and a vignette over everything; the last 0.3 s fade to black.
- Sound: the hook's own, a 0.35 s fade at its end, at the presets' loudness (-14 LUFS, -1.5
  dBTP). No voice, no generated music, no "Follow for more" ending: the reference has none and
  it would cut the song.
- Output: 1080x1920 (the reference is square; Reels are 9:16), 30 fps, CRF 21 (the grain
  costs bits: 27 Mbit/s at 17, 8 at 21).
- In the studio: the scene editor shows each sung line (fixed: one scene a line, in order),
  its shot, key words, doodle, side notes and picture; change in words and "write this scene
  again" work as for other styles; a singer edit keeps its style (a narrated reel can't become
  one either); the timeline editor isn't shown (the song sets every length). Autopilots can't
  make singer edits (they need photos).

Measured on the PC (RTX 5050): writer (qwen3.5 9b) 22 s, the two cutouts 3 s, a 12 s edit
renders in 40 s.

### Any photo, made ready by itself

The first live uploads were refused: search-result thumbnails (a 197x197 WebP album cover, a
295x180 stage photo) under the old 200 px minimum, with nothing in the log to say why. Now
(`singer.take_photo`, `singer.prepare_photo`, on upload):

- Any kind a phone or browser saves: JPEG, PNG, WebP, GIF and animated WebP (the first
  frame), AVIF (Pillow), HEIC/HEIF from iPhones (pillow-heif: its code is BSD-3, its wheels
  bundle libheif and libde265 under LGPL-3 and x265 under GPL-2, fine to run on our own
  machines; pi-heif, the decode-only variant, is discontinued with a known decoder flaw),
  CMYK, 16-bit (scaled, not clipped), see-through parts put on white, turned as the camera
  held it. The picker offers every image (`image/*`, plus .heic/.heif/.avif).
- Sizes: under 120 px on the short side is refused with the size in the message; under 400
  px it is enlarged to 800 by Real-ESRGAN with GFPGAN on the faces (the upscale preset's
  models, on the CPU: a thumbnail takes a few seconds and the GPU stays free), smoothly where
  those models aren't, with a note that it may look soft; over 1600 px it is made smaller;
  over 200 megapixels it is refused as a decompression bomb.
- A refused upload is logged with the reason, the file's format, its size in bytes and
  pixels and what the browser sent it as (never the picture), and the form shows the
  server's reason next to the file's name.
- Made ready: plain rows and columns at the edges trimmed (letterboxing, a phone
  screenshot's status and navigation bars, a frame); every line OCR reads painted out, and
  letters its detector sees but can't read (a cover's script title) where they aren't on the
  person; cropped to the main person (the biggest face in a group, between the neighbours'
  faces, with the whole head and room around the face; more room when no face is found);
  smaller faces away from them left out of the cutout (a drummer behind the singer). A photo
  of a face alone (a close portrait, a cover) is shown whole, as a print, not cut out to
  float, and painted from whole. The form's thumbnail is this prepared photo, and the edit
  uses exactly it; the notes ("a small photo (197x197): enlarged", "borders trimmed", "text
  painted out", "cropped to the singer") show as a message.
- The singer is described by the vision model from a photo of all of them where there is
  one (it took the cover's made-up face for a woman's), and that description goes into the
  likeness model's prompt.

### Marks on the photos

Photos people send carry stamps: an agency's watermark ("Bollywood Hungama" on the first
test photo's jacket), a page's @handle, a web address. They would show in every scene, and
a painting would copy them. Before anything else each photo is read by OCR (RapidOCR, the
editor's own), and painted out (`singer.clean_photo`): any line that reads like a stamp
(a web address, a handle, a copyright sign, a photo agency's name) wherever it is, any text
mostly on the singer (lyric stickers go over the singer), and the lines beside those (the
rest of a logo), each with a margin of half its height. Only the strokes are painted over
(what stands out from the box's own smoothed look, cv2 inpainting), so a jacket under a
watermark keeps its folds. Text in the background that doesn't read like a stamp stays; it
goes with the background when the singer is cut out. The cleaned photo is used everywhere
after: the cutout, the print, the likeness painter's reference. Without OCR (no ml extras),
photos are used as sent.

### The singer painted into the scenes (likeness)

The reference paints the singer into each scene in new poses. Where the likeness model is
(`IDENTITY_MODEL`, off until the Mac test below has passed), every line's picture is painted
by FLUX.2 [klein] 4B (Black Forest Labs, Apache-2.0, distilled to 4 steps; text encoder
Qwen3-4B, Apache-2.0) through mflux (0.24: `Flux2KleinEdit`), in the image model's own
environment and the GPU slot, one model for the whole reel:

- a scene with the singer (`hero`, `close`, `far`, `mirror`, `echo`, `silhouette`,
  `painted`): the singer from their photo, as a reference image (the cleaned cutout on plain
  grey, so nothing behind them is copied), posed as the shot says ("seen from the waist up",
  "a close-up of their face", "small in the distance, seen from behind", "before a tall old
  mirror", "in a crowd of grey faceless people", "a dark silhouette against a bright sunset"),
  in the writer's place for the line, in the singer look, with "keep their face, hair, beard,
  skin tone, build and clothes" and no text, letters, logo or watermark. Photos are used in
  turn when there are several.
- `words` and `print`: the place alone, nobody in it.
- The player shows a painting with the singer in it at full size (their face stays sharp)
  with nothing cut out in front. Its face is found in the painting (YuNet, the biggest face:
  the singer stands nearest; MediaPipe's short-range finder missed most painted faces) and
  the words go above it, else below it, never over it; the side notes keep off it too.
- A picture that can't be painted (the model missing, a timeout) shows the singer cut out
  and stood in the scene, as without the model; so does every scene while `IDENTITY_MODEL` is
  unset.

Why this model: the owner pages earn money, so the licence must allow commercial use. FLUX.1
Kontext [dev] and FLUX.2 [klein] 9B are non-commercial; InstantID, PuLID, IP-Adapter-FaceID
and PhotoMaker need InsightFace's antelopev2/buffalo face models, which are non-commercial;
Z-Image-Edit isn't released (Z-Image-Turbo alone, text to image). The two candidates left are
Qwen-Image-Edit-2511 (Apache-2.0, 20B: about 12 GB at 4 bits plus a 7B text encoder, many
times slower) and FLUX.2 [klein] 4B (Apache-2.0, 4B, 4 steps; mflux's measure on an M4 Pro:
27 s for a 1024x1024 picture in bf16 with the prompt encoding, 8.6 GB of weights at 8 bits,
4.6 GB at 4). Measured on the M5 (the script below, 2026-11-27, `mflux-community/
flux2-klein-4b-mflux-q8`, 8.0 GB download): a 720x1280 picture with one reference in 15-26 s
(22-23 s on average, the first of a reel the quickest), about what Z-Image takes now; the
painter's peak memory 4-4.5 GB. On the PC (RTX 5050, diffusers, 4-bit): 10-13 s a picture.

Seen in the test paintings (two singers, three photos, six shots each): the face, turban or
hair, beard or none, and the clothes carry over in the hero, echo, mirror reflection and
close shots; far and silhouette shots are seen from behind as asked. Close-ups drift most
from the real face. A back view now and then loses the headwear. Anything stuck to the
singer in the cutout is painted as part of them (a logo behind one photo's turban came out
as horns), so photos with a plain background work best. Signs in a street can come out with
made-up letters despite "no text".

Mac check before turning it on (nothing live is touched; the model, a newer mflux when the
live one has no FLUX.2, the pictures and frames all go in a test folder):

    .venv/bin/python run/polite.py scripts/mac_singer_identity_test.py \
        --photos ~/Desktop/singer1.jpg --folder ~/aiedit-test/identity

It prints the load time, the seconds of each picture and the painter's peak memory, and
saves the photo next to three paintings (`out/sheet.jpg`) and three frames as the reel shows
them. Then `IDENTITY_MODEL=mflux-community/flux2-klein-4b-mflux-q8` in `.env` and a restart.
The live image environment needs mflux 0.24 (`models/image-venv/bin/pip install -U
mflux==0.24.0`) for FLUX.2.

## Phase 3: autopilot

- One autopilot per account (`animation_autopilots`): niche, topics (one per line, first
  goes next), hour, reels a day (a few hours apart), content, style, voice, length, the
  Instagram page, auto-post.
- Each due reel: the next topic, else a new one from the niche (the model suggests,
  avoiding those made); drafted; its facts checked against its sources (stories and
  lessons are not checked; facts with no sources are in doubt); rendered; then it waits
  for approval ("Approve & post" posts it to the page; "Skip").
- Auto-post can be switched on after 10 approved reels; even then a reel with a doubtful
  fact, or one whose post fails, waits for a person. Posting uses a public link to a
  copy of the video, as Post Reel does; the site's address is kept from the last time
  the autopilot was saved.

## Status

Phase 1 built (branch animation-studio, 2026-11-21): everything above, both styles, ten
visuals, three voice kinds and every Kokoro English voice, the studio page (new reel,
reels, live preview with sound, Instagram-area guides, play from a scene, scene editor,
change in words, write a scene again, style and voice switches, undo, render, the video
and its job). Phases 2 and 3 built the same day (branch studio-characters): see above.
