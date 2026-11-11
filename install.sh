#!/bin/sh
# TeamTalk Player installer for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/mad-gamer26/TeamTalk-Player/main/install.sh | sh
#
# Installs for your user account (no root needed, except to install Python's
# venv module or to keep the player running while you are logged out):
#   Linux: ~/.local/share/teamtalk-player, settings ~/.config/teamtalk-player,
#          a systemd user service "teamtalk-player"
#   macOS: ~/Library/Application Support/TeamTalk Player, a LaunchAgent
# Everything else it needs (ffmpeg, yt-dlp, deno, Python packages) goes into
# its own Python environment; the TeamTalk SDK comes from BearWare.
# Run it again to update (settings are kept), add a server, or uninstall.
#
# For testing: TEAMTALK_PLAYER_SOURCE=/path/to/checkout (or a .tar.gz URL),
# TEAMTALK_PLAYER_HOME=/some/dir (everything under it, no service).
set -u

REPO_TARBALL="https://github.com/mad-gamer26/TeamTalk-Player/archive/refs/heads/main.tar.gz"
SOURCE="${TEAMTALK_PLAYER_SOURCE:-$REPO_TARBALL}"
OS=$(uname -s)

if [ -n "${TEAMTALK_PLAYER_HOME:-}" ]; then
  DATA="$TEAMTALK_PLAYER_HOME/data"; CONFIG_DIR="$TEAMTALK_PLAYER_HOME/config"; BIN_DIR="$TEAMTALK_PLAYER_HOME/bin"
  NO_SERVICE=1
elif [ "$OS" = Darwin ]; then
  DATA="$HOME/Library/Application Support/TeamTalk Player"; CONFIG_DIR="$DATA"; BIN_DIR="$HOME/.local/bin"
  NO_SERVICE=""
else
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}/teamtalk-player"
  CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/teamtalk-player"
  BIN_DIR="$HOME/.local/bin"
  NO_SERVICE=""
fi
# Homebrew's programs, also when they are not on this shell's PATH yet.
[ "$OS" = Darwin ] && PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" && export PATH
APP="$DATA/app"
VENV="$DATA/venv"
CONFIG="$CONFIG_DIR/config.json"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/teamtalk-player.service"
AGENT_LABEL="com.github.mad-gamer26.teamtalk-player"
AGENT="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"
TTY=/dev/tty
if [ "$(id -u)" = 0 ]; then SUDO=""; else SUDO=sudo; fi
need_sudo() {  # before a step that installs system packages
  [ -z "$SUDO" ] && return 0
  command -v sudo >/dev/null 2>&1 && return 0
  fail "installing system packages needs sudo (or run this as root): $*"
}

say() { printf '%s\n' "$*"; }
fail() { say "Error: $*" >&2; exit 1; }

ask() {  # ask "Question" default -> REPLY
  if [ -n "${2:-}" ]; then printf '%s [%s]: ' "$1" "$2" >"$TTY"; else printf '%s: ' "$1" >"$TTY"; fi
  IFS= read -r REPLY <"$TTY" || REPLY=""
  [ -z "$REPLY" ] && REPLY="${2:-}"
  return 0
}

yes_no() {  # yes_no "Question" y|n -> exit status
  default=$2
  while :; do
    if [ "$default" = y ]; then ask "$1 (Y/n)"; else ask "$1 (y/N)"; fi
    case "$REPLY" in
      "") [ "$default" = y ]; return ;;
      y|Y|yes|Yes) return 0 ;;
      n|N|no|No) return 1 ;;
      *) say "Please answer y or n." >"$TTY" ;;
    esac
  done
}

[ -r "$TTY" ] || fail "this installer asks questions; run it in a terminal."

# ------------------------------------------------------------------ Python

python_ok() {  # a Python 3.10+ that can make virtual environments
  "$1" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3.14 python3.10 python3; do
    p=$(command -v "$candidate" 2>/dev/null) || continue
    if python_ok "$p"; then PYTHON=$p; return 0; fi
  done
  return 1
}

have_lib() {  # a shared library the TeamTalk SDK needs, on Linux
  [ "$OS" = Darwin ] && return 0
  ldconfig -p 2>/dev/null | grep -q "$1" && return 0
  for dir in /lib /usr/lib /lib64 /usr/lib64 /lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu \
             /lib/aarch64-linux-gnu /usr/lib/aarch64-linux-gnu; do
    [ -e "$dir/$1" ] && return 0
  done
  return 1
}

apt_name() {  # the first of the given package names that apt knows
  for name in "$@"; do
    apt-cache show "$name" >/dev/null 2>&1 && { echo "$name"; return; }
  done
}

system_packages() {
  # What is missing: Python with venv, and the libraries BearWare's TeamTalk
  # library loads (ALSA, PulseAudio's client library, OpenSSL) even though
  # the player opens no sound device.
  need_python=""; find_python || need_python=1
  need_libs=""
  for lib in libasound.so.2 libpulse.so.0 libssl.so.3; do have_lib "$lib" || need_libs=1; done
  [ -z "$need_python$need_libs" ] && return 0
  if [ "$OS" = Darwin ]; then
    if command -v brew >/dev/null 2>&1; then
      yes_no "Python 3.10 or newer is needed. Install it with Homebrew (brew install python)?" y || return 1
      brew install python || return 1
    else
      say "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/macos/ and run this again."
      return 1
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    need_sudo "python3 python3-venv and the TeamTalk SDK's libraries"
    $SUDO apt-get update -qq >/dev/null 2>&1
    packages="python3 python3-venv $(apt_name libasound2t64 libasound2) $(apt_name libpulse0) $(apt_name libssl3t64 libssl3)"
    yes_no "TeamTalk Player needs these system packages: $packages. Install them with $SUDO apt-get?" y || return 1
    # shellcheck disable=SC2086
    $SUDO apt-get install -y --no-install-recommends $packages || return 1
  elif command -v dnf >/dev/null 2>&1; then
    need_sudo "python3 and the TeamTalk SDK's libraries"
    packages="python3 alsa-lib pulseaudio-libs openssl-libs"
    yes_no "TeamTalk Player needs these system packages: $packages. Install them with $SUDO dnf?" y || return 1
    # shellcheck disable=SC2086
    $SUDO dnf install -y $packages || return 1
  elif command -v pacman >/dev/null 2>&1; then
    need_sudo "python and the TeamTalk SDK's libraries"
    packages="python alsa-lib libpulse openssl"
    yes_no "TeamTalk Player needs these system packages: $packages. Install them with $SUDO pacman?" y || return 1
    # shellcheck disable=SC2086
    $SUDO pacman -S --needed --noconfirm $packages || return 1
  elif command -v zypper >/dev/null 2>&1; then
    need_sudo "python3 and the TeamTalk SDK's libraries"
    packages="python3 libasound2 libpulse0 libopenssl3"
    yes_no "TeamTalk Player needs these system packages: $packages. Install them with $SUDO zypper?" y || return 1
    # shellcheck disable=SC2086
    $SUDO zypper install -y $packages || return 1
  else
    say "TeamTalk Player needs Python 3.10 or newer with its venv module, and the ALSA, PulseAudio client"
    say "and OpenSSL 3 libraries. Install them with your system's package manager and run this again."
    return 1
  fi
  find_python
}

# ------------------------------------------------------------------ files

fetch_source() {  # into $1
  mkdir -p "$1"
  case "$SOURCE" in
    http://*|https://*)
      command -v curl >/dev/null 2>&1 || fail "curl is needed to download TeamTalk Player."
      curl -fsSL "$SOURCE" | tar -xz -C "$1" --strip-components=1 || fail "could not download $SOURCE"
      ;;
    *)
      [ -d "$SOURCE" ] || fail "$SOURCE is not a folder"
      (cd "$SOURCE" && tar -cf - --exclude=.git --exclude=.venv --exclude=__pycache__ .) | tar -xf - -C "$1" \
        || fail "could not copy $SOURCE"
      ;;
  esac
  [ -f "$1/teamtalk_player.py" ] || fail "the download does not contain TeamTalk Player"
}

player() { "$VENV/bin/python" "$APP/teamtalk_player.py" "$@"; }

install_files() {
  say "Downloading TeamTalk Player..."
  new="$DATA/app.new"
  rm -rf "$new"
  fetch_source "$new"
  rm -rf "$DATA/app.old"
  [ -d "$APP" ] && mv "$APP" "$DATA/app.old"
  mv "$new" "$APP"
  rm -rf "$DATA/app.old"
  if [ ! -x "$VENV/bin/python" ] || ! python_ok "$VENV/bin/python"; then
    say "Making its Python environment..."
    rm -rf "$VENV"
    "$PYTHON" -m venv "$VENV" || fail "could not make a Python environment in $VENV"
  fi
  say "Installing its Python packages (ffmpeg, yt-dlp and deno included)..."
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade -r "$APP/requirements.txt" \
    || fail "the Python packages could not be installed (see the messages above)"
}

install_sdk() {
  if player --install-sdk </dev/null >/dev/null 2>&1; then
    return 0
  fi
  say ""
  say "TeamTalk Player needs BearWare's TeamTalk 5 SDK, which it downloads from bearware.dk."
  say "BearWare offers it as a 30-day trial; an SDK license is required for use beyond that"
  say "(https://bearware.dk/?page_id=316). It is installed for your user account only."
  yes_no "Download the TeamTalk SDK now?" y || fail "the TeamTalk SDK is needed. Run this again to install it."
  player --install-sdk --yes </dev/null || fail "the TeamTalk SDK could not be installed (see above)"
}

install_command() {
  mkdir -p "$BIN_DIR"
  cat >"$BIN_DIR/teamtalk-player" <<EOF
#!/bin/sh
# TeamTalk Player (installed by install.sh). Settings: $CONFIG
exec "$VENV/bin/python" "$APP/teamtalk_player.py" -c "$CONFIG" "\$@"
EOF
  chmod 755 "$BIN_DIR/teamtalk-player"
}

# ---------------------------------------------------------------- service

service_kind() {
  [ -n "$NO_SERVICE" ] && { echo none; return; }
  if [ "$OS" = Darwin ]; then echo launchd; return; fi
  if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    echo systemd
  else
    echo none
  fi
}

stop_service() {
  case $(service_kind) in
    systemd) systemctl --user stop teamtalk-player.service >/dev/null 2>&1 ;;
    launchd) launchctl bootout "gui/$(id -u)/$AGENT_LABEL" >/dev/null 2>&1 ;;
  esac
  return 0
}

start_service() {
  case $(service_kind) in
    systemd)
      mkdir -p "$(dirname "$UNIT")"
      cat >"$UNIT" <<EOF
[Unit]
Description=TeamTalk Player
After=network-online.target
Wants=network-online.target

[Service]
ExecStart="$VENV/bin/python" "$APP/teamtalk_player.py" -c "$CONFIG"
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
      systemctl --user daemon-reload
      systemctl --user enable teamtalk-player.service >/dev/null 2>&1
      systemctl --user restart teamtalk-player.service || fail "the service did not start; see: journalctl --user -u teamtalk-player"
      say "TeamTalk Player is running and starts when you log in (systemd user service teamtalk-player)."
      if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != yes ]; then
        if yes_no "Keep it running when you are not logged in (for a server; needs sudo once)?" y; then
          $SUDO loginctl enable-linger "$(id -un)" && say "It now runs from startup, logged in or not."
        fi
      fi
      ;;
    launchd)
      mkdir -p "$(dirname "$AGENT")" "$DATA/logs"
      cat >"$AGENT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AGENT_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$VENV/bin/python</string><string>$APP/teamtalk_player.py</string><string>-c</string><string>$CONFIG</string></array>
  <key>RunAtLoad</key><true/>
  <!-- Not a background job: macOS would otherwise throttle its timers (App Nap)
       and the sound would stutter. -->
  <key>ProcessType</key><string>Interactive</string>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$DATA/logs/output.log</string>
  <key>StandardErrorPath</key><string>$DATA/logs/output.log</string>
</dict>
</plist>
EOF
      launchctl bootout "gui/$(id -u)/$AGENT_LABEL" >/dev/null 2>&1
      launchctl bootstrap "gui/$(id -u)" "$AGENT" || fail "the LaunchAgent did not load"
      say "TeamTalk Player is running and starts when you log in (LaunchAgent $AGENT_LABEL)."
      ;;
    none)
      if [ -z "$NO_SERVICE" ]; then
        say "This system has no systemd user services. Start TeamTalk Player with: $BIN_DIR/teamtalk-player"
      fi
      ;;
  esac
}

web_address() {
  "$VENV/bin/python" - "$CONFIG" <<'EOF'
import json, sys
try:
    web = json.load(open(sys.argv[1])).get("web", {})
except OSError:
    sys.exit()
if web.get("enabled"):
    host = web.get("host", "127.0.0.1")
    host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print("Web page: http://{}:{}/".format(host, web.get("port", 8773)))
EOF
}

# ------------------------------------------------------------------- menu

uninstall() {
  yes_no "Uninstall TeamTalk Player?" n || { say "Nothing was changed."; exit 0; }
  stop_service
  case $(service_kind) in
    systemd) systemctl --user disable teamtalk-player.service >/dev/null 2>&1; rm -f "$UNIT"; systemctl --user daemon-reload ;;
    launchd) rm -f "$AGENT" ;;
  esac
  rm -rf "$APP" "$VENV" "$DATA/app.new" "$BIN_DIR/teamtalk-player"
  if yes_no "Also remove its settings, saved lists and the TeamTalk SDK?" n; then
    rm -rf "$DATA" "$CONFIG_DIR"
    say "TeamTalk Player and its settings were removed."
  else
    say "TeamTalk Player was removed. Its settings are kept in $CONFIG_DIR."
  fi
  exit 0
}

say "TeamTalk Player installer"
mode=install
if [ -f "$APP/teamtalk_player.py" ]; then
  say "TeamTalk Player is installed in $DATA."
  say "1. Update it (settings are kept)"
  say "2. Add a TeamTalk server"
  say "3. Uninstall"
  say "4. Quit"
  while :; do
    ask "Choice, 1 to 4" 1
    case "$REPLY" in 1) mode=update; break ;; 2) mode=add; break ;; 3) uninstall ;; 4) exit 0 ;; *) say "Please choose 1 to 4." ;; esac
  done
fi

system_packages || fail "the system packages TeamTalk Player needs are missing."
find_python || fail "no usable Python was found."
say "Using $("$PYTHON" -c 'import sys; print("Python", sys.version.split()[0])') ($PYTHON)."

if [ "$mode" = add ]; then
  player --setup -c "$CONFIG" <"$TTY" || fail "setup did not finish"
  stop_service; start_service; web_address
  exit 0
fi

mkdir -p "$DATA" "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR" 2>/dev/null
stop_service
install_files
install_sdk
install_command
if [ ! -f "$CONFIG" ]; then
  say ""
  player --setup -c "$CONFIG" <"$TTY" || fail "setup did not finish; run this installer again"
fi
start_service
say ""
say "TeamTalk Player is installed."
web_address
say "Settings: $CONFIG"
say "Command: $BIN_DIR/teamtalk-player (for example: teamtalk-player --setup to add a server)"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "($BIN_DIR is not on your PATH yet; open a new terminal or add it.)" ;; esac
