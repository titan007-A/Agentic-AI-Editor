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
