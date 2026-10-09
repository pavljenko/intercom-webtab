"""Command line: ``python3 -m intercom_webtab --config /etc/intercom-webtab/webtab.ini``."""
import argparse
import logging
import os
import sys

from . import APP_NAME, __version__
from .config import DEFAULT_CONFIG_PATH, ConfigError, load


def _setup_logging(level):
    # under systemd the journal adds timestamps itself
    fmt = "%(levelname)s %(name)s: %(message)s"
    if not os.environ.get("INVOCATION_ID"):
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=fmt,
                        datefmt="%H:%M:%S", stream=sys.stdout)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="intercom_webtab",
                                 description="%s: door-intercom station and weather panel "
                                             "for an old iPad." % APP_NAME)
    ap.add_argument("--config", default=os.environ.get("IWT_CONFIG", DEFAULT_CONFIG_PATH),
                    help="path to webtab.ini (default: %(default)s)")
    ap.add_argument("--check", action="store_true",
                    help="validate the configuration, secrets and token, then exit")
    ap.add_argument("--log-level", choices=("debug", "info", "warning", "error"),
                    help="override [station] log_level")
    ap.add_argument("--version", action="version", version="%s %s" % (APP_NAME, __version__))
    args = ap.parse_args(argv)
    try:
        cfg = load(args.config)
    except ConfigError as e:
        print("intercom-webtab: configuration error: %s" % e, file=sys.stderr)
        return 2
    _setup_logging(args.log_level or cfg.station.log_level)
    log = logging.getLogger("iwt")
    for w in cfg.warnings:
        log.warning("config: %s", w)
    if args.check:
        print("%s: configuration OK (%s; panels: %s)" % (APP_NAME, cfg.path,
                                                        ", ".join(cfg.panel_ids)))
        return 0
    try:
        from .web import run
    except ImportError as e:
        print("intercom-webtab: missing dependency: %s (install python3-aiohttp)" % e,
              file=sys.stderr)
        return 3
    try:
        run(cfg)
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        log.error("%s", e)
        return 1
    except OSError as e:               # HTTP port in use, state directory not writable...
        log.error("cannot start: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
