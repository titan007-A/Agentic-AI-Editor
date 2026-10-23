import argparse
import logging
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
    commands.add_parser("presets", help="list presets").set_defaults(handler=_presets)
    commands.add_parser("serve", help="run the web app and job worker").set_defaults(handler=_serve)
    commands.add_parser("hash-password", help="hash a login password for .env").set_defaults(
        handler=_hash_password
    )

    args = parser.parse_args(argv)
    logs.setup(logging.DEBUG if args.verbose else logging.INFO)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
