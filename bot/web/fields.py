"""Describes the configuration models as labelled form sections for the web interface.

Every setting in the models is included automatically; the tables below only
give friendlier labels, help text and ordering.
"""

from __future__ import annotations
import typing
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel

from bot.config.models import LoggerModel, ServerModel

# Sections in the order they appear, as (path, title, description).
SERVER_SECTIONS: List[Tuple[str, str, str]] = [
    ("teamtalk", "Connection and account", "The TeamTalk server to connect to and how the bot appears there."),
    ("teamtalk.users", "Users", "Who may give admin commands and who may not use the bot."),
    ("player", "Player", "Volume and playback behaviour."),
    ("services", "Services", "Where music comes from."),
    ("services.yt", "YouTube", ""),
    ("services.am", "Apple Music", "Songs are found in Apple's catalog and played from the matching recording on YouTube."),
    ("general", "General", "Startup commands and other options."),
    ("teamtalk.event_handling", "Event handlers (advanced)", "Python code that runs on TeamTalk events."),
]

LABELS: Dict[str, Tuple[str, str]] = {
    "teamtalk.hostname": ("Host name or address", ""),
    "teamtalk.tcp_port": ("TCP port", ""),
    "teamtalk.udp_port": ("UDP port", ""),
    "teamtalk.encrypted": ("Encrypted connection", "Turn on if the server uses encryption."),
    "teamtalk.username": ("Username", "The account the bot logs in with."),
    "teamtalk.password": ("Password", ""),
    "teamtalk.nickname": ("Nickname", ""),
    "teamtalk.status": ("Status message", 'Shown while nothing is playing. Empty means "Send "h" for help".'),
    "teamtalk.gender": ("Gender", ""),
    "teamtalk.channel": ("Channel", 'A channel path such as /Music/, or a channel number.'),
    "teamtalk.channel_password": ("Channel password", ""),
    "teamtalk.license_name": ("TeamTalk SDK license name", "Leave empty to use the SDK's trial mode."),
    "teamtalk.license_key": ("TeamTalk SDK license key", ""),
    "teamtalk.reconnection_attempts": ("Reconnection attempts", "-1 keeps trying forever."),
    "teamtalk.reconnection_timeout": ("Seconds between reconnection attempts", ""),
    "teamtalk.users.admins": ("Admin usernames", "One username per line. Admins can use every command."),
    "teamtalk.users.banned_users": ("Banned usernames", "One username per line."),
    "teamtalk.event_handling.load_event_handlers": ("Load event handlers", ""),
    "teamtalk.event_handling.event_handlers_file_name": ("Event handlers file", "A .py file, or a folder with __init__.py."),
    "player.default_volume": ("Volume at startup", ""),
    "player.max_volume": ("Maximum volume", ""),
    "player.volume_fading": ("Fade volume changes", ""),
    "player.volume_fading_interval": ("Fading step, in seconds", ""),
    "player.seek_step": ("Seek step, in seconds", ""),
    "services.default_service": ("Default service", "The service searches use until someone changes it with sv."),
    "services.yt.enabled": ("YouTube enabled", ""),
    "services.yt.cookiefile_path": ("Own cookies file", "A cookies.txt file for YouTube, for when YouTube asks the bot to sign in. Leave empty to use yt-dlp's own configuration."),
    "services.am.enabled": ("Apple Music enabled", ""),
    "services.am.country": ("Storefront country", "Two-letter country code, for example us or gb."),
    "services.am.no_match": ("When no recording matches", ""),
    "general.send_channel_messages": ("Announce requests in the channel", ""),
    "general.cache_file_name": ("Cache file", '{server} is replaced by the server name.'),
    "general.blocked_commands": ("Blocked commands", "One command per line, for example dl. Admins are not affected."),
    "general.delete_uploaded_files_after": ("Delete uploaded files after, in seconds", "-1 keeps them."),
    "general.time_format": ("Time format", "A strftime format such as %H:%M."),
    "general.start_commands": ("Commands to run on connect", "One command per line, run as an admin."),
    "log": ("Write a log", ""),
    "level": ("Log level", ""),
    "format": ("Log line format", "A Python logging format."),
    "mode": ("Log to", ""),
    "file_name": ("Log file", "Relative paths are next to the configuration file."),
    "max_file_size": ("Maximum log size, in KB", "0 means no limit."),
    "backup_count": ("Old log files to keep", ""),
}

CHOICES: Dict[str, List[Tuple[str, str]]] = {
    "teamtalk.gender": [("n", "Neutral"), ("m", "Male"), ("f", "Female")],
    "services.default_service": [("yt", "YouTube"), ("am", "Apple Music")],
    "services.am.no_match": [("preview", "Play Apple's 30-second preview"), ("skip", "Skip to the next song")],
    "level": [(level, level.title()) for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")],
    "mode": [("FILE", "File"), ("STDOUT", "Standard output"), ("STDOUT_AND_FILE", "File and standard output")],
}

SECRET = {"teamtalk.password", "teamtalk.channel_password", "teamtalk.license_key"}
NUMBER_LIMITS = {
    "teamtalk.tcp_port": (1, 65535),
    "teamtalk.udp_port": (1, 65535),
    "player.default_volume": (0, 1000),
    "player.max_volume": (0, 1000),
}


def _humanize(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _kind(annotation: Any) -> str:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "text"
    if origin in (list, List):
        return "lines"
    if origin in (dict, Dict):
        return "json"
    if origin is typing.Union and set(args) == {int, str}:
        return "int_or_text"
    return "text"


def _describe(model: Type[BaseModel], prefix: str, nested: set) -> List[Dict[str, Any]]:
    fields = []
    for name, info in model.model_fields.items():
        path = prefix + name
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            continue  # its own section
        if path in nested:
            continue
        label, help_text = LABELS.get(path, (_humanize(name), ""))
        field: Dict[str, Any] = {"key": name, "path": path, "label": label, "help": help_text, "kind": _kind(annotation)}
        choices = CHOICES.get(path)
        if choices:
            field["kind"] = "select"
            field["choices"] = [{"value": v, "label": l} for v, l in choices]
        if path in SECRET:
            field["kind"] = "password"
        if path in NUMBER_LIMITS:
            field["min"], field["max"] = NUMBER_LIMITS[path]
        fields.append(field)
    return fields


def _model_at(path: str) -> Type[BaseModel]:
    model: Type[BaseModel] = ServerModel
    for part in path.split("."):
        model = model.model_fields[part].annotation  # type: ignore[assignment]
    return model


def server_form() -> List[Dict[str, Any]]:
    sections = []
    for path, title, description in SERVER_SECTIONS:
        sections.append(
            {
                "path": path,
                "title": title,
                "description": description,
                "fields": _describe(_model_at(path), path + ".", set()),
            }
        )
    return sections


def logger_form() -> List[Dict[str, Any]]:
    return [
        {
            "path": "logger",
            "title": "Logging",
            "description": "Shared by every server. Changes take effect when the bot restarts.",
            "fields": _describe(LoggerModel, "", set()),
        }
    ]
