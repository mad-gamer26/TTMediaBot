from typing import Any, Dict, List, Union

from pydantic import BaseModel, field_validator


class GeneralModel(BaseModel):
    send_channel_messages: bool = True
    # "{server}" is replaced by the server's name, so every server keeps its own cache.
    cache_file_name: str = "cache-{server}.dat"
    blocked_commands: List[str] = []
    delete_uploaded_files_after: int = 300
    time_format: str = r"%H:%M"
    start_commands: List[str] = []

class PlayerModel(BaseModel):
    default_volume: int = 50
    max_volume: int = 100
    volume_fading: bool = True
    volume_fading_interval: float = 0.025
    seek_step: int = 5

class TeamTalkUserModel(BaseModel):
    admins: List[str] = ["admin"]
    banned_users: List[str] = []


class EventHandlingModel(BaseModel):
    load_event_handlers: bool = False
    event_handlers_file_name: str = "event_handlers.py"


class TeamTalkModel(BaseModel):
    hostname: str = "localhost"
    tcp_port: int = 10333
    udp_port: int = 10333
    encrypted: bool = False
    nickname: str = "TeamTalk Player"
    status: str = ""
    gender: str = "n"
    username: str = ""
    password: str = ""
    channel: Union[int, str] = "/"
    channel_password: str = ""
    license_name: str = ""
    license_key: str = ""
    reconnection_attempts: int = -1
    reconnection_timeout: int = 10
    users: TeamTalkUserModel = TeamTalkUserModel()
    event_handling: EventHandlingModel = EventHandlingModel()


class YtModel(BaseModel):
    enabled: bool = True
    # Empty: cookies.txt next to the configuration file, if there is one.
    cookiefile_path: str = ""


class AmModel(BaseModel):
    enabled: bool = True
    # Apple Music storefront (two-letter country code) used for searches and links.
    country: str = "us"
    # What to play when no YouTube recording matches an Apple Music song:
    # "preview" plays Apple's 30-second preview, "skip" moves to the next song.
    no_match: str = "preview"


class ServicesModel(BaseModel):
    default_service: str = "yt"
    yt: YtModel = YtModel()
    am: AmModel = AmModel()


class LoggerModel(BaseModel):
    log: bool = True
    level: str = "INFO"
    format: str = "%(levelname)s [%(asctime)s] %(threadName)s: %(message)s file: %(filename)s line %(lineno)d function %(funcName)s"
    mode: Union[int, str] = "FILE"
    file_name: str = "teamtalk-player.log"
    max_file_size: int = 1024  # KB; the log is rotated past this
    backup_count: int = 1

    @field_validator("level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        value = value.upper()
        if value not in ("PLAYER_DEBUG", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return value

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: Union[int, str]) -> Union[int, str]:
        if value in (1, 2, 3, "STDOUT", "FILE", "STDOUT_AND_FILE"):
            return value
        raise ValueError("must be FILE, STDOUT or STDOUT_AND_FILE")


class ServerModel(BaseModel):
    """Settings for one TeamTalk server. Every server has its own copy."""

    enabled: bool = True
    general: GeneralModel = GeneralModel()
    player: PlayerModel = PlayerModel()
    teamtalk: TeamTalkModel = TeamTalkModel()
    services: ServicesModel = ServicesModel()


class WebModel(BaseModel):
    """The management web interface. Without a password it should listen on
    127.0.0.1 only (or behind a proxy that admits only trusted networks);
    with one (teamtalk_player.py --web-password) it may listen on the
    network, and the page asks for it."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8773
    # PBKDF2-SHA256, written by --web-password; empty: no password.
    password_hash: str = ""


class ConfigModel(BaseModel):
    config_version: int = 3
    logger: LoggerModel = LoggerModel()
    web: WebModel = WebModel()
    servers: Dict[str, ServerModel] = {}
