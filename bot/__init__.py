from __future__ import annotations
import os
import logging
import queue
import sys
import threading
import time
from typing import Optional, TYPE_CHECKING
from bot.TeamTalk.structs import Message, MessageType, User, UserType, UserStatusMode, UserState
from bot import errors

from bot import (
    TeamTalk,
    cache,
    commands,
    config,
    connectors,
    logger,
    modules,
    player,
    services,
    translator,
    app_vars,
)

if TYPE_CHECKING:
    from bot.manager import BotManager


class Bot:
    """The media bot for one TeamTalk server."""

    def __init__(
        self,
        name: str,
        config_manager: Optional[config.ServerConfigManager] = None,
        manager: Optional[BotManager] = None,
    ) -> None:
        # Held while something outside the bot's own threads uses it (the web
        # interface) and while it is closed, so a player or TeamTalk client is
        # never read while it is being destroyed (that crashed the process).
        self.use_lock = threading.RLock()
        self.closing = False
        self.name = name
        self.manager = manager
        if config_manager is None:
            config_manager = config.ConfigManager(None).server(name)
        self.config_manager = config_manager
        self.config = self.config_manager.config
        self.translator = translator.Translator()
        cache_file_name = self.config.general.cache_file_name.replace(
            "{server}", name
        )
        if not os.path.isdir(os.path.join(*os.path.split(cache_file_name)[0:-1])):
            cache_file_name = os.path.join(
                self.config_manager.config_dir, cache_file_name
            )
        try:
            self.cache_manager = cache.CacheManager(cache_file_name)
        except PermissionError:
            sys.exit(
                "The cache file {} cannot be accessed due to a permission error or is already used by another server or instance of the bot".format(
                    cache_file_name
                )
            )
        self.cache = self.cache_manager.cache
        self._claim_cached_tracks()
        try:
            self.player = player.Player(self)
            self.ttclient = TeamTalk.TeamTalk(self)
            self.tt_player_connector = connectors.TTPlayerConnector(self)
            self.service_manager = services.ServiceManager(self)
            self.module_manager = modules.ModuleManager(self)
            self.command_processor = commands.CommandProcessor(self)
        except BaseException:
            self._release_partial()
            raise
        self._close = False

    def _release_partial(self) -> None:
        """Free what a failed __init__ already opened, so the server can be retried."""
        if hasattr(self, "service_manager"):
            self.service_manager.close(self)
        if hasattr(self, "ttclient"):
            self.ttclient.tt.closeTeamTalk()
            self.ttclient.tt.closeTeamTalk = lambda: True
        if hasattr(self, "player"):
            self.player.engine.close()
        self.cache_manager.close()

    def _claim_cached_tracks(self) -> None:
        """Tracks saved before multi-server support belong to this server's cache."""
        tracks = list(self.cache.recents)
        for favorites in self.cache.favorites.values():
            tracks += favorites
        for track in tracks:
            if not getattr(track, "server", ""):
                track.server = self.name

    def initialize(self):
        logging.debug("Initializing")
        self.ttclient.initialize()
        self.player.initialize()
        self.service_manager.initialize()
        logging.debug("Initialized")

    def run(self):
        logging.debug("Starting")
        self.player.run()
        self.tt_player_connector.start()
        self.command_processor.run()
        logging.info("Started")
        logging.info(f"Processing {len(self.config.general.start_commands)} startup command(s)...")
        startup_context_user = User(
            id=-1, nickname="Startup", username="",
            channel=self.ttclient.channel,
            type=UserType.Admin, is_admin=True,
            status="", gender=UserStatusMode.N, state=UserState.Null,
            client_name="", version=0, user_account=None, is_banned=False
        )
        for command in self.config.general.start_commands:
            message = Message(text=command, user=startup_context_user, channel=self.ttclient.channel, type=MessageType.User)
            self.command_processor(message)
        while not self._close:
            try:
                message = self.ttclient.message_queue.get_nowait()
                logging.info(
                    "New message {text} from {username}".format(
                        text=message.text, username=message.user.username
                    )
                )
                self.command_processor(message)
            except queue.Empty:
                pass
            time.sleep(app_vars.loop_timeout)

    def close(self) -> None:
        logging.debug("Closing bot")
        self._close = True
        self.command_processor.close()
        self.player.close()
        self.ttclient.close()
        self.tt_player_connector.close()
        self.service_manager.close(self)
        self.config_manager.close()
        self.cache_manager.close()
        logging.info("Bot closed")
