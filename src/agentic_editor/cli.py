import argparse
import logging
import os
import subprocess
import sys

from agentic_editor import ffmpeg, logs, pipeline
from agentic_editor.backends import detect
from agentic_editor.presets import load_presets
from agentic_editor.settings import get_settings

log = logging.getLogger(__name__)

# A short CC BY 3.0 performance from Wikimedia Commons, also used by the end-to-end test.
BENCHMARK_SAMPLE = "https://commons.wikimedia.org/wiki/File:Breathe_by_Rockbitch_(circa_2000).webm"


def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    available = load_presets(settings.presets_dir)
    missing = [name for name in args.preset if name not in available]
    if missing:
        print(f"unknown preset(s): {', '.join(missing)}; available: {', '.join(available)}")
        return 2
    backends = detect(settings)
    presets = [available[n] for n in args.preset]
    failed = 0
    for source in args.source:
        try:
            result = pipeline.run(source, presets, settings, backends, args.notes)
        except Exception as exc:
            # One video that can't be edited doesn't hold up the rest of the batch.
            log.debug("editing %s failed", source, exc_info=True)
            print(f"{source}: failed: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        for output in result.outputs:
            clips = ", ".join(f"{c.start:.2f}-{c.end:.2f}" for c in output.edl.clips)
            print(f"{output.preset}: {output.path}")
            print(
                f"  {output.edl.duration:.1f}s from {clips}, planned by {output.planner}, "
                f"{output.renders} render(s)"
            )
            if output.edl.title:
                print(f"  title: {output.edl.title}")
            if output.doubts:
                print(f"  needs a look ({output.confidence:.0%} sure):")
                for doubt in output.doubts:
                    print(f"    {doubt}")
            else:
                print("  ready to post")
        for failure in result.failures:
            print(f"failed: {failure}")
            failed += 1
    return 1 if failed else 0


def _learn(args: argparse.Namespace) -> int:
    import yaml

    from agentic_editor import style
    from agentic_editor.presets import save_preset

    settings = get_settings()
    existing = load_presets(settings.presets_dir)
    if args.save and args.name in existing:
        print(f"a preset called {args.name} already exists; pick another --name")
        return 2
    draft = style.learn(args.source, settings, detect(settings), args.name)
    print(f"{draft.preset.label} ({draft.preset.name})")
    for note in draft.notes:
        print(f"  {note}")
    if args.save:
        if draft.preset.name in existing:
            print(f"a preset called {draft.preset.name} already exists; pick a --name to save")
            return 2
        print(f"saved {save_preset(draft.preset, settings.presets_dir)}")
    else:
        data = draft.preset.model_dump(mode="json")
        print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return 0


def _benchmark(args: argparse.Namespace) -> int:
    from agentic_editor import benchmark

    settings = get_settings()
    presets = load_presets(settings.presets_dir)
    if args.preset not in presets:
        print(f"unknown preset {args.preset}; available: {', '.join(presets)}")
        return 2
    backends = detect(settings)
    source = pipeline.resolve_source(args.source, settings)
    results = benchmark.run(source.path, presets[args.preset], settings, backends, not args.no_llm)
    print(benchmark.report(results, source.title, ffmpeg.probe(source.path).duration))
    return 0


def _fetch_models(args: argparse.Namespace) -> int:
    from agentic_editor.router import ModelRouter

    settings = get_settings()
    ModelRouter(settings, detect(settings)).fetch_models()
    print("all models are on this machine")
    return 0


def _backends(args: argparse.Namespace) -> int:
    print(detect(get_settings()).describe())
    return 0


def _presets(args: argparse.Namespace) -> int:
    for name, preset in load_presets(get_settings().presets_dir).items():
        print(
            f"{name:24} {preset.resolution:>10}  {preset.min_duration:g}-{preset.max_duration:g}s"
            f"  {preset.label}"
        )
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from agentic_editor.api import create_app
    from agentic_editor.backends import get_backends

    settings = get_settings()
    if not settings.password_hash:
        print("PASSWORD_HASH is not set. Run `aiedit hash-password` and put the result in .env.")
        return 2
    if not settings.secret_key:
        print("SECRET_KEY is not set; logins will not survive a restart.")
    get_backends()
    if sys.platform == "darwin":
        # Holds off sleep while on power, for as long as this process runs, so jobs and
        # overnight autopilot runs aren't cut short. The display may still sleep.
        subprocess.Popen(["/usr/bin/caffeinate", "-s", "-w", str(os.getpid())])
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)
    return 0


def _hash_password(args: argparse.Namespace) -> int:
    import getpass
    import secrets

    from agentic_editor.auth import hash_password

    password = getpass.getpass("New password: ")
    if len(password) < 8:
        print("use at least 8 characters")
        return 2
    if getpass.getpass("Again: ") != password:
        print("the passwords don't match")
        return 2
    print("Add these lines to .env:")
    print(f"PASSWORD_HASH='{hash_password(password)}'")
    print(f"SECRET_KEY='{secrets.token_hex(32)}'")
    return 0


def _ask_password() -> str | None:
    import getpass

    password = getpass.getpass("Password: ")
    if getpass.getpass("Again: ") != password:
        print("the passwords don't match")
        return None
    return password


def _user(args: argparse.Namespace) -> int:
    """Accounts from this computer: the only way to make or remove an admin, so a stolen
    web session can't hand out admin rights."""
    from sqlalchemy import select

    from agentic_editor import accounts
    from agentic_editor.db import Job, User, make_sessionmaker

    settings = get_settings()
    with make_sessionmaker(settings.database)() as session:
        try:
            if args.action == "list":
                for a in session.scalars(select(User).order_by(User.created_at)):
                    jobs = session.query(Job).filter(Job.owner_id == a.id).count()
                    seen = a.last_seen_at.strftime("%Y-%m-%d %H:%M") if a.last_seen_at else "never"
                    print(f"{a.username:24s} {a.role:7s} {jobs:4d} jobs  last seen {seen}")
                return 0
            if not args.name:
                print("give the account's username")
                return 2
            if args.action == "add":
                role = accounts.ADMIN if args.admin else accounts.MEMBER
                hashed = settings.password_hash if args.env_password else None
                if args.env_password and not hashed:
                    print("PASSWORD_HASH isn't set in .env")
                    return 2
                password = None if hashed else _ask_password()
                if password is None and hashed is None:
                    return 2
                user = accounts.create(
                    session, args.name, password, role, settings.presets_dir, password_hash=hashed
                )
                print(f"made {user.role} {user.username}")
                return 0
            user = accounts.find(session, args.name)
            if user is None:
                print(f"no account called {accounts.normal(args.name)}")
                return 2
            if args.action == "passwd":
                password = _ask_password()
                if password is None:
                    return 2
                accounts.set_password(session, user, password)
                print(f"new password for {user.username}; their sessions are signed out")
            elif args.action == "role":
                accounts.set_role(session, user, args.role, settings.presets_dir)
                print(f"{user.username} is now {user.role}")
            elif args.action == "remove":
                keeper = next((a for a in accounts.admins(session) if a.id != user.id), None)
                accounts.remove(session, user, settings.presets_dir, keep_work_for=keeper)
                heir = keeper.username if keeper else "nobody"
                print(f"removed {args.name}; their jobs went to {heir}")
        except accounts.AccountError as exc:
            print(exc)
            return 2
    return 0


def _relocate(args: argparse.Namespace) -> int:
    from agentic_editor.db import make_sessionmaker
    from agentic_editor.relocate import relocate
    from agentic_editor.settings import ROOT

    settings = get_settings()
    moved = relocate(
        make_sessionmaker(settings.database), settings.media_dir, args.old_root, str(ROOT)
    )
    print(
        f"moved {moved.outputs} video(s), {moved.sources} source(s) and "
        f"{moved.feed_items} feed item(s) from {args.old_root} to {ROOT}"
    )
    for folder in moved.missing_folders:
        print(f"folder feed with no folder here, point it at one on this machine: {folder}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiedit", description="Local agentic video editor")
    parser.add_argument("-v", "--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="edit one or more videos with one or more presets")
    run.add_argument("source", nargs="+", help="video URL or local file")
    run.add_argument("-p", "--preset", action="append", required=True)
    run.add_argument("-n", "--notes", help="an instruction in plain words")
    run.set_defaults(handler=_run)

    learn = commands.add_parser("learn", help="draft a preset from a reel with the look you want")
    learn.add_argument("source", help="the reel: URL or local file")
    learn.add_argument("--name", help="file name for the preset, e.g. raw-vocals-es")
    learn.add_argument("--save", action="store_true", help="save it to the presets folder")
    learn.set_defaults(handler=_learn)

    bench = commands.add_parser("benchmark", help="time transcription, separation, LLMs, render")
    bench.add_argument("source", nargs="?", default=BENCHMARK_SAMPLE, help="video URL or file")
    bench.add_argument("-p", "--preset", default="vocal-music-9x16", help="frame to render")
    bench.add_argument("--no-llm", action="store_true", help="skip the Ollama models")
    bench.set_defaults(handler=_benchmark)

    commands.add_parser("backends", help="show the detected backends").set_defaults(
        handler=_backends
    )
    commands.add_parser(
        "fetch-models", help="download the transcription, separation and face models"
    ).set_defaults(handler=_fetch_models)
    commands.add_parser("presets", help="list presets").set_defaults(handler=_presets)
    commands.add_parser("serve", help="run the web app and job worker").set_defaults(handler=_serve)
    commands.add_parser("hash-password", help="hash a login password for .env").set_defaults(
        handler=_hash_password
    )
    user = commands.add_parser("user", help="accounts: add, list, passwd, role, remove")
    user.add_argument("action", choices=["add", "list", "passwd", "role", "remove"])
    user.add_argument("name", nargs="?", help="the account's username")
    user.add_argument("role", nargs="?", choices=["admin", "member"], help="for role")
    user.add_argument("--admin", action="store_true", help="add: make an admin, not an employee")
    user.add_argument(
        "--env-password", action="store_true", help="add: use the password set in .env"
    )
    user.set_defaults(handler=_user)
    move = commands.add_parser(
        "relocate", help="after copying the media folder from another machine, fix its paths"
    )
    move.add_argument("old_root", help="where the repository was on the old machine")
    move.set_defaults(handler=_relocate)

    args = parser.parse_args(argv)
    logs.setup(logging.DEBUG if args.verbose else logging.INFO)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
