"""Command line interface: song info, register logs, and WAV rendering."""

import argparse
import sys
from pathlib import Path

from pysidtracker import render_player_wav, run_cli, seconds_to_frames

from pygoattracker import audio, constants, convert, ninja, reglog
from pygoattracker.errors import GoatTrackerError
from pygoattracker.gt1 import Gt1Song
from pygoattracker.gt1player import Gt1Player
from pygoattracker.reader import read_sng


def _text(data: bytes) -> str:
    return data.decode("latin-1", "replace")


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
    if isinstance(song, Gt1Song):
        print("format:      GoatTracker V1.x editor song")
        print(f"player:      {song.player}")
        print(f"name:        {_text(song.sng.name)}")
        print(f"author:      {_text(song.sng.author)}")
        print(f"copyright:   {_text(song.sng.copyright)}")
        print(f"subtunes:    {len(song.orderlists)}")
        print(f"patterns:    {len(song.patterns)}")
        print(f"instruments: {len(song.instruments)}")
        for num, instrument in enumerate(song.instruments, start=1):
            print(f"  {num:02X}: {_text(instrument.name)}")
        return
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
    frames = seconds_to_frames(
        args.seconds, constants.PAL_CYCLES_PER_FRAME, constants.PAL_CLOCK_HZ
    )
    if isinstance(song, Gt1Song):
        player = Gt1Player(song, subtune=args.subtune)
        writes = reglog.register_writes_from_player(
            player, frames, constants.PAL_CYCLES_PER_FRAME
        )
    else:
        writes = reglog.iter_register_writes(
            song, subtune=args.subtune, max_frames=frames
        )
    reglog.write_reglog(writes, args.output, header=reglog.REGLOG_HEADER)
    print(f"wrote {args.output}")


def _wav(args) -> None:
    song = read_sng(args.song)
    if isinstance(song, Gt1Song):
        try:
            render_player_wav(
                Gt1Player(song, subtune=args.subtune),
                args.output,
                seconds=args.seconds,
                model=args.model,
            )
        except ValueError as exc:
            raise GoatTrackerError(str(exc)) from exc
        print(f"wrote {args.output}")
        return
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
    if isinstance(song, Gt1Song):
        song = song.to_gt2()
    report: list = []
    errors = "drop" if args.lenient else "strict"
    converted = convert.gt_to_nt2(song, errors=errors, report=report)
    ninja.write_nt2(converted, args.output)
    for message in report:
        print(f"dropped: {message}")
    print(f"wrote {args.output}")


def _sid2sng(args) -> None:
    from pygoattracker import sid
    from pygoattracker.writer import write_sng

    result = sid.decompile_sid(args.sid, subtune=args.subtune)
    write_sng(result.song, args.output)
    song = result.song
    print(f"decompiled {args.sid}")
    print(f"  name:        {song.name}")
    print(f"  subtunes:    {len(song.subtunes)}")
    print(f"  patterns:    {len(song.patterns)}")
    print(f"  instruments: {len(song.instruments)}")
    print(f"  simplepulse: {result.info.playback.simplepulse}")
    print(f"wrote {args.output}")


def _totext(args) -> None:
    from pygoattracker import sid, text
    from pygoattracker.typed import TypedSong

    data = Path(args.song).read_bytes()
    if data[:4] in constants.SNG_COMPATIBLE_MAGICS:
        song = read_sng(data)
        if isinstance(song, Gt1Song):
            raise GoatTrackerError("GoatTracker V1.x songs have no GT2 text form")
        typed = TypedSong.from_song(song)
    else:
        typed = sid.decompile_sid(data, subtune=args.subtune).typed
    out = text.dumps(typed)
    if args.output:
        Path(args.output).write_text(out)
    else:
        print(out, end="")


def _fromtext(args) -> None:
    from pygoattracker import text
    from pygoattracker.writer import write_sng

    write_sng(text.loads(Path(args.text).read_text()).to_song(), args.output)


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

    sid2sng = commands.add_parser(
        "sid2sng", help="decompile a packed GoatTracker .sid into a .sng"
    )
    sid2sng.add_argument("sid", help="packed GoatTracker .sid file")
    sid2sng.add_argument("output", help=".sng file to write")
    sid2sng.add_argument("--subtune", type=int, default=0)
    sid2sng.set_defaults(func=_sid2sng)

    totext = commands.add_parser(
        "text", help="print a .sng or packed .sid in the song grammar (song.lark)"
    )
    totext.add_argument("song", help=".sng or packed GoatTracker .sid file")
    totext.add_argument(
        "output", nargs="?", help="text file to write (default: stdout)"
    )
    totext.add_argument("--subtune", type=int, default=0)
    totext.set_defaults(func=_totext)

    fromtext = commands.add_parser("fromtext", help="write song grammar text as a .sng")
    fromtext.add_argument("text", help="song text file")
    fromtext.add_argument("output", help=".sng file to write")
    fromtext.set_defaults(func=_fromtext)
    return parser


def main(argv=None) -> int:
    """CLI entry point; returns a process exit code."""
    return run_cli(_parser, GoatTrackerError, argv)


if __name__ == "__main__":
    sys.exit(main())
