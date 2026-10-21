# Social Downloader Bot 🤖

A Telegram bot that auto-downloads **TikTok**, **Instagram Reels**, and **YouTube Shorts** when links are shared in a group.

Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) and
[pyrotgfork](https://github.com/TelegramPlayground/PyroTGFork), using Telegram's
MTProto API for bot login, messages, and uploads.

## Features

- **Auto-download** — just paste a TikTok, Instagram Reel, or YouTube Shorts link in any group chat
- **Manual download** — `/dl <url>` for YouTube, Facebook, and other supported platforms
- **Works for everyone** — not just you. Anyone in the group can share a link and it gets downloaded
- **Clean uploads** — videos are re-uploaded to Telegram with title and source URL as caption
- **Instagram photos with music** — single-photo posts/reels are rendered into an MP4 with their attached audio when available. If audio download, rendering, or video upload fails, the bot sends the photo instead.
- **Large video uploads** — up to 2000 MiB through MTProto

## Quick Start

### 1. Create a Telegram Bot

Talk to [@BotFather](https://t.me/BotFather) on Telegram:

```
/newbot
```

Follow the prompts, get your **bot token** (looks like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`).

### 2. Configure

Copy `config.ini.example` to `config.ini`. Get an `api_id` and `api_hash` from
[my.telegram.org/apps](https://my.telegram.org/apps) (existing Telegram application
credentials can be reused), and keep this bot's token from @BotFather:

```ini
[pyrogram]
api_id = 12345
api_hash = your_telegram_api_hash
bot_token = 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
workdir = workdir

[proxy]
# Optional — only if TikTok/Instagram are blocked in your region
socks5_proxy = socks5://username:password@ip-address:port
```

For an existing installation, rename `[bot]` to `[pyrogram]` and add `api_id`
and `api_hash`. Login still uses the bot token; no phone login is needed.
The first run creates `workdir/social_dl_bot.session`. Preserve `workdir/`
between restarts; Docker Compose mounts it automatically. Each bot must use its
own session file. The proxy above applies to media downloads only.

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

Use Python 3.10 or newer. TgCrypto may need a C compiler and Python development
headers to install locally (for example, `build-essential` and `python3-dev` on
Debian/Ubuntu). The Docker build compiles it automatically.

Make sure `yt-dlp` is installed:
```bash
which yt-dlp || pip install -U yt-dlp[default,curl-cffi]
```

Install `ffmpeg` (including `ffprobe`) to render Instagram photos with audio;
the Docker image already includes both. Without them, photo posts fall back to
the image. The bot uses Instagram's audio start time and clip duration when
available, otherwise a 15-second clip, capped at 90 seconds. Access to the
photo/audio depends on the metadata Instagram makes available; private or
restricted posts may still fail. Multi-photo carousels are not rendered.

Run the local checks with `python -m unittest discover -s tests -v`.

### 4. Run It

```bash
python main.py
```

For Docker, run `docker compose up -d --build` after updating the config.

### 5. Add Bot to Your Group

1. Open Telegram
2. Add your bot to the group (search by the username @BotFather gave you)
3. Make it an **admin** or at least give it **"Send Messages"** and **"Send Media"** permissions
4. Done! Share a TikTok link and watch the magic ✨

## Commands

| Command | Description |
|---|---|
| `/start` | Show welcome message |
| `/help` | Show usage help |
| `/dl <url>` | Manually download any supported video (YouTube, Facebook, etc.) |

## What Gets Auto-Downloaded

These platforms are downloaded **automatically** when a link is shared:

| Platform | Example Link |
|---|---|
| TikTok | `https://tiktok.com/@user/video/123...` |
| TikTok (short) | `https://vm.tiktok.com/xxxxx/` |
| Instagram Reel | `https://instagram.com/reel/xxxxx/` |
| Instagram Post | `https://instagram.com/p/xxxxx/` |
| YouTube Shorts | `https://youtube.com/shorts/xxxxx` |

These platforms work with `/dl` command only:

| Platform | Example |
|---|---|
| YouTube | `/dl https://youtube.com/watch?v=xxxxx` |
| Facebook | `/dl https://facebook.com/watch/?v=xxxxx` |

## How It Works

1. Bot monitors group messages
2. When it spots a TikTok / Instagram / YouTube Shorts link, it extracts the URL
3. Follows redirects and normalises the URL (e.g. `ddinstagram.com` → `instagram.com`)
4. Downloads the video using `yt-dlp` to a temporary directory
5. Uploads the video back to the group with a caption
6. Cleans up the temporary files

## Troubleshooting

**"Download failed"**
- Try with a proxy if TikTok/Instagram are blocked in your region
- Make sure yt-dlp is up to date: `pip install -U yt-dlp[default,curl-cffi]`

**"Bot isn't responding"**
- Make sure the bot is a **group admin** or has permission to read messages
- Check the `bot.log` file for errors

**"File too large"**
- The bot uploads through MTProto, with pyrotgfork's 2000 MiB limit for bot
  sessions. Videos above 50 MB can be uploaded without a local Bot API server.

## License

MIT — do whatever you want with it.
