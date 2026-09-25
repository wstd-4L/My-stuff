"""
WASTED — Multi-Token Discord Controller
Modern red/black UI. Bot + user tokens. Voice kept alive (no auto-close).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import QFont, QColor, QTextCursor, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QFormLayout,
    QSpinBox,
    QTabWidget,
    QFileDialog,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QAbstractItemView,
    QSizePolicy,
)

# ---------------------------------------------------------------------------
# Libraries
#   Bot tokens  → discord.py or discord.py-self
#   User tokens → discord.py-self only
# ---------------------------------------------------------------------------
DISCORD_AVAILABLE = False
USER_TOKEN_SUPPORT = False
discord = None  # type: ignore
commands = None  # type: ignore

try:
    import discord as _discord
    from discord.ext import commands as _commands

    discord = _discord
    commands = _commands
    DISCORD_AVAILABLE = True
    try:
        import importlib.metadata as _md

        names = {
            (d.metadata.get("Name") or "").lower()
            for d in _md.distributions()
            if d.metadata.get("Name")
        }
        if "discord.py-self" in names or "discord-py-self" in names:
            USER_TOKEN_SUPPORT = True
    except Exception:
        pass
    ver = str(getattr(_discord, "__version__", "") or "")
    mod = str(getattr(_discord, "__file__", "") or "").lower()
    if "self" in mod or "self" in ver:
        USER_TOKEN_SUPPORT = True
except ImportError:
    pass

CONFIG_PATH = Path(__file__).resolve().parent / "wasted_config.json"

# WASTED palette — red / black, modern
C_BG = "#0a0a0a"
C_PANEL = "#111111"
C_SURFACE = "#161616"
C_BORDER = "#2a2a2a"
C_BORDER_SOFT = "#1c1c1c"
C_RED = "#e11d2e"
C_RED_DIM = "#8b121c"
C_RED_GLOW = "#ff2a3a"
C_TEXT = "#f0f0f0"
C_MUTED = "#8a8a8a"
C_GREEN = "#22c55e"
C_DANGER = "#ef4444"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@dataclass
class TokenEntry:
    token: str
    kind: str = "user"
    label: str = ""
    client: Any = None
    loop: Optional[asyncio.AbstractEventLoop] = None
    ready: bool = False
    user_id: Optional[int] = None
    username: str = ""
    error: str = ""


def token_preview(token: str) -> str:
    t = token.strip()
    if len(t) > 12:
        return t[:8] + "..." + t[-4:]
    return (t[:8] + "...") if t else "?"


def extract_invite_code(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    m = re.search(
        r"(?:discord(?:app)?\.com/invite/|discord\.gg/)([A-Za-z0-9-]+)",
        raw,
        re.I,
    )
    if m:
        return m.group(1)
    return raw.split("?")[0].strip("/")


def _is_voice_channel(ch) -> bool:
    if discord is None or ch is None:
        return False
    vt = getattr(discord, "VoiceChannel", None)
    if vt is not None and isinstance(ch, vt):
        return True
    name = type(ch).__name__
    return name in ("VoiceChannel", "StageChannel") and hasattr(ch, "connect")


# ---------------------------------------------------------------------------
# discord helpers
# ---------------------------------------------------------------------------
async def dp_join_invite(client: Any, invite_raw: str) -> str:
    code = extract_invite_code(invite_raw)
    if not code:
        return "no invite code"
    try:
        invite = await client.fetch_invite(code, with_counts=False, with_expiration=False)
        if hasattr(invite, "accept") and callable(invite.accept):
            await invite.accept()
            gid = invite.guild.id if invite.guild else "?"
            gname = invite.guild.name if invite.guild else "?"
            return f"joined via Invite.accept -> {gname} ({gid})"
    except Exception:
        pass
    try:
        route = discord.http.Route("POST", "/invites/{invite_code}", invite_code=code)
        data = await client.http.request(route)
        guild = data.get("guild") or {}
        return f"joined via HTTP -> {guild.get('name', '?')} ({guild.get('id', '?')})"
    except Exception as e2:
        return f"invite join failed: {e2}"


async def dp_ensure_guild(client: Any, guild_id: int, invite_raw: str = "") -> str:
    g = client.get_guild(guild_id)
    if g is not None:
        return f"already in guild {g.name} ({guild_id})"
    if invite_raw:
        result = await dp_join_invite(client, invite_raw)
        await asyncio.sleep(1.0)
        if client.get_guild(guild_id) is not None:
            return f"{result} | confirmed in cache"
        return result
    return f"not in guild {guild_id} and no invite given"


async def dp_leave_guild(client: Any, guild_id: int) -> str:
    g = client.get_guild(guild_id)
    if g is None:
        return f"guild {guild_id} not in cache"
    name = g.name
    await g.leave()
    return f"left guild {name} ({guild_id})"


async def dp_send_message(client: Any, channel_id: int, content: str) -> str:
    ch = client.get_channel(channel_id)
    if ch is None:
        try:
            ch = await client.fetch_channel(channel_id)
        except Exception as e:
            return f"fetch_channel failed: {e}"
    if not hasattr(ch, "send"):
        return f"channel {channel_id} has no send()"
    msg = await ch.send(content)
    return f"sent msg {msg.id} in #{getattr(ch, 'name', channel_id)}"


async def dp_resolve_voice_channel(client: Any, guild_id: int, channel_id: int):
    ch = client.get_channel(channel_id)
    if _is_voice_channel(ch):
        return ch, getattr(ch, "guild", None)
    guild = client.get_guild(guild_id)
    if guild is not None:
        ch = guild.get_channel(channel_id)
        if _is_voice_channel(ch):
            return ch, guild
        for vc in getattr(guild, "voice_channels", []) or []:
            if getattr(vc, "id", None) == channel_id:
                return vc, guild
    try:
        ch = await client.fetch_channel(channel_id)
        if _is_voice_channel(ch):
            return ch, getattr(ch, "guild", None) or client.get_guild(guild_id)
    except Exception:
        pass
    if guild is None:
        try:
            guild = await client.fetch_guild(guild_id)
        except Exception:
            guild = None
    if guild is not None:
        for c in getattr(guild, "channels", []) or []:
            if getattr(c, "id", None) == channel_id and _is_voice_channel(c):
                return c, guild
        ch = guild.get_channel(channel_id)
        if _is_voice_channel(ch):
            return ch, guild
    return None, guild


async def dp_join_voice(client: Any, guild_id: int, channel_id: int) -> str:
    """
    Join voice and KEEP the connection. Do not close the client afterward.
    The token worker loop must stay running — that is what was killing the app.
    """
    channel, guild = await dp_resolve_voice_channel(client, guild_id, channel_id)
    if channel is None:
        g = guild or client.get_guild(guild_id)
        if g is not None:
            vcs = list(getattr(g, "voice_channels", []) or [])
            sample = ", ".join(f"#{v.name}({v.id})" for v in vcs[:8]) or "(none)"
            return f"voice channel {channel_id} not found. cached: {sample}"
        return f"guild {guild_id} / channel {channel_id} not found — join server first"

    if guild is None:
        guild = getattr(channel, "guild", None) or client.get_guild(guild_id)
    if guild is None:
        return f"guild missing for channel {channel_id}"

    existing = getattr(guild, "voice_client", None)
    if existing is not None:
        try:
            if (
                getattr(existing, "channel", None)
                and existing.channel.id == channel_id
                and existing.is_connected()
            ):
                return f"already in voice #{channel.name}"
            await existing.disconnect(force=True)
            await asyncio.sleep(0.5)
        except Exception:
            pass

    for vc in list(getattr(client, "voice_clients", []) or []):
        g = getattr(vc, "guild", None)
        if g is not None and g.id == guild_id:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass

    try:
        # self_deaf/self_mute optional — some forks reject kwargs
        try:
            vc = await channel.connect(
                reconnect=True,
                timeout=30.0,
                self_deaf=False,
                self_mute=False,
            )
        except TypeError:
            vc = await channel.connect(reconnect=True, timeout=30.0)
    except asyncio.TimeoutError:
        return "voice connect timed out — check permission / PyNaCl / channel full"
    except Exception as e:
        en = type(e).__name__
        if en in ("Forbidden", "HTTPException"):
            return f"Forbidden (Connect/View Channel): {e}"
        if en == "ClientException":
            return f"ClientException: {e}"
        return f"voice failed: {en}: {e}"

    # Keep reference so GC does not drop the voice client
    try:
        if not hasattr(client, "_wasted_voice"):
            client._wasted_voice = {}
        client._wasted_voice[guild_id] = vc
    except Exception:
        pass

    sess = getattr(vc, "session_id", None) or "?"
    return f"joined voice #{channel.name} ({channel_id}) session={sess} — staying connected"


async def dp_leave_voice(client: Any, guild_id: int) -> str:
    left = []
    guild = client.get_guild(guild_id)
    if guild is not None and getattr(guild, "voice_client", None) is not None:
        try:
            await guild.voice_client.disconnect(force=True)
            left.append("guild.vc")
        except Exception as e:
            left.append(f"err:{e}")
    for vc in list(getattr(client, "voice_clients", []) or []):
        g = getattr(vc, "guild", None)
        if g is not None and g.id == guild_id:
            try:
                await vc.disconnect(force=True)
                left.append("client.vc")
            except Exception as e:
                left.append(f"err:{e}")
    try:
        store = getattr(client, "_wasted_voice", None)
        if store and guild_id in store:
            del store[guild_id]
            left.append("store")
    except Exception:
        pass
    if not left:
        return f"not in voice for guild {guild_id}"
    return f"left voice ({', '.join(left)})"


async def dp_list_guilds(client: Any) -> str:
    names = [f"{g.name} ({g.id})" for g in client.guilds]
    if not names:
        return "no guilds"
    return "guilds: " + ", ".join(names[:40])


async def dp_list_voice_channels(client: Any, guild_id: int) -> str:
    guild = client.get_guild(guild_id)
    if guild is None:
        try:
            guild = await client.fetch_guild(guild_id)
        except Exception as e:
            return f"guild not found: {e}"
    if guild is None:
        return "guild not in cache"
    vcs = list(getattr(guild, "voice_channels", []) or [])
    if not vcs:
        vcs = [c for c in getattr(guild, "channels", []) or [] if _is_voice_channel(c)]
    if not vcs:
        return f"no voice channels in {guild.name}"
    lines = [f"#{v.name}  id={v.id}  limit={v.user_limit or '∞'}" for v in vcs]
    return f"voice in {guild.name}:\n" + "\n".join(lines)


async def dp_bot_send_as_command(client: Any, channel_id: int, content: str) -> str:
    return await dp_send_message(client, channel_id, content)


async def dp_fetch_roles(client: Any, guild_id: int) -> str:
    guild = client.get_guild(guild_id)
    if guild is None:
        try:
            guild = await client.fetch_guild(guild_id)
        except Exception as e:
            return f"guild fetch failed: {e}"
    if guild is None:
        return "guild not found"
    roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
    lines = []
    for r in roles:
        flags = []
        if getattr(r, "managed", False):
            flags.append("managed")
        if getattr(r, "hoist", False):
            flags.append("hoist")
        extra = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"{r.name}  id={r.id}  pos={r.position}{extra}")
    return f"roles in {guild.name} ({len(roles)}):\n" + "\n".join(lines)


async def dp_fetch_channels(client: Any, guild_id: int) -> str:
    guild = client.get_guild(guild_id)
    if guild is None:
        try:
            guild = await client.fetch_guild(guild_id)
        except Exception as e:
            return f"guild fetch failed: {e}"
    if guild is None:
        return "guild not found"
    lines = []
    for c in sorted(guild.channels, key=lambda x: (getattr(x, "position", 0), x.id)):
        kind = type(c).__name__.replace("Channel", "")
        lines.append(f"[{kind}] #{getattr(c, 'name', '?')}  id={c.id}")
    return f"channels in {guild.name} ({len(lines)}):\n" + "\n".join(lines)


async def dp_channel_permissions(client: Any, channel_id: int) -> str:
    ch = client.get_channel(channel_id)
    if ch is None:
        try:
            ch = await client.fetch_channel(channel_id)
        except Exception as e:
            return f"fetch failed: {e}"
    if ch is None:
        return "channel not found"
    lines = [f"channel: #{getattr(ch, 'name', channel_id)} ({channel_id})"]
    me = None
    if getattr(ch, "guild", None):
        me = getattr(ch.guild, "me", None)
        if me is None and client.user:
            me = ch.guild.get_member(client.user.id)
    if me is not None and hasattr(ch, "permissions_for"):
        perms = ch.permissions_for(me)
        flags = [
            "view_channel", "send_messages", "manage_messages", "embed_links",
            "attach_files", "read_message_history", "mention_everyone",
            "connect", "speak", "mute_members", "deafen_members", "move_members",
            "manage_channels", "manage_roles", "administrator",
        ]
        yes = [f for f in flags if getattr(perms, f, False)]
        no = [f for f in flags if not getattr(perms, f, False)]
        lines.append("ALLOWED: " + (", ".join(yes) if yes else "(none)"))
        lines.append("DENIED:  " + (", ".join(no) if no else "(none)"))
    else:
        lines.append("could not resolve bot member permissions")
    return "\n".join(lines)


async def dp_guild_info(client: Any, guild_id: int) -> str:
    guild = client.get_guild(guild_id)
    if guild is None:
        try:
            guild = await client.fetch_guild(guild_id)
        except Exception as e:
            return f"fetch failed: {e}"
    if guild is None:
        return "guild not found"
    owner = getattr(guild, "owner", None)
    owner_s = str(owner) if owner else str(getattr(guild, "owner_id", "?"))
    return (
        f"name: {guild.name}\n"
        f"id: {guild.id}\n"
        f"owner: {owner_s}\n"
        f"members: {getattr(guild, 'member_count', '?')}\n"
        f"roles: {len(guild.roles)}\n"
        f"channels: {len(guild.channels)}\n"
        f"me: {getattr(guild, 'me', '?')}"
    )


async def dp_create_role(client: Any, guild_id: int, name: str, color_hex: str = "") -> str:
    guild = client.get_guild(guild_id)
    if guild is None:
        return "guild not in cache"
    kwargs: dict = {"name": name}
    if color_hex and hasattr(discord, "Colour"):
        color_hex = color_hex.lstrip("#")
        try:
            kwargs["colour"] = discord.Colour(int(color_hex, 16))
        except Exception:
            pass
    role = await guild.create_role(**kwargs, reason="wasted support")
    return f"created role {role.name} id={role.id}"


async def dp_delete_role(client: Any, guild_id: int, role_id: int) -> str:
    guild = client.get_guild(guild_id)
    if guild is None:
        return "guild not in cache"
    role = guild.get_role(role_id)
    if role is None:
        return f"role {role_id} not found"
    await role.delete(reason="wasted support")
    return f"deleted role {role.name} ({role_id})"


def run_coro(entry: TokenEntry, coro, timeout: float = 60.0):
    if entry.loop is None or entry.client is None:
        raise RuntimeError("token has no loop/client")
    if not entry.ready:
        raise RuntimeError("token not ready")
    fut = asyncio.run_coroutine_threadsafe(coro, entry.loop)
    return fut.result(timeout=timeout)


# ---------------------------------------------------------------------------
# Worker — MUST keep event loop alive after voice join
# ---------------------------------------------------------------------------
class TokenWorker(QThread):
    log = pyqtSignal(str)
    status = pyqtSignal(str, str)
    ready_signal = pyqtSignal(str, int, str)
    finished_signal = pyqtSignal(str)

    def __init__(self, entry: TokenEntry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Any = None
        self._closing = False

    def stop(self):
        """Explicit disconnect only — never call this after a successful voice join."""
        self._closing = True
        if self._loop is None:
            return
        if self._client is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
                fut.result(timeout=10)
            except Exception:
                pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

    async def _shutdown(self):
        try:
            if self._client:
                for vc in list(getattr(self._client, "voice_clients", []) or []):
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                for g in list(getattr(self._client, "guilds", []) or []):
                    vc = getattr(g, "voice_client", None)
                    if vc is not None:
                        try:
                            await vc.disconnect(force=True)
                        except Exception:
                            pass
                await self._client.close()
        except Exception:
            pass

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        # Prevent "Task was destroyed but it is pending" hard crashes
        self._loop.set_exception_handler(self._loop_exception_handler)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            self.log.emit(f"[{token_preview(self.entry.token)}] fatal: {e}")
            self.entry.error = str(e)
            self.status.emit(token_preview(self.entry.token), "error")
        finally:
            # Only tear down if we are actually closing — NOT after voice join.
            # client.start() returns only when the connection ends.
            try:
                pending = asyncio.all_tasks(self._loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass
            self.finished_signal.emit(token_preview(self.entry.token))

    def _loop_exception_handler(self, loop, context):
        msg = context.get("message", "")
        exc = context.get("exception")
        text = f"{msg}: {exc}" if exc else msg
        try:
            self.log.emit(f"[{token_preview(self.entry.token)}] loop: {text}")
        except Exception:
            pass

    async def _main(self):
        if not DISCORD_AVAILABLE:
            self.log.emit("Discord runtime not installed")
            return
        token = self.entry.token.strip()
        kind = self.entry.kind
        prev = token_preview(token)

        client_kwargs = {}
        intents_obj = None
        if hasattr(discord, "Intents"):
            try:
                intents_obj = discord.Intents.default()
                for flag in (
                    "guilds",
                    "guild_messages",
                    "messages",
                    "message_content",
                    "voice_states",
                    "members",
                ):
                    if hasattr(intents_obj, flag):
                        setattr(intents_obj, flag, True)
                client_kwargs["intents"] = intents_obj
            except Exception as e:
                self.log.emit(f"[{prev}] intents setup skipped: {e}")

        if kind == "bot":
            try:
                client = commands.Bot(
                    command_prefix="!",
                    help_command=None,
                    **client_kwargs,
                )
            except TypeError:
                client = commands.Bot(command_prefix="!")
        else:
            if not USER_TOKEN_SUPPORT:
                self.log.emit(
                    f"[{prev}] USER TOKEN BLOCKED — self-bot runtime not installed\n"
                    f"[{prev}] See README: install user-token support, then restart"
                )
                self.entry.error = "need user-token runtime"
                self.status.emit(prev, "need runtime")
                return
            try:
                client = discord.Client(**client_kwargs)
            except TypeError:
                client = discord.Client()

        self._client = client
        self.entry.client = client
        self.entry.loop = self._loop

        @client.event
        async def on_ready():
            if self._closing:
                return
            self.entry.ready = True
            self.entry.error = ""
            self.entry.user_id = client.user.id if client.user else None
            self.entry.username = str(client.user) if client.user else "?"
            self.ready_signal.emit(prev, self.entry.user_id or 0, self.entry.username)
            self.status.emit(prev, "online")
            self.log.emit(
                f"[{prev}] READY as {self.entry.username} | "
                f"id={self.entry.user_id} | guilds={len(client.guilds)} | kind={kind}"
            )

        @client.event
        async def on_disconnect():
            # Do NOT treat voice-related gateway blips as fatal.
            # Only mark offline if we are intentionally closing.
            if self._closing:
                self.status.emit(prev, "disconnected")
                self.log.emit(f"[{prev}] gateway disconnected (closing)")
            else:
                self.log.emit(f"[{prev}] gateway blip — reconnecting if possible")

        @client.event
        async def on_resumed():
            if not self._closing:
                self.status.emit(prev, "online")
                self.entry.ready = True
                self.log.emit(f"[{prev}] gateway resumed")

        @client.event
        async def on_error(event_method, *args, **kwargs):
            self.log.emit(f"[{prev}] event error in {event_method}")

        self.log.emit(f"[{prev}] connecting as {kind}...")
        self.status.emit(prev, "connecting")
        try:
            try:
                await client.start(token, reconnect=True)
            except TypeError:
                await client.start(token)
        except Exception as e:
            en = type(e).__name__
            msg = str(e)
            is_login = (
                en in ("LoginFailure", "HTTPException")
                or "token" in msg.lower()
                or "login" in msg.lower()
                or "improper" in msg.lower()
            )
            self.entry.ready = False
            if is_login:
                self.entry.error = f"LoginFailure: {e}"
                self.log.emit(f"[{prev}] LOGIN FAILED: {msg}")
                if kind == "user":
                    self.log.emit(
                        f"[{prev}] User token login failed — use a fresh token and user-token runtime (README)."
                    )
                self.status.emit(prev, "login failed")
            else:
                self.entry.error = str(e)
                self.log.emit(f"[{prev}] start error: {e}\n{traceback.format_exc()}")
                self.status.emit(prev, "error")


# ---------------------------------------------------------------------------
# UI widgets
# ---------------------------------------------------------------------------
class Panel(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(10)
        if title:
            t = QLabel(title)
            t.setObjectName("PanelTitle")
            self._layout.addWidget(t)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(shadow)

    def body(self) -> QVBoxLayout:
        return self._layout


class TitleBar(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self._win = window
        self.setFixedHeight(48)
        self.setObjectName("TitleBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 12, 0)
        lay.setSpacing(10)

        mark = QLabel("W")
        mark.setObjectName("LogoMark")
        mark.setFixedSize(28, 28)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(mark)

        title = QLabel("WASTED")
        title.setObjectName("TitleText")
        lay.addWidget(title)

        sub = QLabel("multi-token controller")
        sub.setObjectName("TitleSub")
        lay.addWidget(sub)
        lay.addStretch()

        self.status_chip = QLabel("IDLE")
        self.status_chip.setObjectName("StatusChip")
        lay.addWidget(self.status_chip)

        # Visible window controls
        self.min_btn = QPushButton("─")
        self.min_btn.setObjectName("WinBtn")
        self.min_btn.setFixedSize(40, 32)
        self.min_btn.setToolTip("Minimize")
        self.min_btn.clicked.connect(window.showMinimized)
        lay.addWidget(self.min_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("WinBtn")
        self.max_btn.setFixedSize(40, 32)
        self.max_btn.setToolTip("Maximize")
        self.max_btn.clicked.connect(self._toggle_max)
        lay.addWidget(self.max_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("CloseBtn")
        self.close_btn.setFixedSize(40, 32)
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(window.close)
        lay.addWidget(self.close_btn)

        self._drag_pos: Optional[QPoint] = None

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            )
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if self._win.isMaximized():
                return
            self._win.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag_pos = None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WASTED")
        self.resize(1180, 780)
        self.setMinimumSize(960, 640)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)

        self.tokens: list[TokenEntry] = []
        self.workers: dict[str, TokenWorker] = {}

        self._apply_style()
        self._build_ui()
        self._load_config()
        self._update_bot_gates()

        if not DISCORD_AVAILABLE:
            self._log("ERROR: Discord runtime missing — run install from README")
        else:
            mode = "user+bot ready" if USER_TOKEN_SUPPORT else "bot tokens only"
            self._log(f"WASTED ready · {mode}")
            if not USER_TOKEN_SUPPORT:
                self._log("User tokens need the self-bot runtime — see README install section")
            try:
                import nacl  # noqa: F401

                self._log("Voice stack OK")
            except ImportError:
                self._log("WARNING: voice stack incomplete — voice may fail")

    def _apply_style(self):
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background-color: {C_BG};
                color: {C_TEXT};
                font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
                font-size: 13px;
            }}
            #RootFrame {{
                background-color: {C_BG};
                border: 1px solid {C_BORDER};
                border-radius: 14px;
            }}
            #TitleBar {{
                background-color: {C_PANEL};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
                border-bottom: 1px solid {C_BORDER_SOFT};
            }}
            #LogoMark {{
                background-color: {C_RED};
                color: white;
                font-size: 14px;
                font-weight: 800;
                border-radius: 8px;
            }}
            #TitleText {{
                color: {C_TEXT};
                font-size: 15px;
                font-weight: 800;
                letter-spacing: 2px;
            }}
            #TitleSub {{
                color: {C_MUTED};
                font-size: 11px;
                font-weight: 500;
            }}
            #StatusChip {{
                background-color: {C_SURFACE};
                color: {C_MUTED};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                padding: 4px 12px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            #WinBtn {{
                background-color: {C_SURFACE};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                color: {C_TEXT};
                font-size: 14px;
                font-weight: 600;
            }}
            #WinBtn:hover {{
                background-color: #1e1e1e;
                border-color: #444;
            }}
            #CloseBtn {{
                background-color: {C_SURFACE};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                color: {C_TEXT};
                font-size: 14px;
                font-weight: 600;
            }}
            #CloseBtn:hover {{
                background-color: {C_RED};
                border-color: {C_RED_GLOW};
                color: white;
            }}
            #Panel {{
                background-color: {C_PANEL};
                border: 1px solid {C_BORDER_SOFT};
                border-radius: 12px;
            }}
            #PanelTitle {{
                color: {C_MUTED};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1.5px;
            }}
            QLineEdit, QSpinBox, QComboBox {{
                background-color: {C_SURFACE};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                padding: 9px 12px;
                color: {C_TEXT};
                selection-background-color: {C_RED_DIM};
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
                border: 1px solid {C_RED};
            }}
            QTextEdit, QListWidget {{
                background-color: {C_SURFACE};
                border: 1px solid {C_BORDER};
                border-radius: 10px;
                padding: 8px;
                color: {C_TEXT};
                selection-background-color: {C_RED_DIM};
            }}
            QListWidget::item {{
                padding: 9px 12px;
                border-radius: 8px;
                margin: 2px 0;
            }}
            QListWidget::item:selected {{
                background-color: #1a1010;
                border: 1px solid {C_RED_DIM};
            }}
            QListWidget::item:hover {{
                background-color: #1a1a1a;
            }}
            QPushButton {{
                background-color: {C_SURFACE};
                border: 1px solid {C_BORDER};
                border-radius: 8px;
                padding: 9px 16px;
                color: {C_TEXT};
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: #1c1c1c;
                border-color: #444;
            }}
            QPushButton:pressed {{
                background-color: #222;
            }}
            QPushButton:disabled {{
                color: #444;
                border-color: #1a1a1a;
                background-color: #0e0e0e;
            }}
            QPushButton#primary {{
                background-color: {C_RED};
                border: 1px solid {C_RED_GLOW};
                color: white;
            }}
            QPushButton#primary:hover {{
                background-color: #f12a3c;
            }}
            QPushButton#danger {{
                border-color: #4a1515;
                color: #fca5a5;
                background-color: #140a0a;
            }}
            QPushButton#danger:hover {{
                background-color: #1f0e0e;
                border-color: {C_DANGER};
            }}
            QPushButton#ghost {{
                background-color: transparent;
                border: 1px solid {C_BORDER};
                color: {C_MUTED};
            }}
            QPushButton#ghost:hover {{
                color: {C_TEXT};
                border-color: #555;
            }}
            QTabWidget::pane {{
                background-color: {C_PANEL};
                border: 1px solid {C_BORDER_SOFT};
                border-radius: 12px;
                top: -1px;
                padding: 10px;
            }}
            QTabBar::tab {{
                background: {C_SURFACE};
                border: 1px solid {C_BORDER};
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 9px 18px;
                margin-right: 4px;
                color: {C_MUTED};
                font-weight: 600;
            }}
            QTabBar::tab:selected {{
                background: {C_PANEL};
                color: {C_TEXT};
                border-color: {C_BORDER};
            }}
            QTabBar::tab:hover:!selected {{
                color: {C_TEXT};
            }}
            QLabel#hint {{
                color: {C_MUTED};
                font-size: 11px;
            }}
            QLabel#lockHint {{
                color: {C_RED};
                font-size: 11px;
                font-weight: 600;
            }}
            QScrollBar:vertical {{
                background: {C_PANEL};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: #333;
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            """
        )

    def _build_ui(self):
        root = QFrame()
        root.setObjectName("RootFrame")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.title_bar = TitleBar(self)
        outer.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(14)
        outer.addLayout(body, 1)

        # LEFT
        left = QVBoxLayout()
        left.setSpacing(12)
        tok = Panel("TOKENS")
        tl = tok.body()

        form = QFormLayout()
        form.setSpacing(8)
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("paste token…")
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Token", self.token_input)
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(["user", "bot"])
        form.addRow("Type", self.kind_combo)
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("optional label")
        form.addRow("Label", self.label_input)
        tl.addLayout(form)

        row = QHBoxLayout()
        add_btn = QPushButton("Add Token")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_token)
        row.addWidget(add_btn)
        load_btn = QPushButton("Load File")
        load_btn.setObjectName("ghost")
        load_btn.clicked.connect(self._load_tokens_file)
        row.addWidget(load_btn)
        tl.addLayout(row)

        self.token_list = QListWidget()
        self.token_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.token_list.itemSelectionChanged.connect(self._update_bot_gates)
        tl.addWidget(self.token_list, 1)

        crow = QHBoxLayout()
        connect_btn = QPushButton("Connect")
        connect_btn.setObjectName("primary")
        connect_btn.clicked.connect(self._connect_selected)
        crow.addWidget(connect_btn)
        disconnect_btn = QPushButton("Disconnect")
        disconnect_btn.setObjectName("danger")
        disconnect_btn.clicked.connect(self._disconnect_selected)
        crow.addWidget(disconnect_btn)
        remove_btn = QPushButton("Remove")
        remove_btn.setObjectName("ghost")
        remove_btn.clicked.connect(self._remove_selected)
        crow.addWidget(remove_btn)
        tl.addLayout(crow)

        util = QHBoxLayout()
        lg = QPushButton("List Guilds")
        lg.setObjectName("ghost")
        lg.clicked.connect(self._action_list_guilds)
        util.addWidget(lg)
        lv = QPushButton("List Voice")
        lv.setObjectName("ghost")
        lv.clicked.connect(self._action_list_voice)
        util.addWidget(lv)
        tl.addLayout(util)

        left.addWidget(tok, 1)
        body.addLayout(left, 2)

        # RIGHT
        right = QVBoxLayout()
        right.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # Server
        server_tab = QWidget()
        sl = QVBoxLayout(server_tab)
        sf = QFormLayout()
        self.guild_id_input = QLineEdit()
        self.guild_id_input.setPlaceholderText("guild / server ID")
        sf.addRow("Guild ID", self.guild_id_input)
        self.invite_input = QLineEdit()
        self.invite_input.setPlaceholderText("invite code or discord.gg/…")
        sf.addRow("Invite", self.invite_input)
        sl.addLayout(sf)
        sbtn = QHBoxLayout()
        jb = QPushButton("Join / Ensure In Server")
        jb.setObjectName("primary")
        jb.clicked.connect(self._action_join_guild)
        sbtn.addWidget(jb)
        lb = QPushButton("Leave Server")
        lb.setObjectName("danger")
        lb.clicked.connect(self._action_leave_guild)
        sbtn.addWidget(lb)
        sl.addLayout(sbtn)
        hint = QLabel("User tokens accept invites. Bot tokens need OAuth2 invite with Connect.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        sl.addWidget(hint)
        sl.addStretch()
        self.tabs.addTab(server_tab, "Server")

        # Text
        text_tab = QWidget()
        xl = QVBoxLayout(text_tab)
        xf = QFormLayout()
        self.channel_id_input = QLineEdit()
        self.channel_id_input.setPlaceholderText("text channel ID")
        xf.addRow("Channel ID", self.channel_id_input)
        self.message_input = QTextEdit()
        self.message_input.setPlaceholderText("message content…")
        self.message_input.setMaximumHeight(100)
        xf.addRow("Message", self.message_input)
        self.msg_delay = QSpinBox()
        self.msg_delay.setRange(0, 120)
        self.msg_delay.setValue(1)
        self.msg_delay.setSuffix(" s gap")
        xf.addRow("Delay", self.msg_delay)
        xl.addLayout(xf)
        send_btn = QPushButton("Send Message")
        send_btn.setObjectName("primary")
        send_btn.clicked.connect(self._action_send)
        xl.addWidget(send_btn)
        xl.addStretch()
        self.tabs.addTab(text_tab, "Text")

        # Voice
        voice_tab = QWidget()
        vl = QVBoxLayout(voice_tab)
        vf = QFormLayout()
        self.voice_guild_input = QLineEdit()
        self.voice_guild_input.setPlaceholderText("guild ID (fallback: Server tab)")
        vf.addRow("Guild ID", self.voice_guild_input)
        self.voice_channel_input = QLineEdit()
        self.voice_channel_input.setPlaceholderText("voice channel ID")
        vf.addRow("Voice Channel", self.voice_channel_input)
        vl.addLayout(vf)
        vb = QHBoxLayout()
        jv = QPushButton("Join Voice")
        jv.setObjectName("primary")
        jv.clicked.connect(self._action_join_voice)
        vb.addWidget(jv)
        lvb = QPushButton("Leave Voice")
        lvb.setObjectName("danger")
        lvb.clicked.connect(self._action_leave_voice)
        vb.addWidget(lvb)
        vl.addLayout(vb)
        vh = QLabel(
            "Voice stays connected until you press Leave or Disconnect.\n"
            "Use List Voice to copy exact channel IDs."
        )
        vh.setObjectName("hint")
        vh.setWordWrap(True)
        vl.addWidget(vh)
        vl.addStretch()
        self.tabs.addTab(voice_tab, "Voice")

        # Bot Commands
        bot_tab = QWidget()
        bl = QVBoxLayout(bot_tab)
        self.bot_lock_label = QLabel("LOCKED — select & connect a BOT token")
        self.bot_lock_label.setObjectName("lockHint")
        bl.addWidget(self.bot_lock_label)
        bf = QFormLayout()
        self.bot_channel_input = QLineEdit()
        self.bot_channel_input.setPlaceholderText("channel ID")
        bf.addRow("Channel", self.bot_channel_input)
        self.bot_cmd_input = QLineEdit()
        self.bot_cmd_input.setPlaceholderText("text / command to send as bot")
        bf.addRow("Command / Text", self.bot_cmd_input)
        bl.addLayout(bf)
        self.bot_send_btn = QPushButton("Execute as Bot")
        self.bot_send_btn.setObjectName("primary")
        self.bot_send_btn.clicked.connect(self._action_bot_command)
        bl.addWidget(self.bot_send_btn)
        bl.addStretch()
        self.tabs.addTab(bot_tab, "Bot Commands")

        # Support
        support_tab = QWidget()
        sul = QVBoxLayout(support_tab)
        self.support_lock_label = QLabel("LOCKED — bot token required")
        self.support_lock_label.setObjectName("lockHint")
        sul.addWidget(self.support_lock_label)
        sf2 = QFormLayout()
        self.support_guild_input = QLineEdit()
        self.support_guild_input.setPlaceholderText("guild ID")
        sf2.addRow("Guild ID", self.support_guild_input)
        self.support_channel_input = QLineEdit()
        self.support_channel_input.setPlaceholderText("channel ID for perms")
        sf2.addRow("Channel ID", self.support_channel_input)
        self.support_role_name = QLineEdit()
        self.support_role_name.setPlaceholderText("new role name")
        sf2.addRow("Role Name", self.support_role_name)
        self.support_role_id = QLineEdit()
        self.support_role_id.setPlaceholderText("role ID to delete")
        sf2.addRow("Role ID", self.support_role_id)
        sul.addLayout(sf2)
        grid = QHBoxLayout()
        self._btn_guild_info = QPushButton("Guild Info")
        self._btn_guild_info.setObjectName("ghost")
        self._btn_guild_info.clicked.connect(self._action_guild_info)
        grid.addWidget(self._btn_guild_info)
        self._btn_list_roles = QPushButton("List Roles")
        self._btn_list_roles.setObjectName("ghost")
        self._btn_list_roles.clicked.connect(self._action_list_roles)
        grid.addWidget(self._btn_list_roles)
        self._btn_list_channels = QPushButton("List Channels")
        self._btn_list_channels.setObjectName("ghost")
        self._btn_list_channels.clicked.connect(self._action_list_channels)
        grid.addWidget(self._btn_list_channels)
        self._btn_channel_perms = QPushButton("Channel Perms")
        self._btn_channel_perms.setObjectName("ghost")
        self._btn_channel_perms.clicked.connect(self._action_channel_perms)
        grid.addWidget(self._btn_channel_perms)
        sul.addLayout(grid)
        rrow = QHBoxLayout()
        self.btn_create_role = QPushButton("Create Role")
        self.btn_create_role.setObjectName("primary")
        self.btn_create_role.clicked.connect(self._action_create_role)
        rrow.addWidget(self.btn_create_role)
        self.btn_delete_role = QPushButton("Delete Role")
        self.btn_delete_role.setObjectName("danger")
        self.btn_delete_role.clicked.connect(self._action_delete_role)
        rrow.addWidget(self.btn_delete_role)
        sul.addLayout(rrow)
        sul.addStretch()
        self.tabs.addTab(support_tab, "Support")

        right.addWidget(self.tabs, 2)

        log_panel = Panel("LOG")
        ll = log_panel.body()
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        ll.addWidget(self.log_view, 1)
        clr = QPushButton("Clear Log")
        clr.setObjectName("ghost")
        clr.clicked.connect(lambda: self.log_view.clear())
        ll.addWidget(clr)
        right.addWidget(log_panel, 2)

        body.addLayout(right, 3)

        self._bot_widgets = [
            self.bot_send_btn,
            self.bot_channel_input,
            self.bot_cmd_input,
            self.btn_create_role,
            self.btn_delete_role,
            self.support_guild_input,
            self.support_channel_input,
            self.support_role_name,
            self.support_role_id,
            self._btn_guild_info,
            self._btn_list_roles,
            self._btn_list_channels,
            self._btn_channel_perms,
        ]

    # ---- gates ----
    def _selected_bot_ready(self) -> list[TokenEntry]:
        return [
            e
            for e in self._selected_entries()
            if e.kind == "bot" and e.ready and e.client and e.loop
        ]

    def _update_bot_gates(self):
        bots = self._selected_bot_ready()
        unlock = len(bots) > 0
        for w in self._bot_widgets:
            w.setEnabled(unlock)
        if unlock:
            names = ", ".join(e.username or token_preview(e.token) for e in bots)
            self.bot_lock_label.setText(f"UNLOCKED · {names}")
            self.bot_lock_label.setStyleSheet(f"color: {C_GREEN}; font-weight: 600; font-size: 11px;")
            self.support_lock_label.setText(f"UNLOCKED · {names}")
            self.support_lock_label.setStyleSheet(f"color: {C_GREEN}; font-weight: 600; font-size: 11px;")
        else:
            self.bot_lock_label.setText("LOCKED — select & connect a BOT token")
            self.bot_lock_label.setStyleSheet(f"color: {C_RED}; font-weight: 600; font-size: 11px;")
            self.support_lock_label.setText("LOCKED — bot token required")
            self.support_lock_label.setStyleSheet(f"color: {C_RED}; font-weight: 600; font-size: 11px;")

    # ---- tokens ----
    def _selected_entries(self) -> list[TokenEntry]:
        out = []
        for item in self.token_list.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None and 0 <= idx < len(self.tokens):
                out.append(self.tokens[idx])
        return out

    def _ready_selected(self) -> list[TokenEntry]:
        return [e for e in self._selected_entries() if e.ready and e.client and e.loop]

    def _refresh_list(self):
        self.token_list.clear()
        for i, e in enumerate(self.tokens):
            prev = token_preview(e.token)
            status = "online" if e.ready else ("error" if e.error else "offline")
            name = e.username or e.label or prev
            item = QListWidgetItem(f"[{e.kind}]  {name}  ·  {status}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            if e.ready:
                item.setForeground(QColor(C_GREEN))
            elif e.error:
                item.setForeground(QColor(C_DANGER))
            else:
                item.setForeground(QColor(C_MUTED))
            self.token_list.addItem(item)
        self._update_bot_gates()

    def _add_token(self):
        tok = self.token_input.text().strip()
        if not tok:
            return
        if any(e.token == tok for e in self.tokens):
            self._log("token already listed")
            return
        entry = TokenEntry(
            token=tok,
            kind=self.kind_combo.currentText(),
            label=self.label_input.text().strip(),
        )
        self.tokens.append(entry)
        self.token_input.clear()
        self.label_input.clear()
        self._refresh_list()
        self._save_config()
        self._log(f"added {token_preview(tok)} ({entry.kind})")

    def _load_tokens_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load tokens", "", "Text (*.txt);;All (*.*)")
        if not path:
            return
        kind = self.kind_combo.currentText()
        added = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                tok = line.strip()
                if not tok or tok.startswith("#"):
                    continue
                if any(e.token == tok for e in self.tokens):
                    continue
                self.tokens.append(TokenEntry(token=tok, kind=kind))
                added += 1
        self._refresh_list()
        self._save_config()
        self._log(f"loaded {added} tokens")

    def _remove_selected(self):
        idxs = sorted(
            [item.data(Qt.ItemDataRole.UserRole) for item in self.token_list.selectedItems()],
            reverse=True,
        )
        for idx in idxs:
            if 0 <= idx < len(self.tokens):
                e = self.tokens[idx]
                prev = token_preview(e.token)
                if prev in self.workers:
                    self.workers[prev].stop()
                    del self.workers[prev]
                del self.tokens[idx]
        self._refresh_list()
        self._save_config()

    def _connect_selected(self):
        if not DISCORD_AVAILABLE:
            self._log("Discord runtime missing")
            return
        for e in self._selected_entries():
            prev = token_preview(e.token)
            if prev in self.workers and self.workers[prev].isRunning():
                self._log(f"{prev} already running")
                continue
            e.ready = False
            e.error = ""
            worker = TokenWorker(e)
            worker.log.connect(self._log)
            worker.status.connect(self._on_status)
            worker.ready_signal.connect(self._on_ready)
            worker.finished_signal.connect(self._on_worker_finished)
            self.workers[prev] = worker
            worker.start()
        self._refresh_list()
        self.title_bar.status_chip.setText("LINKING")
        self.title_bar.status_chip.setStyleSheet(
            f"background:{C_SURFACE};color:{C_RED};border:1px solid {C_BORDER};"
            f"border-radius:8px;padding:4px 12px;font-size:10px;font-weight:700;"
        )

    def _disconnect_selected(self):
        for e in self._selected_entries():
            prev = token_preview(e.token)
            if prev in self.workers:
                self.workers[prev].stop()
                self._log(f"stopping {prev}")
            e.ready = False
            e.client = None
            e.loop = None
        self._refresh_list()
        self.title_bar.status_chip.setText("IDLE")

    def _on_status(self, preview: str, status: str):
        for e in self.tokens:
            if token_preview(e.token) == preview:
                if status == "online":
                    e.ready = True
                elif status in ("login failed", "error", "need self lib"):
                    e.ready = False
                # ignore transient "disconnected" blips so voice stay-alive works
        self._refresh_list()
        self.title_bar.status_chip.setText(status.upper()[:12])

    def _on_ready(self, preview: str, user_id: int, username: str):
        for e in self.tokens:
            if token_preview(e.token) == preview:
                e.ready = True
                e.user_id = user_id
                e.username = username
                e.error = ""
        self._refresh_list()
        self.title_bar.status_chip.setText("ONLINE")
        self.title_bar.status_chip.setStyleSheet(
            f"background:{C_SURFACE};color:{C_GREEN};border:1px solid {C_BORDER};"
            f"border-radius:8px;padding:4px 12px;font-size:10px;font-weight:700;"
        )

    def _on_worker_finished(self, preview: str):
        # Only clear if worker actually ended (disconnect / login fail).
        # Do not force-close the window.
        if preview in self.workers:
            del self.workers[preview]
        for e in self.tokens:
            if token_preview(e.token) == preview:
                e.ready = False
                e.client = None
                e.loop = None
        self._refresh_list()
        self._log(f"{preview} worker finished")

    # ---- actions ----
    def _parse_id(self, raw: str, label: str) -> Optional[int]:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            self._log(f"invalid {label}")
            return None

    def _guild_id(self) -> Optional[int]:
        return self._parse_id(
            self.guild_id_input.text()
            or self.voice_guild_input.text()
            or self.support_guild_input.text(),
            "guild id",
        )

    def _run_on(self, entries: list[TokenEntry], fn: Callable[[TokenEntry], None]):
        if not entries:
            self._log("select connected tokens first")
            return

        def work():
            for e in entries:
                try:
                    fn(e)
                except Exception as ex:
                    self._log(f"[{token_preview(e.token)}] {ex}")

        threading.Thread(target=work, daemon=True).start()

    def _action_join_guild(self):
        gid = self._guild_id()
        invite = self.invite_input.text().strip()
        if gid is None and not invite:
            self._log("need guild id and/or invite")
            return

        def per(e: TokenEntry):
            if e.kind == "bot" and invite and gid is not None:
                result = run_coro(e, dp_ensure_guild(e.client, gid, ""))
                self._log(f"[{token_preview(e.token)}] {result} (bot: invite ignored)")
                return
            if gid is not None:
                result = run_coro(e, dp_ensure_guild(e.client, gid, invite))
            else:
                result = run_coro(e, dp_join_invite(e.client, invite))
            self._log(f"[{token_preview(e.token)}] {result}")

        self._run_on(self._ready_selected(), per)

    def _action_leave_guild(self):
        gid = self._guild_id()
        if gid is None:
            self._log("need guild id")
            return

        def per(e: TokenEntry):
            self._log(f"[{token_preview(e.token)}] {run_coro(e, dp_leave_guild(e.client, gid))}")

        self._run_on(self._ready_selected(), per)

    def _action_send(self):
        cid = self._parse_id(self.channel_id_input.text(), "channel id")
        content = self.message_input.toPlainText().strip()
        if cid is None or not content:
            self._log("need channel id + message")
            return
        delay = self.msg_delay.value()

        def per(e: TokenEntry):
            self._log(
                f"[{token_preview(e.token)}] {run_coro(e, dp_send_message(e.client, cid, content))}"
            )
            if delay > 0:
                time.sleep(delay)

        self._run_on(self._ready_selected(), per)

    def _action_join_voice(self):
        gid = self._parse_id(
            self.voice_guild_input.text() or self.guild_id_input.text(), "guild id"
        )
        cid = self._parse_id(self.voice_channel_input.text(), "voice channel id")
        invite = self.invite_input.text().strip()
        if gid is None or cid is None:
            self._log("need guild id + voice channel id")
            return

        def per(e: TokenEntry):
            try:
                if invite and e.kind == "user":
                    self._log(
                        f"[{token_preview(e.token)}] {run_coro(e, dp_ensure_guild(e.client, gid, invite))}"
                    )
                result = run_coro(e, dp_join_voice(e.client, gid, cid), timeout=60.0)
                self._log(f"[{token_preview(e.token)}] {result}")
            except Exception as ex:
                self._log(f"[{token_preview(e.token)}] voice error: {ex}")

        self._run_on(self._ready_selected(), per)

    def _action_leave_voice(self):
        gid = self._parse_id(
            self.voice_guild_input.text() or self.guild_id_input.text(), "guild id"
        )
        if gid is None:
            self._log("need guild id")
            return

        def per(e: TokenEntry):
            self._log(f"[{token_preview(e.token)}] {run_coro(e, dp_leave_voice(e.client, gid))}")

        self._run_on(self._ready_selected(), per)

    def _action_list_guilds(self):
        def per(e: TokenEntry):
            self._log(f"[{token_preview(e.token)}] {run_coro(e, dp_list_guilds(e.client))}")

        self._run_on(self._ready_selected(), per)

    def _action_list_voice(self):
        gid = self._guild_id()
        if gid is None:
            self._log("paste Guild ID first")
            return

        def per(e: TokenEntry):
            self._log(
                f"[{token_preview(e.token)}] {run_coro(e, dp_list_voice_channels(e.client, gid))}"
            )

        self._run_on(self._ready_selected(), per)

    def _require_bots(self) -> list[TokenEntry]:
        bots = self._selected_bot_ready()
        if not bots:
            self._log("Bot Commands / Support require a connected BOT token selected")
            return []
        return bots

    def _action_bot_command(self):
        bots = self._require_bots()
        if not bots:
            return
        cid = self._parse_id(self.bot_channel_input.text(), "channel id")
        text = self.bot_cmd_input.text().strip()
        if cid is None or not text:
            self._log("need channel + command/text")
            return

        def per(e: TokenEntry):
            self._log(
                f"[{token_preview(e.token)}] {run_coro(e, dp_bot_send_as_command(e.client, cid, text))}"
            )

        self._run_on(bots, per)

    def _support_guild(self) -> Optional[int]:
        return self._parse_id(
            self.support_guild_input.text() or self.guild_id_input.text(), "guild id"
        )

    def _action_guild_info(self):
        bots = self._require_bots()
        gid = self._support_guild()
        if not bots or gid is None:
            if gid is None:
                self._log("need guild id")
            return

        def per(e: TokenEntry):
            self._log(f"[{token_preview(e.token)}]\n{run_coro(e, dp_guild_info(e.client, gid))}")

        self._run_on(bots, per)

    def _action_list_roles(self):
        bots = self._require_bots()
        gid = self._support_guild()
        if not bots or gid is None:
            if gid is None:
                self._log("need guild id")
            return

        def per(e: TokenEntry):
            self._log(f"[{token_preview(e.token)}]\n{run_coro(e, dp_fetch_roles(e.client, gid))}")

        self._run_on(bots, per)

    def _action_list_channels(self):
        bots = self._require_bots()
        gid = self._support_guild()
        if not bots or gid is None:
            if gid is None:
                self._log("need guild id")
            return

        def per(e: TokenEntry):
            self._log(
                f"[{token_preview(e.token)}]\n{run_coro(e, dp_fetch_channels(e.client, gid))}"
            )

        self._run_on(bots, per)

    def _action_channel_perms(self):
        bots = self._require_bots()
        cid = self._parse_id(
            self.support_channel_input.text() or self.channel_id_input.text(), "channel id"
        )
        if not bots or cid is None:
            if cid is None:
                self._log("need channel id")
            return

        def per(e: TokenEntry):
            self._log(
                f"[{token_preview(e.token)}]\n{run_coro(e, dp_channel_permissions(e.client, cid))}"
            )

        self._run_on(bots, per)

    def _action_create_role(self):
        bots = self._require_bots()
        gid = self._support_guild()
        name = self.support_role_name.text().strip()
        if not bots or gid is None or not name:
            self._log("need guild id + role name")
            return

        def per(e: TokenEntry):
            self._log(
                f"[{token_preview(e.token)}] {run_coro(e, dp_create_role(e.client, gid, name))}"
            )

        self._run_on(bots, per)

    def _action_delete_role(self):
        bots = self._require_bots()
        gid = self._support_guild()
        rid = self._parse_id(self.support_role_id.text(), "role id")
        if not bots or gid is None or rid is None:
            self._log("need guild id + role id")
            return

        def per(e: TokenEntry):
            self._log(
                f"[{token_preview(e.token)}] {run_coro(e, dp_delete_role(e.client, gid, rid))}"
            )

        self._run_on(bots, per)

    # ---- config / log ----
    def _log(self, msg: str):
        self.log_view.append(msg)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _save_config(self):
        data = {
            "tokens": [
                {"token": e.token, "kind": e.kind, "label": e.label} for e in self.tokens
            ],
            "guild_id": self.guild_id_input.text(),
            "invite": self.invite_input.text(),
            "channel_id": self.channel_id_input.text(),
            "voice_channel_id": self.voice_channel_input.text(),
            "voice_guild_id": self.voice_guild_input.text(),
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            self._log(f"config save failed: {e}")

    def _load_config(self):
        # migrate old config name if present
        old = Path(__file__).resolve().parent / "discord_botter_config.json"
        path = CONFIG_PATH if CONFIG_PATH.exists() else old
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for t in data.get("tokens", []):
                tok = t.get("token", "")
                if not tok:
                    continue
                self.tokens.append(
                    TokenEntry(
                        token=tok,
                        kind=t.get("kind", "user"),
                        label=t.get("label", ""),
                    )
                )
            self.guild_id_input.setText(data.get("guild_id", ""))
            self.invite_input.setText(data.get("invite", ""))
            self.channel_id_input.setText(data.get("channel_id", ""))
            self.voice_channel_input.setText(data.get("voice_channel_id", ""))
            self.voice_guild_input.setText(data.get("voice_guild_id", ""))
            self._refresh_list()
        except Exception as e:
            self._log(f"config load failed: {e}")

    def closeEvent(self, event):
        # Only stop workers when the user actually closes the window
        for w in list(self.workers.values()):
            try:
                w.stop()
            except Exception:
                pass
        self._save_config()
        super().closeEvent(event)


def main():
    # Prevent hard abort on background thread exceptions (Qt/PyQt)
    sys.excepthook = lambda *args: print("excepthook:", args, file=sys.stderr)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Keep app alive even if last window momentarily hides
    app.setQuitOnLastWindowClosed(True)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
