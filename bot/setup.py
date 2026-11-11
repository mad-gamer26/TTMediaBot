"""teamtalk_player.py --setup: asks for a TeamTalk server in the terminal and
writes (or adds it to) the configuration file. --web-password sets the web
page's password. Plain questions and answers, one per line, so it works
with a screen reader and over SSH."""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
from typing import Dict, Optional
from urllib.parse import unquote

from bot.config.models import ConfigModel, ServerModel
from bot.manager import SERVER_NAME
from bot.web import auth

TT_DEFAULT_PORT = 10333


def ask(question: str, default: str = "") -> str:
    suffix = " [{}]".format(default) if default else ""
    try:
        answer = input("{}{}: ".format(question, suffix)).strip()
    except EOFError:
        print()
        sys.exit("Setup ended: no more input.")
    return answer or default


def ask_yes(question: str, default: bool) -> bool:
    while True:
        answer = ask(question + (" (Y/n)" if default else " (y/N)")).lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


def ask_port(question: str, default: int) -> int:
    while True:
        answer = ask(question, str(default))
        if answer.isdigit() and 1 <= int(answer) <= 65535:
            return int(answer)
        print("Please give a port number from 1 to 65535.")


def ask_secret(question: str) -> str:
    try:
        return getpass.getpass(question + " (not shown as you type; Enter for none): ")
    except EOFError:
        return ""


def parse_tt_link(link: str) -> Optional[Dict[str, object]]:
    """A tt:// link as TeamTalk makes it (Generate TT URL), like the web page's
    parser: tcpport, udpport, encrypted, username, password, channel, chanpasswd."""
    match = re.match(r"^tt:/+([^?!/]*)/?\??(.*)$", link.strip(), re.I)
    if not match or not match.group(1):
        return None
    host, query = unquote(match.group(1)), match.group(2)
    values: Dict[str, object] = {"host": host, "tcpport": TT_DEFAULT_PORT, "udpport": TT_DEFAULT_PORT,
                                 "encrypted": False, "username": "", "password": "", "channel": "", "chanpasswd": ""}
    seen = set()
    for part in query.split("&"):
        if not part:
            continue
        name, _, value = part.partition("=")
        name = name.lower()
        if name in seen or name not in values:
            continue
        seen.add(name)
        if name in ("tcpport", "udpport"):
            digits = re.match(r"\d+", value)
            if digits and 1 <= int(digits.group()) <= 65535:
                values[name] = int(digits.group())
        elif name == "encrypted":
            values[name] = value.lower() == "true" or value == "1"
        else:
            values[name] = unquote(value)
    host_port = re.match(r"^(.*[^:]):(\d{1,5})$", host)
    if host_port and "]" not in host and ":" not in host_port.group(1) and "tcpport" not in seen:
        values["host"], values["tcpport"] = host_port.group(1), int(host_port.group(2))
        if "udpport" not in seen:
            values["udpport"] = values["tcpport"]
    return values


def suggest_name(host: str, taken) -> str:
    base = host.strip("[]").lower()
    if re.match(r"^\d+(\.\d+){3}$", base) or ":" in base:
        base = "server-" + re.sub(r"[.:]", "-", base)
    else:
        labels = [label for label in base.split(".") if label and label not in ("www", "tt", "teamtalk")]
        base = labels[0] if labels else base
    base = re.sub(r"[^a-z0-9._-]+", "-", base).lstrip("-._")[:36] or "server"
    name, n = base, 2
    while name in taken:
        name, n = "{}-{}".format(base, n), n + 1
    return name


def _load(path: str) -> dict:
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = ConfigModel().model_dump()
    data["servers"] = {}
    data["config_version"] = 3
    return data


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temp = path + ".tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(temp, path)


def web_password(path: str) -> None:
    """Sets (or removes) the web page password and whether other computers may open it."""
    data = _load(path)
    web = data.setdefault("web", {})
    while True:
        first = ask_secret("New web page password")
        if not first:
            web["password_hash"] = ""
            web["host"] = "127.0.0.1"
            _save(path, data)
            print("No password: the web page opens on this computer only.")
            return
        if len(first) < 8:
            print("Please use at least 8 characters.")
            continue
        if ask_secret("The same password again") != first:
            print("The two passwords differ. Try again.")
            continue
        break
    web["password_hash"] = auth.hash_password(first)
    if ask_yes("Allow other computers on your network to open the web page?", True):
        web["host"] = "0.0.0.0"
    _save(path, data)
    print("Web page password saved. Restart TeamTalk Player for it to take effect.")


def run(path: str) -> None:
    data = _load(path)
    servers = data.setdefault("servers", {})
    adding = bool(servers)
    print("TeamTalk Player setup")
    print("Settings file: " + os.path.abspath(path))
    if adding:
        print("Servers already set up: " + ", ".join(servers) + ". This adds another one.")
    print()
    print("Which TeamTalk server should the player join?")
    values = None
    while values is None:
        link = ask("Paste a tt:// link for it, or press Enter to type the details")
        if not link:
            break
        values = parse_tt_link(link)
        if values is None:
            print("That is not a TeamTalk link (they start with tt://). Try again, or press Enter.")
    if values is None:
        host = ""
        while not host:
            host = ask("Server address (host name or IP address)")
        tcp = ask_port("TCP port", TT_DEFAULT_PORT)
        values = {"host": host, "tcpport": tcp, "udpport": ask_port("UDP port", tcp),
                  "encrypted": ask_yes("Encrypted connection?", False),
                  "username": ask("Username for the player's account (Enter to log in as a guest)"),
                  "password": "", "channel": "", "chanpasswd": ""}
        if values["username"]:
            values["password"] = ask_secret("Password")
        values["channel"] = ask("Channel to join, such as /Music/ (/ is the root channel)", "/")
        values["chanpasswd"] = ask_secret("Channel password")
    else:
        print("From the link: {}:{}, {}, {}, channel {}.".format(
            values["host"], values["tcpport"], "encrypted" if values["encrypted"] else "not encrypted",
            "user " + str(values["username"]) if values["username"] else "guest login", values["channel"] or "/"))
    name = ""
    while not SERVER_NAME.match(name):
        name = ask("A short name for this server (letters, numbers, dots, dashes)", suggest_name(str(values["host"]), servers))
        if name in servers:
            print("There is already a server called {}.".format(name))
            name = ""
    nickname = ask("The player's nickname in TeamTalk", "TeamTalk Player")
    admins = [a.strip() for a in ask("TeamTalk usernames that may use admin commands, separated by commas").split(",") if a.strip()]
    server = ServerModel().model_dump()
    server["teamtalk"].update(hostname=values["host"], tcp_port=values["tcpport"], udp_port=values["udpport"],
                              encrypted=values["encrypted"], username=values["username"], password=values["password"],
                              nickname=nickname, channel=values["channel"] or "/", channel_password=values["chanpasswd"])
    server["teamtalk"]["users"]["admins"] = admins
    ServerModel(**server)  # validates
    servers[name] = server
    if not adding:
        web = data.setdefault("web", {})
        print()
        print("The web page shows what plays on each server and lets you add and change servers.")
        web["enabled"] = ask_yes("Turn on the web page?", True)
        if web["enabled"]:
            web["port"] = ask_port("Its port", int(web.get("port") or 8773))
            web["host"] = "127.0.0.1"
            web["password_hash"] = ""
            if ask_yes("Should other computers on your network be able to open it? (It then needs a password.)", False):
                _save(path, data)
                web_password(path)
                data = _load(path)
                data["servers"][name] = server
    _save(path, data)
    print()
    print("Saved {} ({}:{}).".format(name, values["host"], values["tcpport"]))
