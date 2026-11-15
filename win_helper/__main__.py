"""Start the Beacon Windows Helper on 127.0.0.1:8765 (or the LAN with BEACON_DEV_LAN=1 and BEACON_TOKEN)."""

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--talk-key", default=os.environ.get("BEACON_PTT_KEY", "right_alt"),
                        help="right_alt (default), right_control, right_win, scroll_lock, pause, f13")
    parser.add_argument("--no-hud", action="store_true", help="no pill, ring or Beacon window")
    parser.add_argument("--no-voice", action="store_true", help="no microphone, talk key or speech output")
    args = parser.parse_args()

    from . import win32
    win32.set_dpi_aware()  # before any window or UIA call, so every coordinate is in physical pixels

    import uvicorn

    from .server import create_app
    talk_key = "right_alt" if args.talk_key == "right_option" else args.talk_key
    app = create_app(talk_key, with_hud=not args.no_hud, with_voice=not args.no_voice)
    dev_lan = os.environ.get("BEACON_DEV_LAN", "0") == "1"
    if dev_lan and not os.environ.get("BEACON_TOKEN"):
        sys.exit("BEACON_DEV_LAN=1 needs BEACON_TOKEN")
    uvicorn.run(app, host="0.0.0.0" if dev_lan else "127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
