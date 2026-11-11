import json
import os
import sys
from threading import RLock
from typing import Any, Dict, Optional

from bot import app_vars, utils
from bot.config.models import ConfigModel, LoggerModel, ServerModel
from bot.migrators import config_migrator

import portalocker

config_data_type = Dict[str, Any]


def save_default_file() -> None:
    with open(utils.get_abs_path("config_default.json"), "w") as f:
        json.dump(ConfigModel().model_dump(), f, indent=4, ensure_ascii=False)


class ConfigManager:
    """Owns the configuration file shared by every server the bot connects to."""

    version = 3

    def __init__(self, file_name: Optional[str]) -> None:
        self._io_lock = RLock()
        self.file_locker = None
        if file_name:
            if os.path.isfile(file_name):
                self.file_name = os.path.abspath(file_name)
                config_dict = config_migrator.migrate(self, self._load())
                self.config_dir = os.path.dirname(self.file_name)
                self._lock()
            else:
                sys.exit("Incorrect configuration file path")
        else:
            self.config_dir = app_vars.directory
            config_dict = {}
        self.config: ConfigModel = ConfigModel(**config_dict)

    def server(self, name: str) -> "ServerConfigManager":
        return ServerConfigManager(self, name)

    def _dump(self, data: config_data_type):
        # Written in place (the file stays locked by this process) and kept
        # readable by its owner only: it holds account passwords.
        with open(self.file_name, "w", encoding="UTF-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        try:
            os.chmod(self.file_name, 0o600)
        except OSError:
            pass

    def _load(self):
        with open(self.file_name, "r", encoding="UTF-8") as f:
            try:
                return json.load(f)
            except json.decoder.JSONDecodeError as e:
                sys.exit("Syntax error in configuration file: " + str(e))

    def _lock(self):
        self.file_locker = portalocker.Lock(
            self.file_name,
            timeout=0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        try:
            self.file_locker.acquire()
        except portalocker.exceptions.LockException:
            raise PermissionError()

    def close(self):
        if self.file_locker:
            self.file_locker.release()

    def reload_server(self, name: str) -> ServerModel:
        """Re-read one server's settings from disk, leaving the others untouched."""
        with self._io_lock:
            data = config_migrator.migrate(self, self._load())
            server = ConfigModel(**data).servers.get(name)
            if server is None:
                raise KeyError(name)
            self.config.servers[name] = server
            return server

    def _update_file(self, change) -> None:
        """Apply change(data) to the file on disk, keeping everything else as it is there."""
        with self._io_lock:
            data = config_migrator.migrate(self, self._load())
            change(data)
            self.file_locker.release()
            try:
                self._dump(data)
            finally:
                self.file_locker.acquire()

    def save_server(self, name: str) -> None:
        """Write one server's current settings into its section of the file on disk."""
        with self._io_lock:
            server = self.config.servers[name].model_dump()
            self._update_file(
                lambda data: data.setdefault("servers", {}).__setitem__(name, server)
            )

    def add_server(self, name: str, server: ServerModel) -> None:
        with self._io_lock:
            if name in self.config.servers:
                raise KeyError(name)
            self.config.servers[name] = server
            try:
                self.save_server(name)
            except BaseException:
                del self.config.servers[name]
                raise

    def remove_server(self, name: str) -> None:
        with self._io_lock:
            self.config.servers.pop(name, None)
            self._update_file(lambda data: data.get("servers", {}).pop(name, None))

    def save_logger(self, logger: LoggerModel) -> None:
        with self._io_lock:
            self.config.logger = logger
            dumped = logger.model_dump()
            self._update_file(lambda data: data.__setitem__("logger", dumped))


class ServerConfigManager:
    """The view of the configuration that one server's bot works with."""

    def __init__(self, manager: ConfigManager, name: str) -> None:
        self.manager = manager
        self.name = name
        self.config_dir = manager.config_dir

    @property
    def config(self) -> ServerModel:
        return self.manager.config.servers[self.name]

    def save(self) -> None:
        self.manager.save_server(self.name)

    def close(self) -> None:
        # The shared file stays locked until the whole process exits.
        pass
