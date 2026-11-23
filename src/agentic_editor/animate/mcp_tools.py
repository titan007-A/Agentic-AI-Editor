"""The Animations tab as MCP tools, for Claude (Claude Code, claude.ai's connectors).

Each tool runs one of the studio's own operations (routes.operations, built with
`own_only`: Claude sees and changes only the account's own reels, whoever it is), so it
goes through the same checks as the web page and a refusal comes back as the tool's
error. Who is calling comes from mcp_access (the key or token on the request), or, over
stdio, from `aiedit mcp --user`. Work is queued as from the web page: the server's worker
writes, voices, paints and renders; the tools start it and look at it.

Posting is a separate, explicit step: its tools work only for a way in the person allowed
to post, and only with confirm=true.
"""

import copy
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal

import anyio
from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field, ValidationError
from sqlalchemy import func, select

from agentic_editor.animate import images, studio
from agentic_editor.animate.routes import AnimationRequest, ChangeRequest, PlanEdit, StudioChoice
from agentic_editor.db import Animation, InstagramChannel, Job, Output
from agentic_editor.db import User as Account
from agentic_editor.mcp_access import CALLS_PER_MINUTE, Caller, Hits

WORKING = ("queued", *studio.BUSY)
MAX_WORKING = 3  # one account's reels being written (or rendered) at once, from Claude
WAIT_MOST = 50  # seconds wait_for_animation holds on (Cloudflare ends a request at 100)
POSTING_OFF = (
    "posting is off for this connection; the person can turn it on in the Animations tab "
    "(Connect Claude)"
)
NOT_CONFIRMED = (
    "nothing was posted: call again with confirm=true, and only after the person said to "
    "post this reel now"
)

INSTRUCTIONS = """\
The Animations tab of the person's video editor: narrated, animated vertical reels \
(1080x1920) written from one line, in five styles, with an AI voice and painted pictures.

The usual flow:
1. create_animation with the idea in a few words. Writing, painting and recording take \
1-5 minutes: call wait_for_animation (repeat it) until status is "ready" or "failed".
2. get_animation gives the script scene by scene. Show it to the person; the preview \
plays at studio_url.
3. Changes: change_animation (plain words, or one scene written again), \
edit_animation_plan (exact edits to the plan get_animation returns with \
include_raw_plan), set_style_and_voice, repaint_pictures, undo_last_change. Each is \
queued like the first draft: wait again.
4. render_animation makes the video (about a minute); wait_for_animation until the \
render is "done", then give the person video_url. Links open on the site for someone \
signed in.

Post (post_to_instagram, approve_autopilot_reel) only when the person clearly asked to \
post that reel; these work only if they allowed posting for this connection.\
"""

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
CHANGE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
POST = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

AnimationId = Annotated[str, Field(description="The animation's id (from list_animations).")]
Kind = Literal["auto", "facts", "lesson", "story", "news"]
Style = Literal["glow", "paper", "story", "kinetic", "stick"]
Length = Literal["auto", "short", "medium", "long"]
VOICE_HELP = (
    "'auto', a kind of voice ('narrator', 'calm', 'energetic'), or a voice id from list_voices"
)


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value


def visual_of(visual: dict) -> dict:
    """What a scene shows, without the empty fields of its flat shape."""
    shown = {
        k: v
        for k, v in visual.items()
        if v not in ("", 0, [], None) and k not in ("image", "art_seed", "art", "items", "actors")
    }
    if visual.get("items"):
        shown["items"] = [
            {k: v for k, v in i.items() if v and k != "motion"} for i in visual["items"]
        ]
    if visual.get("actors"):
        shown["actors"] = [{k: v for k, v in a.items() if v} for a in visual["actors"]]
    if visual.get("art"):
        shown["picture"] = visual["art"]
        shown["painted"] = bool(visual.get("image"))
    return shown


def script_of(plan: dict) -> dict:
    return {
        "title": plan.get("title", ""),
        "kind": plan.get("kind"),
        "style": plan.get("style"),
        "style_name": studio.STYLE_NAMES.get(plan.get("style", ""), plan.get("style")),
        "voice": plan.get("voice"),
        "mood": plan.get("mood"),
        "cast": plan.get("cast") or [],
        "scenes": [
            {
                "number": n,
                "narration": s.get("narration", ""),
                "shows": visual_of(s.get("visual") or {}),
            }
            for n, s in enumerate(plan.get("scenes") or [], 1)
        ],
        "closing_line": plan.get("cta", ""),
        "post_caption": plan.get("caption", ""),
        "sources": plan.get("sources") or [],
    }


def working(view: dict) -> bool:
    render = view.get("render") or {}
    return view["status"] in WORKING or render.get("status") in ("queued", "running")


def next_step(view: dict, render: dict | None) -> str:
    status = view["status"]
    if status in WORKING:
        return f"Still working ({view['message']}). Call wait_for_animation."
    if status == "failed":
        return f"It failed: {view.get('error')}. retry_animation tries again."
    if render and render["status"] in ("queued", "running"):
        return f"Rendering ({render['stage'] or 'waiting'}). Call wait_for_animation."
    if render and render["status"] == "done":
        return "Rendered: the video is at video_url (it opens for someone signed in to the site)."
    if render and render["status"] == "failed":
        return f"The render failed: {render.get('error')}. render_animation tries again."
    return (
        "Ready to review: show the script (the preview plays at studio_url). Change it, or "
        "render the video with render_animation."
    )


def readable(view: dict, base: str, raw_plan: bool = False) -> dict:
    """An animation as the tools give it: the studio's view, with the script laid out by
    scene (numbered from 1), links, and what to do next; without the player's data."""
    out = {
        k: _plain(view.get(k))
        for k in (
            "id", "title", "idea", "status", "message", "error", "notes", "kind", "style",
            "voice", "length", "can_undo", "created_at", "updated_at",
        )
    }  # fmt: skip
    out["painted_pictures"] = view.get("images")
    out["scene_count"] = view.get("scenes")
    out["duration_seconds"] = round(view["duration"], 1) if view.get("duration") else None
    out["studio_url"] = f"{base}/animations/?id={view['id']}"
    render = None
    if view.get("render"):
        r = view["render"]
        render = {
            "status": r["status"],
            "stage": r["stage"],
            "progress_percent": round((r.get("progress") or 0) * 100),
            "error": r["error"],
            "job_url": f"{base}/job/?id={r['job_id']}",
        }
        if r.get("output_id") and r["status"] == "done":
            render["video_url"] = f"{base}/api/outputs/{r['output_id']}/video"
            render["download_url"] = render["video_url"] + "?download=true"
            out["video_url"] = render["video_url"]
        out["render"] = render
    if view.get("autopilot_id"):
        out["autopilot"] = {
            "post_status": view.get("post_status"),
            "post_error": view.get("post_error"),
            "doubts": view.get("doubts") or [],
        }
    if view.get("plan"):
        out["script"] = script_of(view["plan"])
        if raw_plan:
            out["plan"] = view["plan"]
    out["next"] = next_step(view, render)
    return out


def summary(view: dict, base: str) -> dict:
    full = readable(view, base)
    keep = ("id", "title", "status", "message", "style", "scene_count", "duration_seconds",
            "created_at", "studio_url", "video_url", "autopilot")  # fmt: skip
    found = {k: full[k] for k in keep if full.get(k) is not None}
    if full.get("render"):
        found["render_status"] = full["render"]["status"]
    return found


def http_endpoint(server: MCPServer) -> StreamableHTTPASGIApp:
    """The server's streamable-HTTP endpoint, to mount behind mcp_access.Gate. Stateless and
    answering in plain JSON: each call stands alone (a restart drops nothing), and no
    event stream has to pass through Cloudflare. The Gate checks every request, so the
    SDK's localhost-only Host check would only refuse the public address."""
    server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return StreamableHTTPASGIApp(server.session_manager)


class Studio:
    """The MCP server over the studio's operations. `post_reel(output_id, channel_id,
    caption, account, session, request)` is the Jobs page's Post Reel; `base_of(request)`
    the site's address as the caller reached it (request None: over stdio)."""

    def __init__(self, ops, *, make_session, settings, base_of, post_reel):
        self.ops, self.make_session, self.settings = ops, make_session, settings
        self.base_of, self.post_reel = base_of, post_reel
        self.limit = Hits(CALLS_PER_MINUTE, 60)  # per account, whichever way in
        # The account `aiedit mcp --user` acts as, over stdio; never used for HTTP requests.
        self.local: Caller | None = None
        self.server = MCPServer(
            "agentic-editor-animations",
            title="Agentic AI Editor: Animations",
            instructions=INSTRUCTIONS,
            version="1",
        )
        self._add_tools()

    # ---- calling the studio as the caller ----------------------------------------------

    def caller(self, ctx: Context) -> tuple[Caller, Any]:
        request = ctx.request_context.request
        if request is not None:  # over HTTP: only ever the Gate's caller
            found = getattr(getattr(request, "state", None), "mcp_caller", None)
            if found is None:
                raise ToolError("not signed in")
            return found, request
        if self.local is None:
            raise ToolError("not signed in")
        return self.local, None

    def base(self, request) -> str:
        if request is not None:
            return self.base_of(request)
        local = self.settings.public_url or f"http://localhost:{self.settings.port}"
        return local.rstrip("/")

    def page_request(self, request):
        """What an operation that needs the site's address (approve) gets: the HTTP request,
        or over stdio something with the configured public address."""
        if request is not None:
            return request
        return SimpleNamespace(base_url=self.base(None) + "/")

    async def call(
        self,
        ctx: Context,
        work,
        *,
        posting: bool = False,
        counted: bool = True,
        queues: Literal["writing", "rendering"] | None = None,
    ):
        """`work(account, session, request)` in a worker thread, as the caller's account; a
        refusal of the studio's becomes the tool's error. Work it `queues` waits for the
        account's earlier work once MAX_WORKING of it is under way."""
        caller, request = self.caller(ctx)
        if posting and not caller.posting:
            raise ToolError(POSTING_OFF)
        if counted and not self.limit.allow(caller.account_id):
            raise ToolError("too many calls in a minute; wait a little")

        def run():
            with self.make_session() as session:
                account = session.get(Account, caller.account_id)
                if account is None:
                    raise ToolError("this account no longer exists")
                if queues:
                    self.wait_turn(account, session, queues)
                try:
                    return work(account, session, request)
                except HTTPException as exc:
                    raise ToolError(str(exc.detail)) from None
                except ValidationError as exc:
                    problems = "; ".join(
                        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                    )
                    raise ToolError(f"not valid: {problems}") from None

        return await anyio.to_thread.run_sync(run), request

    @staticmethod
    def wait_turn(account, session, queues: str) -> None:
        if queues == "rendering":
            busy = (
                select(func.count())
                .select_from(Job)
                .where(
                    Job.owner_id == account.id,
                    Job.source.startswith(studio.SOURCE),
                    Job.status.in_(("queued", "running")),
                )
            )
            what = "reels are rendering"
        else:
            busy = (
                select(func.count())
                .select_from(Animation)
                .where(Animation.owner_id == account.id, Animation.status.in_(WORKING))
            )
            what = "reels are being written"
        count = session.scalar(busy)
        if count >= MAX_WORKING:
            raise ToolError(f"{count} {what} already; wait for them to finish")

    async def shown(self, ctx: Context, work, **kwargs) -> dict:
        view, request = await self.call(ctx, work, **kwargs)
        return readable(view, self.base(request))

    # ---- the tools -----------------------------------------------------------------------

    def _add_tools(self) -> None:
        ops, tool = self.ops, self.server.tool

        @tool(annotations=READ)
        async def list_animations(
            ctx: Context,
            limit: Annotated[int, Field(ge=1, le=100, description="How many, newest first.")] = 20,
        ) -> dict:
            """The account's animations, newest first: title, status, length and links."""
            views, request = await self.call(ctx, lambda a, s, r: ops.list_animations(a, s))
            base = self.base(request)
            return {"animations": [summary(v, base) for v in views[:limit]]}

        @tool(annotations=READ)
        async def get_animation(
            ctx: Context,
            animation_id: AnimationId,
            include_raw_plan: Annotated[
                bool,
                Field(description="Also the plan as stored, to edit with edit_animation_plan."),
            ] = False,
        ) -> dict:
            """One animation: its status, the script scene by scene (narration and what each
            scene shows), the post caption, sources, notes, the render and its links."""
            view, request = await self.call(
                ctx, lambda a, s, r: ops.get_animation(animation_id, a, s)
            )
            return readable(view, self.base(request), raw_plan=include_raw_plan)

        @tool(annotations=READ)
        async def wait_for_animation(
            ctx: Context,
            animation_id: AnimationId,
            max_seconds: Annotated[int, Field(ge=1, le=WAIT_MOST)] = 40,
        ) -> dict:
            """Waits while the animation is being written, changed, painted, voiced or
            rendered, up to max_seconds, then gives it as get_animation does. Call it
            again while `next` says it is still working."""
            end = time.monotonic() + max_seconds
            counted = True
            while True:
                view, request = await self.call(
                    ctx, lambda a, s, r: ops.get_animation(animation_id, a, s), counted=counted
                )
                counted = False
                if not working(view) or time.monotonic() >= end:
                    found = readable(view, self.base(request))
                    found["still_working"] = working(view)
                    return found
                await anyio.sleep(min(2.0, max(0.0, end - time.monotonic())))

        @tool(annotations=READ)
        async def list_voices(ctx: Context) -> dict:
            """The narration voices: id, name, kind (narrator, calm, energetic) and sound."""
            voices, _ = await self.call(ctx, lambda a, s, r: ops.animation_voices(a, s))
            return {"voices": voices}

        @tool(annotations=CHANGE)
        async def create_animation(
            ctx: Context,
            idea: Annotated[
                str,
                Field(
                    min_length=3,
                    max_length=300,
                    description="What the reel is about, in a few words or one line.",
                ),
            ],
            kind: Annotated[Kind, Field(description="'auto' lets the writer choose.")] = "auto",
            style: Annotated[
                Literal["auto", "glow", "paper", "story", "kinetic", "stick"],
                Field(
                    description="glow = Neon Glow, paper = Clean Paper, story = Character "
                    "story, kinetic = Bold kinetic text, stick = Stick figure; 'auto' by the "
                    "content."
                ),
            ] = "auto",
            voice: Annotated[str, Field(description=VOICE_HELP)] = "auto",
            length: Annotated[
                Length,
                Field(description="short 20-35 s, medium 35-60 s, long 60-90 s."),
            ] = "auto",
            painted_pictures: Annotated[
                bool,
                Field(description="An AI paints each scene's picture (a few minutes more)."),
            ] = True,
        ) -> dict:
            """Starts a new animation from an idea: the writer finds sources, writes the
            script, the pictures are painted and the voice recorded. Takes 1-5 minutes;
            call wait_for_animation next."""

            def work(account, session, request):
                body = AnimationRequest(
                    idea=idea, kind=kind, style=style, voice=voice, length=length,
                    images=painted_pictures,
                )  # fmt: skip
                return ops.create_animation(body, account, session)

            return await self.shown(ctx, work, queues="writing")

        @tool(annotations=CHANGE)
        async def change_animation(
            ctx: Context,
            animation_id: AnimationId,
            request: Annotated[
                str,
                Field(
                    max_length=500,
                    description="What to change, in plain words (e.g. 'a darker ending', "
                    "'scene 3 has the wrong year, it is 1992'). May be empty with scene_number.",
                ),
            ] = "",
            scene_number: Annotated[
                int | None,
                Field(ge=1, le=21, description="Write only this scene again (numbered from 1)."),
            ] = None,
        ) -> dict:
            """Has the writer change the reel as asked, or write one scene again; then it
            is voiced again. Undo with undo_last_change. Call wait_for_animation next."""
            body = ChangeRequest(
                request=request, scene=None if scene_number is None else scene_number - 1
            )
            return await self.shown(
                ctx,
                lambda a, s, r: ops.change_animation(animation_id, body, a, s),
                queues="writing",
            )

        @tool(annotations=CHANGE)
        async def edit_animation_plan(
            ctx: Context,
            animation_id: AnimationId,
            plan: Annotated[
                dict[str, Any],
                Field(
                    description="The whole plan as get_animation(include_raw_plan=true) gave "
                    "it, with your exact edits (narration, on-screen text, scene order...)."
                ),
            ],
        ) -> dict:
            """Saves exact edits to the plan, as the scene editor does: it is checked, then
            the voice is recorded again. Call wait_for_animation next."""
            return await self.shown(
                ctx,
                lambda a, s, r: ops.edit_animation(animation_id, PlanEdit(plan=plan), a, s),
                queues="writing",
            )

        @tool(annotations=CHANGE)
        async def set_style_and_voice(
            ctx: Context,
            animation_id: AnimationId,
            style: Annotated[Style | None, Field(description="A new look.")] = None,
            voice: Annotated[str | None, Field(description=VOICE_HELP)] = None,
            painted_pictures: Annotated[
                bool | None, Field(description="Turn the painted pictures on or off.")
            ] = None,
        ) -> dict:
            """Changes the look, the narration voice (records it again) or the painted
            pictures, without writing anything new. Call wait_for_animation next."""
            body = StudioChoice(style=style, voice=voice, images=painted_pictures)
            return await self.shown(
                ctx, lambda a, s, r: ops.set_animation(animation_id, body, a, s), queues="writing"
            )

        @tool(annotations=CHANGE)
        async def repaint_pictures(
            ctx: Context,
            animation_id: AnimationId,
            scene_numbers: Annotated[
                list[int] | None,
                Field(description="Scenes to paint again (from 1); none given: every picture."),
            ] = None,
        ) -> dict:
            """Paints the scenes' pictures again (new versions of the same descriptions).
            Call wait_for_animation next."""

            def work(account, session, request):
                current = ops.get_animation(animation_id, account, session)
                plan = copy.deepcopy(current.get("plan"))
                if not plan:
                    raise ToolError("there is no draft yet")
                if plan.get("style") in images.UNPAINTED:
                    raise ToolError("this style draws its scenes; it has no painted pictures")
                if not current.get("images"):
                    raise ToolError(
                        "painted pictures are off for this reel; turn them on with "
                        "set_style_and_voice(painted_pictures=true)"
                    )
                wanted = set(scene_numbers or range(1, len(plan["scenes"]) + 1))
                again = 0
                for n, scene in enumerate(plan["scenes"], 1):
                    visual = scene.setdefault("visual", {})
                    if n in wanted and visual.get("art"):
                        visual["art_seed"] = int(visual.get("art_seed") or 0) + 1
                        again += 1
                if not again:
                    raise ToolError("none of those scenes has a painted picture")
                return ops.edit_animation(animation_id, PlanEdit(plan=plan), account, session)

            return await self.shown(ctx, work, queues="writing")

        @tool(annotations=CHANGE)
        async def undo_last_change(ctx: Context, animation_id: AnimationId) -> dict:
            """Goes back to the version before the last change."""
            return await self.shown(
                ctx, lambda a, s, r: ops.undo_animation(animation_id, a, s), queues="writing"
            )

        @tool(annotations=CHANGE)
        async def retry_animation(ctx: Context, animation_id: AnimationId) -> dict:
            """Tries a failed draft or change again."""
            return await self.shown(
                ctx, lambda a, s, r: ops.retry_animation(animation_id, a, s), queues="writing"
            )

        @tool(annotations=CHANGE)
        async def render_animation(ctx: Context, animation_id: AnimationId) -> dict:
            """Renders the ready animation into the final video (an ordinary job in Jobs,
            with the reel's caption). Takes about a minute; call wait_for_animation next."""
            return await self.shown(
                ctx, lambda a, s, r: ops.render_animation(animation_id, a, s), queues="rendering"
            )

        @tool(annotations=CHANGE)
        async def skip_autopilot_reel(ctx: Context, animation_id: AnimationId) -> dict:
            """Turns down a reel the autopilot made that waits for approval: not posted."""
            return await self.shown(ctx, lambda a, s, r: ops.skip_animation(animation_id, a, s))

        # ---- posting: only when the person allowed it for this connection, and asked --------

        @tool(annotations=READ)
        async def list_instagram_pages(ctx: Context) -> dict:
            """The Instagram pages connected to this account, to post a reel to (needs
            posting allowed for this connection)."""

            def work(account, session, request):
                pages = session.scalars(
                    select(InstagramChannel).where(InstagramChannel.owner_id == account.id)
                )
                return [{"id": p.id, "username": p.username} for p in pages]

            pages, _ = await self.call(ctx, work, posting=True)
            return {"pages": pages}

        @tool(annotations=POST)
        async def post_to_instagram(
            ctx: Context,
            animation_id: AnimationId,
            page: Annotated[
                str, Field(description="The page's username or id (list_instagram_pages).")
            ],
            confirm: Annotated[
                bool, Field(description="true only after the person said to post this reel now.")
            ] = False,
            caption: Annotated[
                str | None,
                Field(max_length=2200, description="The post's caption; none: the reel's own."),
            ] = None,
        ) -> dict:
            """Posts the animation's rendered video to one of the account's Instagram pages
            as a reel, right now. Only when the person clearly asked for it."""
            caller, request = self.caller(ctx)
            if not caller.posting:
                raise ToolError(POSTING_OFF)
            if not confirm:
                raise ToolError(NOT_CONFIRMED)
            if request is None and not self.settings.public_url:
                raise ToolError("posting from here needs PUBLIC_URL, the site's public address")

            def find(account, session, request):
                view = ops.get_animation(animation_id, account, session)
                rendered = view.get("render") or {}
                if rendered.get("status") != "done" or not rendered.get("output_id"):
                    raise ToolError("render it first (render_animation), then post")
                wanted = page.strip().lstrip("@").lower()
                channel = next(
                    (
                        c
                        for c in session.scalars(
                            select(InstagramChannel).where(InstagramChannel.owner_id == account.id)
                        )
                        if wanted in (c.id.lower(), c.username.lower())
                    ),
                    None,
                )
                if channel is None:
                    raise ToolError(f"no Instagram page {page!r} is connected to this account")
                output = session.get(Output, rendered["output_id"])
                text = caption
                if text is None:
                    text = (output.caption if output else "") or (view.get("plan") or {}).get(
                        "caption", ""
                    )
                return rendered["output_id"], channel.id, text

            (output_id, channel_id, text), _ = await self.call(ctx, find, posting=True)
            with self.make_session() as session:
                account = session.get(Account, caller.account_id)
                try:
                    posted = await self.post_reel(
                        output_id, channel_id, text, account, session, self.page_request(request)
                    )
                except HTTPException as exc:
                    raise ToolError(str(exc.detail)) from None
            return {"posted": True, **posted}

        @tool(annotations=POST)
        async def approve_autopilot_reel(
            ctx: Context,
            animation_id: AnimationId,
            confirm: Annotated[
                bool, Field(description="true only after the person said to approve it.")
            ] = False,
        ) -> dict:
            """Approves a reel the autopilot made: it posts now to the autopilot's page (if
            one is chosen) and counts toward posting by itself. Only when the person
            clearly asked for it."""
            caller, _ = self.caller(ctx)
            if not caller.posting:
                raise ToolError(POSTING_OFF)
            if not confirm:
                raise ToolError(NOT_CONFIRMED)
            return await self.shown(
                ctx,
                lambda a, s, r: ops.approve_animation(animation_id, a, s, self.page_request(r)),
                posting=True,
            )
