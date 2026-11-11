from __future__ import annotations
import logging
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from bot import Bot
from bot.config import ConfigManager
from bot.config.models import ServerModel


SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


class ServerError(Exception):
    """A management request that cannot be carried out; the message is shown to the user."""


class BotManager:
    """Runs one bot per configured TeamTalk server, all in this process.

    Servers can be added, removed, changed and turned on or off while the
    others keep running.
    """

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager
        self.bots: Dict[str, Bot] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        # One stop/start of a server at a time (rs twice, rs and the web page).
        self._server_locks: Dict[str, threading.RLock] = {}
        self._stopped = threading.Event()
        self.errors: Dict[str, str] = {}
        self.started_at: Dict[str, float] = {}
        self.process_started_at = time.time()
        self.restart_requested = False

    # ------------------------------------------------------------ running

    def start_all(self) -> int:
        for name, server in self.config_manager.config.servers.items():
            if server.enabled:
                self.start_server(name)
            else:
                logging.info("Server %s is turned off in the configuration", name)
        return len(self.bots)

    def start_server(self, name: str) -> bool:
        with self._lock:
            if name in self.bots:
                return True
            server = self.config_manager.config.servers.get(name)
            if server is None:
                return False
            bot = None
            try:
                bot = Bot(name, self.config_manager.server(name), self)
                bot.initialize()
            except (Exception, SystemExit) as e:
                self.errors[name] = str(e) or e.__class__.__name__
                logging.error(
                    "Server %s could not be started: %s", name, e, exc_info=True
                )
                if bot is not None:
                    self._close_bot(bot)
                return False
            thread = threading.Thread(
                target=self._run_bot, args=(bot,), name=name, daemon=True
            )
            self.bots[name] = bot
            self._threads[name] = thread
            self.errors.pop(name, None)
            self.started_at[name] = time.time()
            thread.start()
            logging.info("Server %s started", name)
            return True

    def _run_bot(self, bot: Bot) -> None:
        try:
            bot.run()
        except (Exception, SystemExit) as e:
            logging.error("Server %s stopped with an error", bot.name, exc_info=True)
            with self._lock:
                if self.bots.get(bot.name) is bot:
                    del self.bots[bot.name]
                    self._threads.pop(bot.name, None)
                    self.errors[bot.name] = str(e) or e.__class__.__name__
            self._close_bot(bot)

    def server_lock(self, name: str) -> threading.RLock:
        with self._lock:
            return self._server_locks.setdefault(name, threading.RLock())

    def _close_bot(self, bot: Bot) -> None:
        with bot.use_lock:
            bot.closing = True
            try:
                bot.close()
            except Exception:
                logging.error("Error while closing server %s", bot.name, exc_info=True)

    def stop_server(self, name: str) -> None:
        """Disconnect one server. Runs in the background so a command can stop its own bot."""
        threading.Thread(
            target=self._stop_server, args=(name,), name=name + "/Stop"
        ).start()

    def _stop_server(self, name: str) -> None:
        with self.server_lock(name):
            with self._lock:
                bot = self.bots.pop(name, None)
                self._threads.pop(name, None)
                self.started_at.pop(name, None)
            if bot is not None:
                self._close_bot(bot)
                logging.info("Server %s stopped", name)

    def restart_server(self, name: str) -> None:
        """Reconnect one server with its settings re-read from the configuration file."""
        threading.Thread(
            target=self.reload_and_restart, args=(name,), name=name + "/Restart"
        ).start()

    def reload_and_restart(self, name: str) -> bool:
        with self.server_lock(name):
            self._stop_server(name)
            try:
                self.config_manager.reload_server(name)
            except (KeyError, ValidationError, SystemExit) as e:
                self.errors[name] = "The configuration could not be loaded: {}".format(e)
                logging.error(
                    "Server %s was not restarted: its configuration could not be loaded: %s",
                    name,
                    e,
                )
                return False
            return self.start_server(name)

    # -------------------------------------------------------- management

    def _check_name(self, name: str) -> None:
        if not SERVER_NAME.match(name):
            raise ServerError(
                "A server name may use letters, numbers, dots, dashes and underscores, "
                "must start with a letter or number, and may be at most 40 characters long."
            )

    def _validate(self, data: Dict[str, Any]) -> ServerModel:
        try:
            return ServerModel(**data)
        except ValidationError as e:
            problems = [
                "{}: {}".format(" › ".join(str(part) for part in error["loc"]), error["msg"])
                for error in e.errors()
            ]
            raise ServerError("Some settings are not valid. " + "; ".join(problems))

    def add_server(self, name: str, data: Dict[str, Any]) -> ServerModel:
        self._check_name(name)
        with self._lock:
            if name in self.config_manager.config.servers:
                raise ServerError("A server named {} already exists.".format(name))
            server = self._validate(data)
            self.config_manager.add_server(name, server)
            logging.info("Server %s added", name)
            if server.enabled:
                self.start_server(name)
            return server

    def update_server(self, name: str, data: Dict[str, Any]) -> bool:
        """Save new settings for a server and reconnect it if it is on. Returns whether it is running."""
        with self._lock:
            old = self.config_manager.config.servers.get(name)
            if old is None:
                raise ServerError("There is no server named {}.".format(name))
            server = self._validate(data)
            self._stop_server(name)
            self.config_manager.config.servers[name] = server
            self.config_manager.save_server(name)
            logging.info("Settings of server %s saved", name)
            if server.enabled:
                return self.start_server(name)
            self.errors.pop(name, None)
            return False

    def remove_server(self, name: str) -> None:
        """Disconnect and delete a server."""
        with self._lock:
            server = self.config_manager.config.servers.get(name)
            if server is None:
                raise ServerError("There is no server named {}.".format(name))
            self._stop_server(name)
            self.config_manager.remove_server(name)
            self.errors.pop(name, None)
            logging.info("Server %s removed", name)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Turn a server on or off and remember it. Returns whether it is running."""
        with self._lock:
            server = self.config_manager.config.servers.get(name)
            if server is None:
                raise ServerError("There is no server named {}.".format(name))
            server.enabled = enabled
            self.config_manager.save_server(name)
            if enabled:
                return self.start_server(name)
            self._stop_server(name)
            self.errors.pop(name, None)
            return False

    # ------------------------------------------------------------ process

    def close(self) -> None:
        with self._lock:
            bots = list(self.bots.values())
            self.bots.clear()
            self._threads.clear()
        for bot in bots:
            self._close_bot(bot)
        self.config_manager.close()
        self._stopped.set()

    def restart_process(self) -> None:
        """Restart the whole bot: every server reconnects and the configuration is read again."""
        self.restart_requested = True
        threading.Thread(target=self.close, name="Restart").start()

    def wait(self) -> None:
        self._stopped.wait()
        if self.restart_requested:
            logging.info("Restarting the bot process")
            os.execv(sys.executable, [sys.executable] + sys.argv)
