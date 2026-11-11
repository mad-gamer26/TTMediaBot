#!/usr/bin/env python3
"""TeamTalk Player: plays music from YouTube, Apple Music and internet radio
into TeamTalk channels, on any number of servers from one process.

    python teamtalk_player.py [-c config.json]
    python teamtalk_player.py --install-sdk [--yes]
"""
from typing import Optional

import signal
import sys
from os import path

from argparse import ArgumentParser

# Started without a console (pythonw on Windows, a service): no stdout/stderr.
if sys.stdout is None or sys.stderr is None:
    import os as _os

    _devnull = open(_os.devnull, "w")
    sys.stdout = sys.stdout or _devnull
    sys.stderr = sys.stderr or _devnull

parser = ArgumentParser(description="TeamTalk Player")
parser.add_argument(
    "-c",
    "--config",
    help="Path to the configuration file",
    default=path.join(path.dirname(path.abspath(__file__)), "config.json"),
)
parser.add_argument("-l", "--log", help="Path to the log file", default=None)
parser.add_argument(
    "--default-config",
    help='Save default config to "config_default.json" and exit',
    action="store_true",
)
parser.add_argument(
    "--install-sdk",
    help="Find (or download from BearWare) the TeamTalk SDK and exit",
    action="store_true",
)
parser.add_argument("--yes", help="With --install-sdk: download without asking", action="store_true")
parser.add_argument(
    "--activate-sdk",
    help="Start the TeamTalk SDK once in the foreground and exit (on Windows, BearWare's trial asks to be activated)",
    action="store_true",
)
parser.add_argument("--setup", help="Set up a TeamTalk server (asks in the terminal) and exit", action="store_true")
parser.add_argument("--web-password", help="Set or remove the web page password and exit", action="store_true")
args = parser.parse_args()

# The TeamTalk SDK is found (or installed) before the bot is imported: its
# modules import it as TeamTalkPy.
import teamtalk_sdk  # noqa: E402

try:
    _sdk = teamtalk_sdk.load_teamtalk_sdk(assume_yes=args.yes)
except teamtalk_sdk.TeamTalkSDKError as e:
    sys.exit(str(e))
_sdk.TeamTalk5 = _sdk
sys.modules["TeamTalkPy"] = _sdk
if args.install_sdk:
    print("TeamTalk SDK {} is ready.".format(_sdk.ttstr(_sdk.getVersion())))
    sys.exit(0)
if args.activate_sdk:
    # On Windows the trial SDK shows "Activating TeamTalk SDK Trial" the first
    # time a client starts, and waits for OK; started in the background later,
    # nobody could see it.
    _client = _sdk.TeamTalk()
    _client.closeTeamTalk()
    _client.closeTeamTalk = lambda: True
    print("The TeamTalk SDK starts.")
    sys.exit(0)

from pydantic import ValidationError  # noqa: E402

from bot import tools, updater  # noqa: E402

from bot import logbuffer, logger  # noqa: E402
from bot.config import ConfigManager, save_default_file  # noqa: E402
from bot.manager import BotManager  # noqa: E402


def load_config(file_name: str) -> ConfigManager:
    try:
        return ConfigManager(file_name)
    except ValidationError as e:
        for error in e.errors():
            print(
                "Error in config:",
                ".".join([str(i) for i in error["loc"]]),
                error["msg"],
            )
        sys.exit(1)
    except PermissionError:
        sys.exit(
            "The configuration file cannot be accessed due to a permission error or is already used by another instance of the bot"
        )


def main(
    config: str = args.config,
    log: Optional[str] = args.log,
    default_config: bool = args.default_config,
) -> None:
    tools.prepare_path()
    if args.setup:
        from bot import setup

        setup.run(config)
        return
    if args.web_password:
        from bot import setup

        setup.web_password(config)
        return
    if default_config:
        save_default_file()
        print("Successfully dumped to config_default.json")
    else:
        config_manager = load_config(config)
        if config_manager.config.logger.log:
            logger.initialize_logger(
                config_manager.config.logger, config_manager.config_dir, log
            )
        logbuffer.install()
        manager = BotManager(config_manager)
        updater.start(manager._stopped)

        def stop(signum, frame):
            manager.close()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        from bot import web

        interface = web.start(manager)
        if manager.start_all() == 0 and interface is None:
            manager.close()
            sys.exit("No server could be started")
        try:
            manager.wait()
        except KeyboardInterrupt:
            manager.close()
        finally:
            if interface is not None:
                interface.close()


if __name__ == "__main__":
    main()
