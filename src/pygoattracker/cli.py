"""Command line interface: song info, register logs, and WAV rendering."""

import argparse
import sys
from pathlib import Path

from pygoattracker import audio, convert, ninja, reglog
from pygoattracker.errors import GoatTrackerError
from pygoattracker.reader import read_sng


def _nt2_info(song) -> None:
    print("format:      NinjaTracker 2")
    print(f"subtunes:    {len(song.subtunes)}")
    print(f"patterns:    {len(song.patterns)}")
    print(f"commands:    {len(song.commands)}")
    print(f"hardrestart: {song.hr_param:02X} / first wave {song.first_wave:02X}")
    for num, command in enumerate(song.commands, start=1):
        print(f"  {num:02X}: {command.name}")


def _info(args) -> None:
    data = Path(args.song).read_bytes()
    if data[: len(ninja.NT2_MAGIC)] == ninja.NT2_MAGIC:
        _nt2_info(ninja.parse_nt2(data))
        return
    song = read_sng(data)
    print(f"name:        {song.name}")
    print(f"author:      {song.author}")
    print(f"copyright:   {song.copyright}")
    print(f"subtunes:    {len(song.subtunes)}")
    print(f"patterns:    {len(song.patterns)}")
    print(f"instruments: {len(song.instruments)}")
    for num, instrument in enumerate(song.instruments, start=1):
        print(f"  {num:02X}: {instrument.name}")


def _reglog(args) -> None:
    song = read_sng(args.song)
    frames = round(args.seconds * 50)
    writes = reglog.iter_register_writes(song, subtune=args.subtune, max_frames=frames)
    reglog.write_reglog(writes, args.output)
    print(f"wrote {args.output}")


def _wav(args) -> None:
    song = read_sng(args.song)
    audio.render_wav(
        song,
        args.output,
        seconds=args.seconds,
        subtune=args.subtune,
        model=args.model,
    )
    print(f"wrote {args.output}")


def _nt2(args) -> None:
    song = read_sng(args.song)
    report: list = []
    errors = "drop" if args.lenient else "strict"
    converted = convert.gt_to_nt2(song, errors=errors, report=report)
    ninja.write_nt2(converted, args.output)
    for message in report:
        print(f"dropped: {message}")
    print(f"wrote {args.output}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pygoattracker", description="GoatTracker 2 song tools"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="print song metadata")
    info.add_argument("song", help=".sng file")
    info.set_defaults(func=_info)

    log = commands.add_parser("reglog", help="write a SID register log")
    log.add_argument("song", help=".sng file")
    log.add_argument("output", help="register log file to write")
    log.add_argument("--subtune", type=int, default=0)
    log.add_argument("--seconds", type=float, default=60.0)
    log.set_defaults(func=_reglog)

    nt2 = commands.add_parser("nt2", help="convert to a NinjaTracker 2 song")
    nt2.add_argument("song", help=".sng file")
    nt2.add_argument("output", help="NinjaTracker 2 file to write")
    nt2.add_argument(
        "--lenient",
        action="store_true",
        help="drop and report inexpressible features instead of failing",
    )
    nt2.set_defaults(func=_nt2)

    wav = commands.add_parser("wav", help="render through an emulated SID")
    wav.add_argument("song", help=".sng file")
    wav.add_argument("output", help="WAV file to write")
    wav.add_argument("--subtune", type=int, default=0)
    wav.add_argument("--seconds", type=float, default=60.0)
    wav.add_argument("--model", choices=audio.CHIP_MODELS, default="8580")
    wav.set_defaults(func=_wav)
    return parser


def main(argv=None) -> int:
    """CLI entry point; returns a process exit code."""
    args = _parser().parse_args(argv)
    try:
        args.func(args)
    except (GoatTrackerError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
