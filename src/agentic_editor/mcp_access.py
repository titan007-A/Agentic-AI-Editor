"""Who Claude is when it uses the Animations tab over MCP (animate/mcp_tools.py).

Two ways in, each acting as one account and nothing more:

- a key the person makes on the Animations tab and gives Claude Code
  (`Authorization: Bearer aie_...`);
- OAuth, as claude.ai's custom connectors (and Claude Code's /mcp sign-in) do it: the app
  registers itself, sends the person to the page here that asks them (/connect/), and once
  they allow it gets a token for their account. The MCP SDK's handlers speak the protocol
  (registration, PKCE, the token endpoint); `Provider` keeps the clients, codes and tokens.

Only hashes of keys and tokens are stored. A changed password ends every way in, like the
signed-in sessions; removing the account removes them. Posting reels is off unless the
person turned it on for that way in.
"""

import asyncio
import hashlib
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import HTTPException
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import build_metadata, create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from agentic_editor import features
from agentic_editor.auth import LoginGuard
from agentic_editor.db import McpClient, McpToken
from agentic_editor.db import User as Account

log = logging.getLogger(__name__)

FEATURE = "animation_studio"
PREFIX = "aie_"  # every key and token starts so, to tell them apart in a config file
SCOPE = "animations"
PATH = "/mcp"
ACCESS_SECONDS = 24 * 3600  # an OAuth access token; the app refreshes it by itself
CODE_SECONDS = 300
ASK_SECONDS = 600  # how long the page asking the person stays valid
MAX_CLIENTS = 200  # registered apps kept; the oldest unused ones go first
MAX_KEYS = 20  # ways in per account
SEEN_EVERY = 60  # seconds between notes of a way in being used
CALLS_PER_MINUTE = 60  # tool calls one way in may make
ISSUER = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_secret() -> str:
    return PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Caller:
    """The account a request acts as, and what that way in may do."""

    account_id: str
    token_id: str | None
    posting: bool
    client_id: str | None = None  # the app, for a connection made through OAuth


class TokenGuard(LoginGuard):
    """Unknown keys from one address: after many, it waits before trying again. More
    lenient than logins: claude.ai's calls come from Anthropic's few shared addresses."""

    FREE_TRIES = 30
    FIRST_WAIT = 10.0


class CallLimit:
    """At most `per_minute` tool calls a minute for each way in: a model stuck in a loop
    can't fill the queue."""

    def __init__(self, per_minute: int = CALLS_PER_MINUTE, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self._calls: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            calls = self._calls.setdefault(key, deque())
            while calls and calls[0] <= now - 60:
                calls.popleft()
            if len(calls) >= self.per_minute:
                return False
            calls.append(now)
            return True


def _naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _expired(row: McpToken) -> bool:
    end = row.expires_at
    if end is None:
        return False
    if end.tzinfo is not None:
        end = end.astimezone(UTC).replace(tzinfo=None)
    return end <= _naive_now()


def check(session: Session, secret: str) -> Caller | None:
    """The caller a key or access token stands for; None when it is unknown, expired, or
    from before its account's password changed."""
    if not secret.startswith(PREFIX):
        return None
    row = session.scalar(select(McpToken).where(McpToken.token_hash == digest(secret)))
    if row is None or _expired(row):
        return None
    account = session.get(Account, row.owner_id)
    if account is None or account.session_version != row.version:
        return None
    seen = row.last_used_at
    if seen is None or (_naive_now() - seen.replace(tzinfo=None)).total_seconds() > SEEN_EVERY:
        row.last_used_at = _naive_now()
        session.commit()
    return Caller(account.id, row.id, row.posting, row.client_id)


def make_key(session: Session, account: Account, label: str, posting: bool) -> str:
    """A new key for `account`, returned this once: only its hash is kept."""
    secret = new_secret()
    session.add(
        McpToken(
            owner_id=account.id, label=label, kind="key", token_hash=digest(secret),
            tail=secret[-4:], posting=posting, version=account.session_version,
        )
    )  # fmt: skip
    session.commit()
    return secret


def view(row: McpToken, clients: dict[str, str]) -> dict:
    return {
        "id": row.id,
        "label": row.label,
        "kind": row.kind,
        "app": clients.get(row.client_id or "", "") if row.kind == "oauth" else "",
        "tail": row.tail,
        "posting": row.posting,
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
    }


class Code(AuthorizationCode):
    posting: bool = False


@dataclass
class Asking:
    """An app's request for a connection, waiting for the person to allow it."""

    client_id: str
    app: str
    params: AuthorizationParams
    made: float = field(default_factory=time.time)


class Provider:
    """The OAuth side (mcp.server.auth.provider.OAuthAuthorizationServerProvider): clients
    and tokens in the database; requests waiting for the person and codes in memory, so a
    restart in the middle of connecting only means connecting again."""

    def __init__(self, make_session: sessionmaker, clock=time.time):
        self.make_session, self.clock = make_session, clock
        self.asking: dict[str, Asking] = {}
        self.codes: dict[str, Code] = {}
        self._lock = threading.Lock()

    # ---- registered apps ---------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        def find() -> OAuthClientInformationFull | None:
            with self.make_session() as session:
                row = session.get(McpClient, client_id[:64])
                return OAuthClientInformationFull.model_validate(row.info) if row else None

        return await asyncio.to_thread(find)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        def keep() -> None:
            with self.make_session() as session:
                session.add(
                    McpClient(
                        client_id=client_info.client_id,
                        info=client_info.model_dump(mode="json", exclude_none=True),
                    )
                )
                session.flush()
                extra = session.scalar(select(func.count()).select_from(McpClient)) - MAX_CLIENTS
                if extra > 0:  # anyone may register: the oldest that never connected go
                    used = select(McpToken.client_id).where(McpToken.client_id.is_not(None))
                    oldest = session.scalars(
                        select(McpClient.client_id)
                        .where(McpClient.client_id.not_in(used))
                        .order_by(McpClient.created_at)
                        .limit(extra)
                    ).all()
                    session.execute(delete(McpClient).where(McpClient.client_id.in_(oldest)))
                session.commit()

        await asyncio.to_thread(keep)

    # ---- asking the person -------------------------------------------------------------

    def _forget_old(self) -> None:
        now = self.clock()
        for key in [k for k, a in self.asking.items() if a.made < now - ASK_SECONDS]:
            del self.asking[key]
        for key in [k for k, c in self.codes.items() if c.expires_at < now]:
            del self.codes[key]

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        ask = secrets.token_urlsafe(24)
        app = (client.client_name or "").strip()[:80] or "An app"
        with self._lock:
            self._forget_old()
            self.asking[ask] = Asking(client.client_id or "", app, params, self.clock())
        return f"/connect/?request={ask}"

    def asked(self, ask: str) -> Asking:
        with self._lock:
            self._forget_old()
            found = self.asking.get(ask)
        if found is None:
            raise HTTPException(404, "this request ran out; start connecting again from the app")
        return found

    def answer(self, ask: str, account: Account | None, posting: bool = False) -> str:
        """Where the person's browser goes next: back to the app with a code when they
        allowed it (`account`), or with access_denied."""
        found = self.asked(ask)
        with self._lock:
            self.asking.pop(ask, None)
            if account is None:
                return construct_redirect_uri(
                    str(found.params.redirect_uri), error="access_denied", state=found.params.state
                )
            code = secrets.token_urlsafe(32)
            self.codes[code] = Code(
                code=code,
                scopes=found.params.scopes or [SCOPE],
                expires_at=self.clock() + CODE_SECONDS,
                client_id=found.client_id,
                code_challenge=found.params.code_challenge,
                redirect_uri=found.params.redirect_uri,
                redirect_uri_provided_explicitly=found.params.redirect_uri_provided_explicitly,
                resource=found.params.resource,
                subject=account.id,
                posting=posting,
            )
        return construct_redirect_uri(
            str(found.params.redirect_uri), code=code, state=found.params.state
        )

    # ---- tokens ------------------------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Code | None:
        with self._lock:
            self._forget_old()
            found = self.codes.get(authorization_code)
        return found if found and found.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: Code
    ) -> OAuthToken:
        with self._lock:
            if self.codes.pop(authorization_code.code, None) is None:
                raise TokenError("invalid_grant", "that code was used already")

        def connect() -> OAuthToken:
            with self.make_session() as session:
                account = session.get(Account, authorization_code.subject or "")
                if account is None:
                    raise TokenError("invalid_grant", "the account is gone")
                # Connecting the same app again replaces its earlier connection.
                session.execute(
                    delete(McpToken).where(
                        McpToken.owner_id == account.id, McpToken.client_id == client.client_id
                    )
                )
                access, refresh = new_secret(), new_secret()
                session.add(
                    McpToken(
                        owner_id=account.id, label=(client.client_name or "App")[:80],
                        kind="oauth", client_id=client.client_id, token_hash=digest(access),
                        refresh_hash=digest(refresh), posting=authorization_code.posting,
                        version=account.session_version,
                        expires_at=_naive_now() + timedelta(seconds=ACCESS_SECONDS),
                    )
                )  # fmt: skip
                session.commit()
                log.info("mcp: %s connected for %s", client.client_name, account.username)
                return _token(access, refresh, authorization_code.scopes)

        return await asyncio.to_thread(connect)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        def find() -> RefreshToken | None:
            with self.make_session() as session:
                row = session.scalar(
                    select(McpToken).where(McpToken.refresh_hash == digest(refresh_token))
                )
                if row is None or row.client_id != client.client_id:
                    return None
                account = session.get(Account, row.owner_id)
                if account is None or account.session_version != row.version:
                    return None
                return RefreshToken(
                    token=refresh_token, client_id=row.client_id, scopes=[SCOPE],
                    subject=row.owner_id,
                )  # fmt: skip

        return await asyncio.to_thread(find)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        def rotate() -> OAuthToken:
            with self.make_session() as session:
                row = session.scalar(
                    select(McpToken).where(McpToken.refresh_hash == digest(refresh_token.token))
                )
                if row is None:
                    raise TokenError("invalid_grant", "that connection was removed")
                access, refresh = new_secret(), new_secret()
                row.token_hash, row.refresh_hash = digest(access), digest(refresh)
                row.expires_at = _naive_now() + timedelta(seconds=ACCESS_SECONDS)
                session.commit()
                return _token(access, refresh, scopes or [SCOPE])

        return await asyncio.to_thread(rotate)

    async def load_access_token(self, token: str) -> AccessToken | None:
        def find() -> AccessToken | None:
            with self.make_session() as session:
                caller = check(session, token)
                if caller is None:
                    return None
                return AccessToken(
                    token=token, client_id=caller.client_id or "", scopes=[SCOPE],
                    subject=caller.account_id,
                )  # fmt: skip

        return await asyncio.to_thread(find)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        def remove() -> None:
            hashed = digest(token.token)
            with self.make_session() as session:
                session.execute(
                    delete(McpToken).where(
                        (McpToken.token_hash == hashed) | (McpToken.refresh_hash == hashed)
                    )
                )
                session.commit()

        await asyncio.to_thread(remove)


def _token(access: str, refresh: str, scopes: list[str]) -> OAuthToken:
    return OAuthToken(
        access_token=access, expires_in=ACCESS_SECONDS, refresh_token=refresh,
        scope=" ".join(scopes),
    )  # fmt: skip


def resource_metadata_url(base: str) -> str:
    return f"{base}/.well-known/oauth-protected-resource{PATH}"


class Gate:
    """In front of the MCP endpoint: lets a request through only with a key or token of an
    account that has the studio, and notes who it is (request.state.mcp_caller) for the
    tools. Anything else gets 401 with the address of the sign-in details (RFC 9728), which
    is how claude.ai finds the way to connect."""

    def __init__(self, app: ASGIApp, make_session: sessionmaker, base_of, guard: TokenGuard):
        self.app, self.make_session, self.base_of, self.guard = app, make_session, base_of, guard

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        client = request.client.host if request.client else ""
        wait = self.guard.wait(client)
        if wait:
            response = JSONResponse(
                {"error": "too many unknown keys; try again later"},
                status_code=429,
                headers={"Retry-After": str(int(wait) + 1)},
            )
            await response(scope, receive, send)
            return
        header = request.headers.get("authorization", "")
        secret = header[7:].strip() if header[:7].lower() == "bearer " else ""
        caller = await asyncio.to_thread(self._check, secret) if secret else None
        if caller is None:
            if secret:
                self.guard.failed(client)
            await self._refuse(request, scope, receive, send, bool(secret))
            return
        allowed = await asyncio.to_thread(self._studio_on, caller.account_id)
        if not allowed:
            response = JSONResponse(
                {"error": "the animation studio isn't on for this account"}, status_code=403
            )
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["mcp_caller"] = caller
        await self.app(scope, receive, send)

    def _check(self, secret: str) -> Caller | None:
        with self.make_session() as session:
            return check(session, secret)

    def _studio_on(self, account_id: str) -> bool:
        with self.make_session() as session:
            account = session.get(Account, account_id)
            return account is not None and features.enabled(account, FEATURE, session)

    async def _refuse(self, request: Request, scope, receive, send, had_one: bool) -> None:
        where = resource_metadata_url(self.base_of(request))
        challenge = f'Bearer resource_metadata="{where}"'
        if had_one:
            challenge = (
                'Bearer error="invalid_token", error_description="unknown or expired key", '
                f'resource_metadata="{where}"'
            )
        response = JSONResponse(
            {"error": "invalid_token" if had_one else "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": challenge},
        )
        await response(scope, receive, send)


class KeyRequest(BaseModel):
    label: str = Field(default="Claude Code", min_length=1, max_length=80)
    posting: bool = False


class KeyChange(BaseModel):
    posting: bool


class Answer(BaseModel):
    allow: bool
    posting: bool = False


def install(app, *, User, DB, make_session: sessionmaker, base_of, endpoint: ASGIApp) -> None:
    """The MCP endpoint (behind the Gate), OAuth's endpoints and sign-in details, the
    page's questions and the Animations tab's list of ways in. `base_of(request)` is the
    site's address as the caller reached it."""
    provider = app.state.mcp_provider = Provider(make_session)
    guard = app.state.mcp_guard = TokenGuard()
    gate = Gate(endpoint, make_session, base_of, guard)
    app.router.routes.extend([Route(PATH, endpoint=gate), Route(PATH + "/", endpoint=gate)])

    # Registration, /authorize, /token and /revoke from the SDK. Their sign-in details are
    # written per request below instead: the address differs with how the site is reached
    # (the public link, the LAN, localhost), and the issuer must be that address.
    registration = ClientRegistrationOptions(enabled=True, default_scopes=[SCOPE])
    revocation = RevocationOptions(enabled=True)
    routes = create_auth_routes(
        provider, AnyHttpUrl("http://localhost"), None, registration, revocation
    )
    app.router.routes.extend(r for r in routes if not r.path.startswith("/.well-known/"))

    def studio_user(account, session: Session) -> None:
        if not features.enabled(account, FEATURE, session):
            raise HTTPException(403, "the animation studio isn't on for this account yet")

    async def server_details(request: Request) -> JSONResponse:
        # The issuer exactly as the address the app was given: "https://site", no "/".
        issuer = ISSUER.validate_python(base_of(request))
        metadata = build_metadata(issuer, None, registration, revocation)
        # Apps like Claude Code connect without a client secret (PKCE guards the code).
        metadata.token_endpoint_auth_methods_supported = [
            "client_secret_post", "client_secret_basic", "none",
        ]  # fmt: skip
        metadata.scopes_supported = [SCOPE]
        return await MetadataHandler(metadata).handle(request)

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    async def authorization_server(request: Request) -> JSONResponse:
        return await server_details(request)

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource" + PATH, include_in_schema=False)
    def protected_resource(request: Request) -> JSONResponse:
        base = base_of(request)
        return JSONResponse(
            {
                "resource": base + PATH,
                "authorization_servers": [base],
                "scopes_supported": [SCOPE],
                "bearer_methods_supported": ["header"],
                "resource_name": "Agentic AI Editor: Animations",
            },
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # ---- the page that asks the person (web /connect/) ---------------------------------

    @app.get("/api/mcp/requests/{ask}")
    def asking(ask: str, account: User, session: DB) -> dict:
        found = provider.asked(ask)
        return {
            "app": found.app,
            "goes_to": urlparse(str(found.params.redirect_uri)).netloc,
            "account": account.username,
            "studio": features.enabled(account, FEATURE, session),
        }

    @app.post("/api/mcp/requests/{ask}")
    def answer(ask: str, body: Answer, account: User, session: DB) -> dict:
        if body.allow:
            studio_user(account, session)
        return {"redirect": provider.answer(ask, account if body.allow else None, body.posting)}

    # ---- the Animations tab's list of ways in ------------------------------------------

    def listing(account, session: Session, request: Request, made: str | None = None) -> dict:
        rows = session.scalars(
            select(McpToken)
            .where(McpToken.owner_id == account.id)
            .order_by(McpToken.created_at.desc())
        ).all()
        ids = [r.client_id for r in rows if r.client_id]
        clients = {
            c.client_id: str(c.info.get("client_name") or "")
            for c in session.scalars(select(McpClient).where(McpClient.client_id.in_(ids)))
        }
        found = {"url": base_of(request) + PATH, "connections": [view(r, clients) for r in rows]}
        if made:
            found["key"] = made
        return found

    def find(key_id: str, account, session: Session) -> McpToken:
        row = session.get(McpToken, key_id)
        if row is None or row.owner_id != account.id:
            raise HTTPException(404, "no such connection")
        return row

    @app.get("/api/mcp/connections")
    def connections(account: User, session: DB, request: Request) -> dict:
        studio_user(account, session)
        return listing(account, session, request)

    @app.post("/api/mcp/keys")
    def new_key(body: KeyRequest, account: User, session: DB, request: Request) -> dict:
        studio_user(account, session)
        count = session.scalar(
            select(func.count()).select_from(McpToken).where(McpToken.owner_id == account.id)
        )
        if count >= MAX_KEYS:
            raise HTTPException(409, f"{MAX_KEYS} connections already; remove one first")
        secret = make_key(session, account, " ".join(body.label.split()), body.posting)
        return listing(account, session, request, made=secret)

    @app.patch("/api/mcp/connections/{key_id}")
    def change_key(key_id: str, body: KeyChange, account: User, session: DB) -> dict:
        row = find(key_id, account, session)
        row.posting = body.posting
        session.commit()
        return {"ok": True, "posting": row.posting}

    @app.delete("/api/mcp/connections/{key_id}")
    def remove_key(key_id: str, account: User, session: DB) -> dict:
        session.delete(find(key_id, account, session))
        session.commit()
        return {"ok": True}
