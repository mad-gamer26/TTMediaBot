"""Management web interface, served from inside the bot process.

Without a password it listens on 127.0.0.1 only (put it behind a proxy that
admits only trusted networks to reach it from elsewhere). With a password
(web.password_hash, set by teamtalk_player.py --web-password) it may listen
on the network and asks for the password (HTTP Basic). Requests that change anything must carry the X-Player-Request header, which a
page on another site cannot send without the browser asking first, and an
Origin, if present, must match the host.
"""

from __future__ import annotations
import html
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

from bot import app_vars, logbuffer
from bot.config.models import LoggerModel, ServerModel
from bot.manager import BotManager, ServerError
from bot.player.enums import State
from bot.TeamTalk.structs import Flags, User, UserState, UserStatusMode, UserType
from bot.web import auth, fields

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_BODY = 1024 * 1024
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}

# Playback buttons map to the bot's own chat commands, so they behave the same.
PLAYER_ACTIONS = {
    "play": "p",
    "url": "u",
    "pause": "p",
    "stop": "s",
    "next": "n",
    "previous": "b",
    "volume": "v",
    "select": "c",
    "service": "sv",
    "mode": "m",
    "speed": "sp",
    "seek_back": "sb",
    "seek_forward": "sf",
    "alt": "alt",
}


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ state


@contextmanager
def _using(bot):
    """Holds the bot so it is not closed meanwhile; yields False if it is
    being closed (a restart or stop is in progress)."""
    if not bot.use_lock.acquire(timeout=5):
        yield False
        return
    try:
        yield not bot.closing
    finally:
        bot.use_lock.release()


def _connection_state(manager: BotManager, name: str) -> Tuple[str, str]:
    """(state, description) for a server."""
    server = manager.config_manager.config.servers.get(name)
    bot = manager.bots.get(name)
    if bot is None:
        if name in manager.errors:
            return "error", "Not running: " + manager.errors[name]
        if server is not None and not server.enabled:
            return "off", "Turned off"
        return "stopped", "Not running"
    with _using(bot) as usable:
        if not usable:
            return "reconnecting", "Reconnecting"
        try:
            flags = bot.ttclient.flags
            if flags & Flags.AUTHORIZED and bot.ttclient.tt.getMyChannelID():
                return "connected", "Connected"
            if flags & Flags.AUTHORIZED:
                return "joining", "Logged in, joining the channel"
            if flags & Flags.CONNECTED:
                return "logging_in", "Connected, logging in"
            if flags & Flags.CONNECTING:
                return "connecting", "Connecting"
        except Exception:
            pass
    return "reconnecting", "Disconnected, trying again"


def _player_state(bot) -> Dict[str, Any]:
    player = bot.player
    track = player.track
    info: Dict[str, Any] = {
        "state": player.state.value,
        "track": getattr(track, "_name", "") or "",
        "service": getattr(track, "service", "") or "",
        "index": player.track_index + 1 if player.state != State.Stopped else 0,
        "count": len(player.track_list),
        "volume": player.volume,
        "max_volume": bot.config.player.max_volume,
        "mode": player.mode.value,
        "current_service": bot.service_manager.service.name,
    }
    try:
        info["speed"] = player.get_speed()
        info["position"] = player.get_position()
        info["duration"] = player.get_duration()
    except Exception:
        pass
    return info


def server_status(manager: BotManager, name: str) -> Dict[str, Any]:
    server = manager.config_manager.config.servers[name]
    state, description = _connection_state(manager, name)
    status: Dict[str, Any] = {
        "name": name,
        "enabled": server.enabled,
        "running": name in manager.bots,
        "state": state,
        "state_text": description,
        "host": "{}:{}".format(server.teamtalk.hostname, server.teamtalk.tcp_port),
        "nickname": server.teamtalk.nickname,
        "channel": str(server.teamtalk.channel),
        "started_at": manager.started_at.get(name),
        "player": None,
        "listeners": None,
    }
    bot = manager.bots.get(name)
    if bot is not None:
        with _using(bot) as usable:
            if usable:
                try:
                    status["nickname"] = bot.config.teamtalk.nickname
                    status["player"] = _player_state(bot)
                    channel_id = bot.ttclient.tt.getMyChannelID()
                    if channel_id:
                        path = bot.ttclient.tt.getChannelPath(channel_id)
                        status["channel"] = path.decode("utf-8", "replace") if isinstance(path, bytes) else str(path)
                        users = bot.ttclient.tt.getChannelUsers(channel_id)
                        status["listeners"] = max(0, len(users) - 1)
                except Exception:
                    logging.debug("Could not read the state of %s", name, exc_info=True)
    return status


# ------------------------------------------------------------- commands


def _web_user(bot) -> User:
    return User(
        id=-1,
        nickname="Web interface",
        username="",
        channel=bot.ttclient.channel,
        type=UserType.Admin,
        is_admin=True,
        status="",
        gender=UserStatusMode.N,
        state=UserState.Null,
        client_name="",
        version=0,
        user_account=None,
        is_banned=False,
    )


def run_command(bot, text: str) -> str:
    """Run a bot command as an admin and return its reply."""
    from bot import errors

    processor = bot.command_processor
    try:
        name, arg = processor.parse_command(text)
    except errors.ParseCommandError:
        raise HttpError(HTTPStatus.BAD_REQUEST, "That is not a command.")
    user = _web_user(bot)
    try:
        command_class = processor.get_command(name, user)
    except errors.UnknownCommandError:
        raise HttpError(HTTPStatus.BAD_REQUEST, 'Unknown command "{}". The h command lists them.'.format(name))
    command = command_class(processor)
    processor.current_command_id = id(command)
    try:
        result = command(arg, user)
    except errors.InvalidArgumentError:
        return processor.help(name, user)
    return result or "Done."


# --------------------------------------------------------------- server


class WebInterface:
    def __init__(self, manager: BotManager, host: str, port: int) -> None:
        self.manager = manager
        self.host = host
        self.port = port
        handler = self._handler_class()
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="Web", daemon=True)
        self.routes: List[Tuple[str, re.Pattern, Callable[..., Any]]] = []
        self._register_routes()

    def start(self) -> None:
        self.thread.start()
        logging.info("Web interface listening on http://%s:%s", self.host, self.port)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # routes -------------------------------------------------------------

    def _route(self, method: str, pattern: str, function: Callable[..., Any]) -> None:
        self.routes.append((method, re.compile("^" + pattern + "$"), function))

    def _register_routes(self) -> None:
        name = r"([A-Za-z0-9][A-Za-z0-9._-]*)"
        self._route("GET", "/api/state", self.api_state)
        self._route("GET", "/api/form", self.api_form)
        self._route("POST", "/api/servers", self.api_add_server)
        self._route("GET", "/api/servers/" + name, self.api_get_server)
        self._route("PUT", "/api/servers/" + name, self.api_update_server)
        self._route("DELETE", "/api/servers/" + name, self.api_remove_server)
        self._route("POST", "/api/servers/" + name + "/enabled", self.api_set_enabled)
        self._route("POST", "/api/servers/" + name + "/restart", self.api_restart_server)
        self._route("POST", "/api/servers/" + name + "/player", self.api_player)
        self._route("POST", "/api/servers/" + name + "/command", self.api_command)
        self._route("GET", "/api/logs", self.api_logs)
        self._route("GET", "/api/settings", self.api_get_settings)
        self._route("PUT", "/api/settings", self.api_update_settings)
        self._route("POST", "/api/process/restart", self.api_restart_process)
        self._route("GET", "/api/matches", self.api_matches)
        self._route("DELETE", r"/api/matches/(\d+)", self.api_forget_match)

    # helpers --------------------------------------------------------------

    @property
    def config(self):
        return self.manager.config_manager.config

    def _server(self, name: str) -> ServerModel:
        server = self.config.servers.get(name)
        if server is None:
            raise HttpError(HTTPStatus.NOT_FOUND, "There is no server named {}.".format(name))
        return server

    def _bot(self, name: str):
        self._server(name)
        bot = self.manager.bots.get(name)
        if bot is None:
            raise HttpError(HTTPStatus.CONFLICT, "{} is not running. Turn it on first.".format(name))
        return bot

    def _match_cache(self):
        from bot.services.am import MatchCache

        return MatchCache.open(os.path.join(self.manager.config_manager.config_dir, "am_matches.json"))

    # API ------------------------------------------------------------------

    def api_state(self, query, body):
        servers = [server_status(self.manager, name) for name in list(self.config.servers)]
        return {
            "servers": servers,
            "process": {
                "pid": os.getpid(),
                "started_at": self.manager.process_started_at,
                "version": app_vars.client_name,
            },
            "time": time.time(),
        }

    def api_form(self, query, body):
        return {
            "server": fields.server_form(),
            "logger": fields.logger_form(),
            "defaults": ServerModel().model_dump(),
        }

    def api_get_server(self, query, body, name):
        server = self._server(name)
        return {"config": server.model_dump(), "status": server_status(self.manager, name)}

    def api_add_server(self, query, body):
        name = str(body.get("name", "")).strip()
        config = body.get("config")
        copy_from = str(body.get("copy_from", "") or "")
        if copy_from:
            base = self._server(copy_from).model_dump()
            # The copy gets its own cache and account details from the form.
            config = _merge(base, config or {})
        if not isinstance(config, dict):
            config = {}
        try:
            self.manager.add_server(name, config)
        except ServerError as e:
            raise HttpError(HTTPStatus.BAD_REQUEST, str(e))
        status = server_status(self.manager, name)
        message = "Server {} added".format(name)
        message += "." if status["running"] or not status["enabled"] else ", but it could not be started: {}".format(
            self.manager.errors.get(name, "")
        )
        return {"message": message, "status": status}

    def api_update_server(self, query, body, name):
        self._server(name)
        config = body.get("config")
        if not isinstance(config, dict):
            raise HttpError(HTTPStatus.BAD_REQUEST, "No settings were sent.")
        try:
            running = self.manager.update_server(name, config)
        except ServerError as e:
            raise HttpError(HTTPStatus.BAD_REQUEST, str(e))
        server = self._server(name)
        if running:
            message = "Settings saved. {} reconnected with them.".format(name)
        elif server.enabled:
            message = "Settings saved, but {} could not be started: {}".format(name, self.manager.errors.get(name, ""))
        else:
            message = "Settings saved. {} is turned off.".format(name)
        return {"message": message, "status": server_status(self.manager, name)}

    def api_remove_server(self, query, body, name):
        self._server(name)
        try:
            self.manager.remove_server(name)
        except ServerError as e:
            raise HttpError(HTTPStatus.BAD_REQUEST, str(e))
        return {"message": "Server {} removed.".format(name)}

    def api_set_enabled(self, query, body, name):
        self._server(name)
        enabled = bool(body.get("enabled"))
        running = self.manager.set_enabled(name, enabled)
        if not enabled:
            message = "{} turned off.".format(name)
        elif running:
            message = "{} turned on.".format(name)
        else:
            message = "{} turned on, but it could not be started: {}".format(name, self.manager.errors.get(name, ""))
        return {"message": message, "status": server_status(self.manager, name)}

    def api_restart_server(self, query, body, name):
        self._server(name)
        if not self._server(name).enabled:
            raise HttpError(HTTPStatus.CONFLICT, "{} is turned off. Turn it on instead.".format(name))
        ok = self.manager.reload_and_restart(name)
        message = "{} reconnected.".format(name) if ok else "{} could not be started: {}".format(
            name, self.manager.errors.get(name, "")
        )
        return {"message": message, "status": server_status(self.manager, name)}

    def api_player(self, query, body, name):
        bot = self._bot(name)
        action = str(body.get("action", ""))
        if action not in PLAYER_ACTIONS:
            raise HttpError(HTTPStatus.BAD_REQUEST, "Unknown player action.")
        value = str(body.get("value", "") or "").strip()
        if action in ("play", "url", "select") and not value:
            raise HttpError(HTTPStatus.BAD_REQUEST, "Please fill in the field first.")
        if action == "pause":
            value = ""
        with _using(bot) as usable:
            if not usable:
                raise HttpError(HTTPStatus.CONFLICT, "{} is reconnecting. Try again in a moment.".format(name))
            reply = run_command(bot, (PLAYER_ACTIONS[action] + " " + value).strip())
        return {"message": reply, "status": server_status(self.manager, name)}

    def api_command(self, query, body, name):
        bot = self._bot(name)
        text = str(body.get("command", "")).strip()
        if not text:
            raise HttpError(HTTPStatus.BAD_REQUEST, "Type a command first.")
        with _using(bot) as usable:
            if not usable:
                raise HttpError(HTTPStatus.CONFLICT, "{} is reconnecting. Try again in a moment.".format(name))
            reply = run_command(bot, text)
        return {"message": reply, "status": server_status(self.manager, name)}

    def api_logs(self, query, body):
        def one(key: str, default: str = "") -> str:
            return query.get(key, [default])[0]

        server = one("server") or None
        if server == "_bot":
            server = ""
        try:
            after = int(one("after", "0"))
            limit = max(1, min(1000, int(one("limit", "300"))))
        except ValueError:
            raise HttpError(HTTPStatus.BAD_REQUEST, "after and limit must be numbers.")
        records = logbuffer.buffer.records(after=after, server=server, min_level=one("level", "INFO").upper(), limit=limit)
        return {"records": records}

    def api_get_settings(self, query, body):
        return {"logger": self.config.logger.model_dump(), "web": self.config.web.model_dump()}

    def api_update_settings(self, query, body):
        try:
            logger = LoggerModel(**(body.get("logger") or {}))
        except ValidationError as e:
            raise HttpError(HTTPStatus.BAD_REQUEST, "Some settings are not valid: {}".format(e.errors()[0]["msg"]))
        self.manager.config_manager.save_logger(logger)
        return {"message": "Logging settings saved. They take effect when the bot restarts."}

    def api_restart_process(self, query, body):
        self.manager.restart_process()
        return {"message": "The bot is restarting. Every server reconnects in a few seconds."}

    def api_matches(self, query, body):
        text = query.get("q", [""])[0].strip().lower()
        entries = []
        for song_id, entry in self._match_cache().entries().items():
            candidates = entry.get("candidates") or []
            index = int(entry.get("index", -1))
            video = candidates[index] if 0 <= index < len(candidates) else ""
            item = {
                "id": song_id,
                "title": entry.get("title", ""),
                "artist": entry.get("artist", ""),
                "apple_url": entry.get("url", ""),
                "video": video,
                "video_url": "https://www.youtube.com/watch?v=" + video if video else "",
                "choice": index + 1,
                "candidates": len(candidates),
                "time": entry.get("time"),
            }
            if text and text not in (item["title"] + " " + item["artist"] + " " + song_id).lower():
                continue
            entries.append(item)
        entries.sort(key=lambda item: -(item["time"] or 0))
        return {"matches": entries}

    def api_forget_match(self, query, body, song_id):
        if not self._match_cache().forget(song_id):
            raise HttpError(HTTPStatus.NOT_FOUND, "That match is not remembered.")
        return {"message": "Match forgotten. The song is matched again the next time it plays."}

    # handler ----------------------------------------------------------------

    def _handler_class(self):
        interface = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "TeamTalk-Player-Web"
            sys_version = ""

            def log_message(self, format: str, *args: Any) -> None:
                logging.debug("Web: " + format, *args)

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
                )
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, status: int, data: Dict[str, Any]) -> None:
                self._send(status, json.dumps(data).encode(), "application/json; charset=utf-8")

            def _check_request_origin(self) -> None:
                if self.headers.get("X-Player-Request") != "1":
                    raise HttpError(HTTPStatus.FORBIDDEN, "Missing request header.")
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    raise HttpError(HTTPStatus.FORBIDDEN, "Requests from other sites are not allowed.")

            def _body(self) -> Dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The request is too large.")
                if not length:
                    return {}
                try:
                    data = json.loads(self.rfile.read(length))
                except ValueError:
                    raise HttpError(HTTPStatus.BAD_REQUEST, "The request is not valid JSON.")
                if not isinstance(data, dict):
                    raise HttpError(HTTPStatus.BAD_REQUEST, "The request must be a JSON object.")
                return data

            def _signed_in(self) -> bool:
                stored = interface.manager.config_manager.config.web.password_hash
                if not stored:
                    return True
                if auth.check_password(auth.password_from_header(self.headers.get("Authorization", "")), stored):
                    return True
                time.sleep(1)  # slows down guessing
                self.send_response(HTTPStatus.UNAUTHORIZED)
                self.send_header("WWW-Authenticate", 'Basic realm="TeamTalk Player", charset="UTF-8"')
                body = b"Sign in with the web page password (any user name)."
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return False

            def _handle(self) -> None:
                if not self._signed_in():
                    return
                parts = urlsplit(self.path)
                path = unquote(parts.path)
                try:
                    if path.startswith("/api/"):
                        method = self.command if self.command != "HEAD" else "GET"
                        if method != "GET":
                            self._check_request_origin()
                        for route_method, pattern, function in interface.routes:
                            match = pattern.match(path)
                            if match and route_method == method:
                                body = self._body() if method in ("POST", "PUT", "DELETE") else {}
                                result = function(parse_qs(parts.query), body, *match.groups())
                                self._json(HTTPStatus.OK, dict(result or {}, ok=True))
                                return
                        raise HttpError(HTTPStatus.NOT_FOUND, "Not found.")
                    if self.command not in ("GET", "HEAD"):
                        raise HttpError(HTTPStatus.METHOD_NOT_ALLOWED, "Not allowed.")
                    self._page_or_static(path)
                except HttpError as e:
                    if path.startswith("/api/"):
                        self._json(e.status, {"ok": False, "error": str(e)})
                    else:
                        self._send(e.status, _page("Not found", "notfound", str(e)).encode(), "text/html; charset=utf-8")
                except Exception as e:
                    logging.error("Web request failed: %s %s", self.command, path, exc_info=True)
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Something went wrong: {}".format(e)})

            def _page_or_static(self, path: str) -> None:
                if path.startswith("/static/"):
                    file_name = path[len("/static/"):]
                    extension = os.path.splitext(file_name)[1]
                    full = os.path.join(STATIC_DIR, file_name)
                    if "/" in file_name or extension not in STATIC_TYPES or not os.path.isfile(full):
                        raise HttpError(HTTPStatus.NOT_FOUND, "Not found.")
                    with open(full, "rb") as f:
                        self._send(HTTPStatus.OK, f.read(), STATIC_TYPES[extension])
                    return
                pages = {
                    "/": ("Servers", "overview", ""),
                    "/add": ("Add a server", "add", ""),
                    "/log": ("Log", "log", ""),
                    "/settings": ("Bot settings", "settings", ""),
                    "/matches": ("Apple Music matches", "matches", ""),
                }
                if path in pages:
                    title, page, argument = pages[path]
                else:
                    match = re.match(r"^/servers/([A-Za-z0-9][A-Za-z0-9._-]*)$", path)
                    if not match or match.group(1) not in interface.config.servers:
                        raise HttpError(HTTPStatus.NOT_FOUND, "There is no such page.")
                    title, page, argument = "Server " + match.group(1), "server", match.group(1)
                self._send(HTTPStatus.OK, _page(title, page, argument).encode(), "text/html; charset=utf-8")

            def do_GET(self) -> None:
                self._handle()

            def do_HEAD(self) -> None:
                self._handle()

            def do_POST(self) -> None:
                self._handle()

            def do_PUT(self) -> None:
                self._handle()

            def do_DELETE(self) -> None:
                self._handle()

        return Handler


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


NAV = [
    ("/", "Servers"),
    ("/add", "Add a server"),
    ("/log", "Log"),
    ("/matches", "Apple Music matches"),
    ("/settings", "Bot settings"),
]


def _page(title: str, page: str, argument: str) -> str:
    links = "".join(
        '<li><a href="{0}"{2}>{1}</a></li>'.format(
            href, html.escape(label), ' aria-current="page"' if label == title else ""
        )
        for href, label in NAV
    )
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - TeamTalk Player</title>
<link rel="stylesheet" href="/static/style.css">
<script src="/static/app.js" defer></script>
</head>
<body data-page="{page}" data-argument="{argument}">
<a class="skip" href="#main">Skip to main content</a>
<header>
<p class="brand">TeamTalk Player</p>
<nav aria-label="Main"><ul>{links}</ul></nav>
</header>
<main id="main" tabindex="-1">
<h1>{heading}</h1>
<div id="status" role="status" aria-live="polite" aria-atomic="true"></div>
<div id="alert" role="alert" aria-atomic="true"></div>
<div id="content"><p>Loading…</p></div>
<noscript><p>This page needs JavaScript.</p></noscript>
</main>
</body>
</html>
""".format(
        title=html.escape(title),
        heading=html.escape(title),
        page=html.escape(page),
        argument=html.escape(argument),
        links=links,
    )


def start(manager: BotManager) -> Optional[WebInterface]:
    web = manager.config_manager.config.web
    if not web.enabled:
        return None
    host = web.host
    if not auth.is_loopback(host) and not web.password_hash:
        logging.error(
            "The web page has no password, so it listens on this computer only (127.0.0.1), not on %s. "
            "Set one with: teamtalk_player.py --web-password", host
        )
        host = "127.0.0.1"
    interface = WebInterface(manager, host, web.port)
    interface.start()
    return interface
