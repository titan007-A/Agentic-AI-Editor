# Channel autopilot

The Channel autopilot tab (feature `channel_autopilot`, on for `siddhant` and
`siddhant-content` until the setting `feature:channel_autopilot` says otherwise) runs a
connected Instagram page by itself: each day it finds videos that fit the page, checks them,
edits them with the page's preset, checks the reel again and posts it at the page's post
times, through the app's own Instagram connection (`instagram.post_reel`, the page connected
on the Instagram tab). It never uses Buffer. Code: `src/agentic_editor/channel/`.

## Setting it up

On the tab, for each connected page:

- **Preset**: the one its reels are edited with (a split preset such as "Instruments vs
  vocals" is offered first).
- **Research account**: the researcher's account for the page (one is made for the page's
  own name when none is chosen). Its research runs every day at **Research starts at**.
- **What to look for**: what the research looks for besides what the page's best posts
  show, passed to the research as the request for the day.
- **Prefer surprising picks** (on by default): the research's searches and ratings go past
  each artist's single most famous hit (rated 6/10 at most) to recognisable songs people
  don't expect (8-10); the page's data showed surprising picks did best.
- **Posts a day** and **Post times** (the server's local time). The defaults come from the
  owner's channel data: five a day at 02:30, 04:00, 06:00, 18:30 and 21:30.
- **Approve the first** (20 by default): until that many reels were approved, each one waits
  for a person; after that it posts alone. A reel in any doubt always waits.
- **Dry run** (on by default): everything happens except the post itself. Switch it off to
  post for real.
- **Running**: off pauses it (nothing found or posted; checks already started finish).

## A day of it

1. **The page is read** (Graph API `/{ig}/media`): every post's caption, likes and comments,
   and the insights Meta gives the login (views, reach, saves, shares). Without the insights
   permission Meta answers (#10) and it learns from likes and comments; the page says so.
   The best liked posts become the research's taste, the page's posts its research account's
   own (never found again), and the planning model's reading of them its summary.
2. **The research** (`research.run`, YouTube Shorts and whole songs, Instagram a tenth at
   most) is told the page's best posts and every song it posted or has waiting, and leaves
   those songs out after rating (`research.Posted`: a song is the same by its title without
   take notes and an artist in common, or named in a caption in any of the page's styles).
3. **Picking**: of what the research gave, the best fitting first (then the best scored),
   with a fit of 8/10 or more, as many as keep two days of posts ahead: three tried for each
   wanted, 45 at most, two of one singer at most. Those under the fit, posted before, or
   known to be too short are noted as skipped without a check. Another copy of a song picked
   from the same research is kept as a spare (two at most): it is checked only if the better
   copy fails (a 29 s copy of Valerie no longer loses the song when its 223 s copy was
   found too). When everything a research gave was checked and too few passed, another
   research starts an hour later, three a day at most.
4. **Checks before the edit**, one video at a time, stopping at the first that fails (the
   video is skipped with why, never guessed about):
   - not posted before, by the research's name for the song;
   - the right length: the preset's shortest reel plus 10 s, up to 10 minutes;
   - downloaded;
   - one song: Shazam names the same song at two of three points or more, no other. Shazam
     is asked 8 s apart at most; when it turns the app away (too many requests) nothing is
     asked for 5 minutes (twice as long each time in a row) and the video goes back in line
     with the time it is tried again, never skipped;
   - named: the video's title, caption or channel names that song and its singer;
   - not posted before, by Shazam's name;
   - vocals: the edit's own separation keeps them (its fingerprint and loudness checks),
     there are instruments to take away (not voice alone), and the singer is heard in 30%
     of the video or more.
5. **The edit**: an ordinary job with the preset and Shazam's song and artist as its notes,
   behind people's own jobs (`jobs.automatic_jobs`), with the preset's post caption. Every
   split reel (the autopilot's or a person's) changes from the band to the voice where the
   isolated voice is silent (25 dB under its loudest, 0.12 s or more) or only breathes (15
   dB under, over 0.12 s): near the preset's share first (2.5 s either way), else anywhere
   from 20% to 60% of the reel; a silence before a breath, a moment no word spans (a word's
   end inside a breath counts) before one whisper puts inside a word; the words' widest gap
   only when there is neither (render/split.py).
6. **Checks on the reel** (any doubt leaves it under "needs a look", on the piles too, never
   posted):
   - the edit's own confidence, doubts and issues;
   - the caption written, with nothing to check before posting;
   - the reel's length within the preset's;
   - panels: the top panel plays then holds, the bottom holds then plays, changing at one
     moment; no black (nothing bright in it) or frozen stretch in the playing one, no blank
     held picture;
   - labels: both panels' labels where the preset draws them;
   - switch: the picture and the sound change together, the voice is 12 dB or more under
     its loudest there, and no sung word spans the change unless the voice is silent there;
   - voice: the singer is heard in 60% of the vocals part or more;
   - leak: no instruments left in the vocals part: its share of energy under 120 Hz, against
     the song's own instruments' share over the same stretch (the mix minus the vocals), puts
     them 28 dB or more under the voice. On six real songs this read their own instruments
     mixed back in at -30 to -15 dB within a dB, and the clean vocals parts 35-54 dB under;
     how far the vocals fall between phrases didn't tell (reverb and backing vocals keep
     clean modern pop 10-27 dB). A leak with no low end (hi-hats alone) isn't measured;
   - loudness: the preset's loudness within 1 LU, its peak, both parts within 3 LU.
7. **Posting**: at each post time (an hour late at most) the oldest ready reel is posted
   with its caption, at most "posts a day" a day. The post's id and link are kept and the
   reel is marked posted. A post that fails pauses the autopilot and says why on the page;
   "Try again" sends it at the next time once it is resumed.
8. **Numbers**: a day and two days after a post its likes and comments (and insights, when
   allowed) are read and shown in the Posted table; the next day's reading of the page takes
   them in.

## The page's caption

Set in the preset (`post_caption`): `first_line: "🎵 {song} — {artist} - {year}"` puts that
line above the caption (the song as the caption was written about it, the year from the post
or MusicBrainz; without a year the line ends at the artist), then a blank line, then what the
`style` asks for, such as "two or three short sentences of plain facts about the song".

## Insights

Views, reach and saves need the `instagram_manage_insights` permission in the Meta app's
login configuration (META_CONFIG_ID). Add it there, then connect the page again on the
Instagram tab; until then likes and comments are the measure.
