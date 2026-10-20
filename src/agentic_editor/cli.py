import argparse
import logging
import sys

from agentic_editor import logs, pipeline
from agentic_editor.backends import detect
from agentic_editor.presets import load_presets
from agentic_editor.settings import get_settings


def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    available = load_presets(settings.presets_dir)
    missing = [name for name in args.preset if name not in available]
    if missing:
        print(f"unknown preset(s): {', '.join(missing)}; available: {', '.join(available)}")
        return 2
    backends = detect(settings)
    presets = [available[n] for n in args.preset]
    for source in args.source:
        for output in pipeline.run(source, presets, settings, backends, args.notes).outputs:
            clips = ", ".join(f"{c.start:.2f}-{c.end:.2f}" for c in output.edl.clips)
            print(f"{output.preset}: {output.path}")
            print(
                f"  {output.edl.duration:.1f}s from {clips}, planned by {output.planner}, "
                f"{output.renders} render(s)"
            )
            if output.edl.title:
                print(f"  title: {output.edl.title}")
            for issue in output.issues:
                print(f"  unresolved: {issue}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiedit", description="Local agentic video editor")
    parser.add_argument("-v", "--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="edit one or more videos with one or more presets")
    run.add_argument("source", nargs="+", help="video URL or local file")
    run.add_argument("-p", "--preset", action="append", required=True)
    run.add_argument("-n", "--notes", help="extra instructions for the planner")
    run.set_defaults(handler=_run)

    commands.add_parser("backends", help="show the detected backends").set_defaults(
        handler=_backends
    )
    commands.add_parser("presets", help="list presets").set_defaults(handler=_presets)
    commands.add_parser("serve", help="run the web app and job worker").set_defaults(
        handler=_serve
    )
    commands.add_parser("hash-password", help="hash a login password for .env").set_defaults(
        handler=_hash_password
    )

    args = parser.parse_args(argv)
    logs.setup(logging.DEBUG if args.verbose else logging.INFO)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
