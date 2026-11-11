import sys

from bot.config import ConfigManager, config_data_type


SERVER_SECTIONS = (
    "general",
    "sound_devices",
    "player",
    "teamtalk",
    "services",
    "shortening",
)
REMOVED_SERVICES = ("vk", "yam")


def to_v1(config_data: config_data_type) -> config_data_type:
    return update_version(config_data, 1)


def to_v2(config_data: config_data_type) -> config_data_type:
    """Move a single-server configuration into the multi-server layout."""
    if "servers" not in config_data:
        server = {
            section: config_data.pop(section)
            for section in SERVER_SECTIONS
            if section in config_data
        }
        config_data["servers"] = {"default": server}
    for server in config_data["servers"].values():
        services = server.get("services", {})
        for service in REMOVED_SERVICES:
            services.pop(service, None)
        if services.get("default_service") in REMOVED_SERVICES:
            services["default_service"] = "yt"
    config_data["config_version"] = 2
    return config_data


def to_v3(config_data: config_data_type) -> config_data_type:
    """TeamTalk Player: sound goes straight into TeamTalk (no devices or
    sinks), links are not shortened, and messages are in English."""
    for server in config_data.get("servers", {}).values():
        server.pop("sound_devices", None)
        server.pop("shortening", None)
        server.get("general", {}).pop("language", None)
        server.get("player", {}).pop("player_options", None)
    config_data["config_version"] = 3
    return config_data


migrate_functs = {1: to_v1, 2: to_v2, 3: to_v3}


def migrate(
    config_manager: ConfigManager,
    config_data: config_data_type,
) -> config_data_type:
    if "config_version" not in config_data:
        config_data = update_version(config_data, 0)
    elif (
        not isinstance(config_data["config_version"], int)
        or config_data["config_version"] > config_manager.version
    ):
        sys.exit("Error: invalid config_version value")
    if config_data["config_version"] == config_manager.version:
        return config_data
    for ver in migrate_functs:
        if ver > config_data["config_version"]:
            config_data = migrate_functs[ver](config_data)
    config_manager._dump(config_data)
    return config_data


def update_version(config_data: config_data_type, version: int) -> config_data_type:
    _config_data = {"config_version": version}
    _config_data.update(config_data)
    _config_data["config_version"] = version
    return _config_data
