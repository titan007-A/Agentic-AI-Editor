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

    args = parser.parse_args(argv)
    logs.setup(logging.DEBUG if args.verbose else logging.INFO)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
