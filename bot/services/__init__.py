from __future__ import annotations
from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import shutil

import requests

from bot import app_vars, errors

if TYPE_CHECKING:
    from bot import Bot
    from bot.player.track import Track


def cookies_file(bot: "Bot") -> str:
    """The YouTube cookies file for a server: its own (services.yt.cookiefile_path),
    else cookies.txt next to the configuration file, else "" (none)."""
    import os

    own = bot.config.services.yt.cookiefile_path
    if own:
        return own if os.path.isfile(own) else ""
    shared = os.path.join(bot.config_manager.config_dir, "cookies.txt")
    return shared if os.path.isfile(shared) else ""


class Service(ABC):
    name: str
    is_enabled: bool
    hidden: bool
    hostnames: List[str]
    error_message: str
    warning_message: str
    help: str

    def download(self, track: Track, file_path: str) -> None:
        with requests.get(track.url, stream=True, timeout=30) as response:
            response.raise_for_status()
            with open(file_path, "wb") as f:
                shutil.copyfileobj(response.raw, f)

    @abstractmethod
    def get(
        self,
        url: str,
        extra_info: Optional[Dict[str, Any]] = None,
        process: bool = False,
    ) -> List[Track]:
        ...

    @abstractmethod
    def initialize(self) -> None:
        ...

    @abstractmethod
    def search(self, query: str) -> List[Track]:
        ...


from bot.services.yt import YtService
from bot.services.am import AmService


# Every server's bot has its own services (with its own settings), so tracks
# look their service up through the server they were created on.
_service_managers: Dict[str, "ServiceManager"] = {}


def get_service_by_name(name: str, server: str = "") -> Service:
    manager = _service_managers.get(server)
    if manager is None:
        if not _service_managers:
            raise errors.ServiceNotFoundError(name)
        manager = next(iter(_service_managers.values()))
    return manager.get_service_by_name(name)


class ServiceManager:
    def __init__(self, bot: Bot) -> None:
        self.config = bot.config.services
        self.services: Dict[str, Service] = {
            "yt": YtService(bot, self.config.yt),
            "am": AmService(bot, self.config.am),
        }
        try:
            self.service: Service = self.services[self.config.default_service]
        except KeyError:
            logging.warning(
                "Unknown default service %r, using %s",
                self.config.default_service,
                app_vars.fallback_service,
            )
            self.service = self.services[app_vars.fallback_service]
        self.fallback_service = app_vars.fallback_service
        _service_managers[bot.name] = self

    def close(self, bot: Bot) -> None:
        if _service_managers.get(bot.name) is self:
            del _service_managers[bot.name]

    def initialize(self) -> None:
        logging.debug("Initializing services")
        for service in self.services.values():
            if not service.is_enabled:
                continue
            try:
                service.initialize()
            except errors.ServiceError as e:
                service.is_enabled = False
                service.error_message = str(e)
                if self.service == service:
                    self.service = self.services[self.fallback_service]
        logging.debug("Services initialized")

    def get_service_by_name(self, name: str) -> Service:
        try:
            service = self.services[name]
            if not service.is_enabled:
                raise errors.ServiceIsDisabledError(service.error_message)
            return service
        except KeyError as e:
            raise errors.ServiceNotFoundError(str(e))
