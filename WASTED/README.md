# WASTED

**Multi-token Discord controller** — connect user tokens and bot tokens, join servers, send messages, join/leave voice, and run bot support tools from a single desktop app.

Modern red/black UI. Frameless window with minimize / maximize / close.

---

## What this app does

| Feature | Description |
|--------|-------------|
| **Multi-token** | Add many Discord tokens (user or bot). Connect several at once. |
| **Server join** | Paste guild ID + invite. User tokens can accept invites; bots must already be in the server via OAuth2. |
| **Text** | Send a message from every selected connected token (optional delay between them). |
| **Voice** | Join or leave a voice channel. Connection stays up until you leave or disconnect. |
| **Bot Commands** | Locked to **bot** tokens. Send text/commands as the bot into a channel. |
| **Support** | Locked to **bot** tokens. Guild info, list roles/channels, channel permissions, create/delete roles. |

**Not a public bot framework.** It is a local controller for tokens you own. You are responsible for how you use it and for Discord’s Terms of Service.

---

## Requirements

- Windows 10/11 (primary target for the `.exe`)
- Python **3.10–3.12** recommended (3.14 may fail building voice extras)
- For **user tokens**: self-bot compatible Discord library (see install)
- For **voice**: PyNaCl / voice stack

---

## Install (from source)

```bat
git clone https://github.com/YOUR_USER/WASTED.git
cd WASTED
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python wasted.py
```

### User tokens + bot tokens (recommended)

Official bot-only libraries reject user tokens. Install the self-bot runtime **instead of** the bot-only package:

```bat
pip uninstall discord.py -y
pip install discord.py-self PyQt6
```

If voice is needed and your Python version has wheels:

```bat
pip install "discord.py-self[voice]" PyQt6
```

On Python 3.14, `[voice]` may try to compile Rust (`davey`) and fail without Visual Studio Build Tools. Use **Python 3.12** or install without `[voice]` first.

### Bot tokens only

```bat
pip install discord.py PyNaCl PyQt6
```

---

## Build the `.exe` (Windows)

```bat
venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
build_exe.bat
```

Output:

```text
dist\WASTED.exe
```

One-folder build (optional):

```bat
pyinstaller --noconfirm WASTED.spec
```

---

## Usage

1. **Add Token** — paste token, set type `user` or `bot`, optional label.
2. Select token(s) → **Connect**. Status goes green when online.
3. **Server** — guild ID + invite → **Join / Ensure In Server**.
4. **List Voice** — copy a voice channel ID from the log.
5. **Voice** — paste IDs → **Join Voice**. Stays connected until **Leave Voice**.
6. **Bot Commands / Support** — select a connected **bot** token to unlock.

Config is saved next to the app as `wasted_config.json` (tokens + last IDs).

---

## Controls

| Control | Action |
|--------|--------|
| **─** | Minimize |
| **□** | Maximize / restore |
| **✕** | Close (stops workers, saves config) |
| Drag title bar | Move window |

---

## Project layout

```text
WASTED/
  wasted.py           # main app
  requirements.txt
  build_exe.bat
  WASTED.spec         # PyInstaller spec
  README.md
  .gitignore
```

---

## Notes

- User-token automation can violate Discord ToS. Use only accounts you control; expect bans if abused.
- Bot tokens need the bot invited with the right permissions (Connect for voice, Manage Roles for role tools, etc.).
- Tokens are stored locally in `wasted_config.json` — do not commit that file.

---

## License

Use at your own risk. No warranty.
