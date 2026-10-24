# Caustic Overlay (v16.1)

A transparent chat overlay for Windows that merges **Twitch, Kick and YouTube** chat into one window over your game.

## New name and icon (v16 / v16.1)

The icon is a green, dripping **C** (v16.1).


MultiChat Overlay is now **Caustic Overlay**. If you installed the old version, just build and run the new installer:

- It closes and **removes the old MultiChat Overlay** (program, shortcuts, Apps-list entry).
- On first start, Caustic Overlay **copies your settings and sounds** from `%APPDATA%\MultiChat Overlay` to `%APPDATA%\Caustic Overlay`. The old folder is left alone; delete it whenever you like.

## Install it as a normal Windows program

1. Double-click **`Caustic-Overlay-Setup-'version'.exe`**. No admin rights needed. You get:
   - a Start Menu shortcut, plus an optional desktop shortcut and optional "start when Windows starts",
   - an uninstaller in **Settings → Apps**.

**"Windows protected your PC"?** Windows warns about any new program that isn't signed with a paid code-signing certificate. Click **More info → Run anyway**.

When installed, settings, `connection_log.txt` and your own sounds live in **`%APPDATA%\Caustic Overlay`** (paste that into Explorer's address bar to open it). They're kept when you update or uninstall. To update later, build a new Setup.exe from the new version and run it over the old one.

Only one copy of the overlay runs at a time: starting it again brings the open one to the front.

## Connect your channels

1. Click ⚙ (or right-click the tray icon → **Settings…**) and open the **Connections** tab.
2. Fill in whichever platforms you stream on:
   - **Twitch channel**: your channel name or link.
   - **Kick channel**: your channel name or link.
   - **YouTube channel**: your `@handle`, channel link, or a live video link.
3. Click **Save**. The header shows each platform's state:
   - `TWITCH ●` connected (in the platform's colour)
   - `KICK …` connecting
   - `YOUTUBE ○` waiting (YouTube isn't live yet; it checks every minute)
   - `KICK ✕` problem: the footer says what's wrong

The footer also says *why* a platform isn't connected, e.g. `YouTube: not live yet, checks every minute`.

Leave a box empty to turn that platform off. Messages deleted by a moderator, and messages from banned or timed-out users, disappear from the overlay too.

### If Kick says "blocked the lookup"

To join Kick chat, the overlay needs the channel's **chatroom ID**. It asks kick.com for it automatically and remembers it, but Kick's Cloudflare protection sometimes blocks programs that aren't web browsers. If that happens:

1. In **Settings → Connections**, click **Find ID…**. Your browser opens a page full of text.
2. Press **Ctrl+F**, search for `"chatroom"`, and copy the number right after `"id":` that follows it.
3. Paste it into **Kick chatroom ID** and click **Save**.

You only need to do this once per channel.

### How each platform is read

| Platform | Method | Reliability |
|---|---|---|
| Twitch | Twitch's official anonymous, read-only chat connection | Very reliable |
| Kick | Kick's public live-chat feed (the one kick.com itself uses) | Good; Kick can change it without notice |
| YouTube | The same chat feed youtube.com's own chat pop-out uses | Unofficial, so it can break if YouTube changes its site. If it does, only YouTube stops; Twitch and Kick keep working |

Emotes show as their names (e.g. `KEKW`) for now. Super Chats show the amount (💲 $5.00), and new members show ⭐.

### Something won't connect?

Every connection step is written to **`connection_log.txt`** in this folder. Send that file in a created issue here https://github.com/Dwenrur/Caustic-Overlay/issues

**v13.1 fix:** in mid-October 2026 Kick changed the key its chat server accepts, so the old key was refused ("server closed the connection"). The overlay now asks Kick for the current key every time it connects, and has the new key built in as a backup.

## Message sound (new in v14)

A short chime plays when new chat arrives. Set it up in **Settings → Sound**:

- **Play a sound…**: on/off.
- **Volume**: 0–100 %. Let go of the slider to hear the new volume.
- **Quiet time**: at most one sound per this many seconds (default 1 s), so a busy chat doesn't turn into a drumroll. 0 = a sound for every message.
- **Choose sound…**: use your own **WAV, MP3 or WMA** file. It's copied into the `sounds` folder, so it keeps working if you move the original.
- **Built-in chime**: switch back to the default.
- **▶ Test**: play it now.

Quick on/off any time: right-click the tray icon → **Message sound**.

## Chat-only mode

**Ctrl+Shift+X**, or right-click the tray icon and untick **Show border**. The frame, outline, header and footer disappear, and clicks go through to your game. Bring it back with the tray icon → **Show border**, a double-click on the tray icon, or Ctrl+Shift+X again.

## Other controls

- **Resize grip:** the purple diagonal lines in the bottom-right corner.
- **Background opacity:** fades the panel; text stays solid.
- **Overlays** only show over games in **windowed** or **borderless windowed** mode, not exclusive fullscreen.
