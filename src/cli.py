"""One-shot transcription from the terminal — the same pipeline the server runs.

    python src/cli.py <youtube-or-spotify-url> --part sax

Useful for checking a song without the browser, and for the end-to-end smoke
test, since it prints what was detected alongside where the files landed.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import fetch  # noqa: E402
import pipeline  # noqa: E402
from paths import PARTS, job_dir  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe a song to sheet music.")
    parser.add_argument("url", help="YouTube or Spotify track link")
    parser.add_argument("--part", default="sax", choices=PARTS)
    parser.add_argument("--subdivision", type=int, default=4,
                        help="grid steps per quarter note (4 = sixteenths)")
    parser.add_argument("--sharps", type=int, default=None,
                        help="override the detected key signature")
    parser.add_argument("--bpm", type=float, default=None, help="override the detected tempo")
    args = parser.parse_args()

    print(f"Resolving {args.url}")
    resolved = fetch.resolve(args.url)
    chosen = resolved["chosen"]
    if resolved["kind"] == "spotify":
        requested = resolved["requested"]
        print(f"  Spotify: {requested['title']} — {requested['artist']} "
              f"({requested['duration']:.0f}s)")
        print(f"  Matched: {chosen['title']} [{chosen['uploader']}] "
              f"({chosen['duration']:.0f}s, {chosen['note']}, score {chosen['score']})")
    else:
        print(f"  {chosen['title']} [{chosen['uploader']}]")

    options = {"subdivision": args.subdivision}
    if args.sharps is not None:
        options["sharps"] = args.sharps
    if args.bpm is not None:
        options["bpm"] = args.bpm

    job_id = pipeline.new_job(chosen["source_id"], args.part, chosen, options)
    print(f"Job {job_id} ({args.part})")

    last = None
    while True:
        status = pipeline.get_status(job_id)
        if status.get("message") != last:
            last = status.get("message")
            print(f"  [{status.get('progress', 0):.0%}] {last}")
        if status.get("state") in ("done", "error"):
            break
        import time
        time.sleep(1.0)

    if status["state"] == "error":
        print(f"\nFailed: {status['message']}", file=sys.stderr)
        print(status.get("traceback", ""), file=sys.stderr)
        return 1

    detected = status.get("detected", {})
    print(f"\nDetected: {detected.get('key')} · {detected.get('tempo')} BPM · "
          f"{detected.get('time_signature')} · beats via {detected.get('beat_source')}"
          + (" · swing" if detected.get("swing") else ""))
    print(f"Output:   {job_dir(job_id)}")
    for name in ("pdf", "musicxml", "midi"):
        print(f"  {name:9s} {status['artifacts'][name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
