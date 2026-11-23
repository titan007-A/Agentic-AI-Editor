# Claude in the Animations tab (MCP)

Claude (claude.ai in the browser or the app, or Claude Code in a terminal) can work the
Animations tab for you: list your reels, start one from an idea, read its script scene by
scene, ask for changes, change the voice or style, paint the pictures again, render the
video and give you its link. It acts as **your account only** and sees **only your own
reels** (even for the primary account, which sees everyone's on the web page). Posting
is off unless you turn it on, and even then Claude must be told to post.

It works through MCP (Model Context Protocol): the site serves an MCP endpoint at
`/mcp`, next to the web app, behind its own sign-in.

## The address

The site's address with `/mcp` at the end:

| Where | Address |
|---|---|
| The live site | `https://pjmediahubeditor.tech/mcp` |
| On the Mac itself | `http://localhost:8000/mcp` |
| Any other way you open the site | `<that address>/mcp` |

The Animations tab shows it in the **Connect Claude** card (with a copy button).

## claude.ai (custom connector)

1. On claude.ai open **Customize > Connectors** (claude.ai/customize/connectors) and
   choose **Add custom connector**.
2. Name: `Animations`. URL: `https://pjmediahubeditor.tech/mcp`. Leave the OAuth client
   fields empty (it registers itself) and add it.
3. Choose **Connect**. A tab opens on pjmediahubeditor.tech (log in if it asks) with
   "Connect to your Animations tab": it names the app and that you go back to
   claude.ai. Leave **Let it post reels** off unless you want that, then **Allow**.
4. Back in claude.ai, turn the connector on in a chat (the tools menu) and ask, for
   example: "Make an animation about the lost city of Ubar, short, Character story
   style. Show me the script when it's ready."

The connection appears in the Connect Claude card ("signed in through this site"),
where you can switch its posting on or off or disconnect it.

If claude.ai says it can't reach the server, Cloudflare's bot protection may be stopping
Anthropic's servers (they call from `160.79.104.0/21`). In Cloudflare, add a WAF custom
rule that skips bot checks for the paths `/mcp`, `/register`, `/token`, `/authorize` and
`/.well-known/` (or for that address range).

## Claude Code

With a key (simplest):

1. Animations tab > **Connect Claude** > **Make a key** (posting off unless wanted).
2. Copy the command it shows (the key is shown only once) and run it in a terminal:

   ```
   claude mcp add --transport http animations https://pjmediahubeditor.tech/mcp \
     --header "Authorization: Bearer aie_..."
   ```

   Add `--scope user` to have it in every project, not only the folder you are in.
3. In Claude Code, `/mcp` lists `animations` as connected. Ask it to make a reel.

Without a key: `claude mcp add --transport http animations https://pjmediahubeditor.tech/mcp`,
then in Claude Code run `/mcp`, pick `animations`, **Authenticate**: the same "Connect to
your Animations tab" page opens in the browser.

On the server machine itself there is also a stdio form, acting as one named account
(the running server's worker does the work it queues):

```
claude mcp add animations -- uv run --directory ~/Agentic-AI-Editor aiedit mcp --user siddhant-content
```

Add `--posting` after the user name to let it post when asked.

## The tools

| Tool | What it does |
|---|---|
| `list_animations` | your reels, newest first, with status and links |
| `get_animation` | one reel: status, the script by scene (narration and what it shows), caption, sources, render, links; `include_raw_plan` for exact edits |
| `wait_for_animation` | waits (up to 50 s a call) while it is written, changed, painted, voiced or rendered |
| `list_voices` | the narration voices |
| `create_animation` | a new reel from an idea (kind, style, voice, length, painted pictures) |
| `change_animation` | a change in plain words, or one scene written again |
| `edit_animation_plan` | exact edits to the plan, as the scene editor saves them |
| `set_style_and_voice` | a new look, a new voice (recorded again), pictures on or off |
| `repaint_pictures` | the painted pictures painted again (some scenes or all) |
| `undo_last_change` | back to the version before the last change |
| `retry_animation` | a failed draft or change tried again |
| `render_animation` | the final video (an ordinary job in Jobs, with the reel's caption) |
| `skip_autopilot_reel` | turns down an autopilot reel waiting for approval |
| `list_instagram_pages` | your connected Instagram pages (posting connections only) |
| `post_to_instagram` | posts the rendered reel to one of your pages, now (posting only, `confirm=true`) |
| `approve_autopilot_reel` | approves an autopilot reel, which posts it (posting only, `confirm=true`) |

Links in the answers (`studio_url`, `video_url`, `job_url`) open on the site for someone
signed in: the reel in the Animations tab, the video, its job.

Not there on purpose: deleting reels, changing the autopilot's settings or starting its
reels (an autopilot reel may post by itself), anything outside the Animations tab.

## Safety

- Keys and tokens are stored only as hashes; a key is shown once when made.
- Changing the account's password (or its role) removes every key and connection, like the
  signed-in sessions. Removing an account removes them. Each can be disconnected on the tab.
- A connection only works while the account has the Animations tab (`animation_studio`).
- Posting needs the connection's **posting** switch on and `confirm=true` on the call;
  Claude is told to use it only when you clearly ask. An autopilot reel posts by itself
  only after the autopilot's own render: rendering it again later (from the tab or from
  Claude) never posts it.
- Limits: 60 tool calls a minute per account; from Claude, at most 3 of the account's reels
  being written and 3 rendering at once; an address sending many unknown keys has to wait;
  apps registering (20 an hour) and asking to connect (60 in 10 minutes) are limited per
  address.
- claude.ai connects with OAuth (the app registers itself, PKCE, a sign-in page here asks
  you); an app may only be sent back to an https address (or http on the same computer,
  for Claude Code), and the page warns when that address isn't Claude's. Access tokens
  last a day and are refreshed by the app; refresh tokens change on every refresh and
  end after 60 days unused.

## On the server

The endpoint is part of `aiedit serve` (stateless streamable HTTP with plain JSON answers,
so nothing has to stream through Cloudflare). New in this version: the `mcp` Python
package (`uv sync --extra ml` on deploy), the tables `mcp_tokens` and `mcp_clients`
(made at start), and the web pages (`npm run build`).
