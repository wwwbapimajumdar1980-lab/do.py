# ============================================================
# TELEGRAM VIDEO DOWNLOADER BOT
# YouTube / Instagram / Facebook / TikTok / X etc.
# API ছাড়া - yt-dlp based
#
# Install:
#   pkg install python ffmpeg -y
#   pip install -U python-telegram-bot yt-dlp
#
# Run:
#   export BOT_TOKEN="YOUR_BOT_TOKEN"
#   python bot.py
# ============================================================

import os
import re
import asyncio
import shutil
import tempfile
from pathlib import Path

import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    print("\n❌ BOT_TOKEN পাওয়া যায়নি!")
    print('Termux-এ চালাও:')
    print('export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"\n')
    raise SystemExit(1)


# Maximum concurrent downloads
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

# Temporary directory
BASE_DIR = Path(tempfile.gettempdir()) / "telegram_video_bot"
BASE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# URL DETECTION
# ============================================================

URL_PATTERN = re.compile(
    r"https?://[^\s]+",
    re.IGNORECASE
)


def extract_url(text: str):
    if not text:
        return None

    match = URL_PATTERN.search(text)

    if not match:
        return None

    return match.group(0).rstrip(").,!?]}>\"'")


# ============================================================
# BASIC PLATFORM DETECTION
# ============================================================

def detect_platform(url: str):

    u = url.lower()

    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"

    if "instagram.com" in u:
        return "Instagram"

    if "facebook.com" in u or "fb.watch" in u:
        return "Facebook"

    if "tiktok.com" in u:
        return "TikTok"

    if "twitter.com" in u or "x.com" in u:
        return "X / Twitter"

    return "Supported Website"


# ============================================================
# SAFE FILENAME
# ============================================================

def safe_filename(name: str):

    name = name or "video"

    name = re.sub(r'[\\/*?:"<>|]', "_", name)

    return name[:100]


# ============================================================
# GET VIDEO INFORMATION
# ============================================================

def get_video_info(url: str):

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,

        # Don't use login/cookies.
        "cookiefile": None,

        # Don't bypass DRM/paywalls/private access.
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    return info


# ============================================================
# QUALITY EXTRACTION
# ============================================================

def get_qualities(info):

    formats = info.get("formats", [])

    qualities = {}

    for f in formats:

        height = f.get("height")
        ext = f.get("ext")

        if not height:
            continue

        # Only common video qualities
        if height not in [360, 480, 720, 1080, 1440, 2160]:
            continue

        # Need actual video
        has_video = f.get("vcodec") not in (None, "none")

        if not has_video:
            continue

        # Prefer mp4
        score = 1

        if ext == "mp4":
            score = 10

        old = qualities.get(height)

        if not old or score > old[0]:
            qualities[height] = (score, f)

    result = []

    for height in sorted(qualities.keys()):

        f = qualities[height][1]

        result.append({
            "height": height,
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
        })

    return result


# ============================================================
# QUALITY BUTTONS
# ============================================================

def quality_keyboard(qualities):

    buttons = []

    for q in qualities:

        height = q["height"]

        label = f"🎬 {height}p"

        buttons.append([
            InlineKeyboardButton(
                label,
                callback_data=f"quality|{q['format_id']}|{height}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🎵 Audio MP3",
            callback_data="audio"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data="cancel"
        )
    ])

    return InlineKeyboardMarkup(buttons)


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "👋 <b>Video Downloader Bot</b>\n\n"
        "🎬 YouTube\n"
        "📸 Instagram\n"
        "📘 Facebook\n"
        "🎵 TikTok\n"
        "🐦 X / Twitter\n\n"
        "শুধু একটি <b>public video URL</b> পাঠাও।\n\n"
        "তারপর আমি available quality দেখাবো।"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# ============================================================
# HELP
# ============================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "ℹ️ <b>How to use</b>\n\n"
        "1️⃣ Video link copy করো\n"
        "2️⃣ এখানে paste করো\n"
        "3️⃣ Quality select করো\n"
        "4️⃣ Bot video process করবে\n"
        "5️⃣ Telegram-এ video পেয়ে যাবে\n\n"
        "⚠️ শুধু এমন content download করো "
        "যেটা download করার অনুমতি তোমার আছে।"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# ============================================================
# URL MESSAGE
# ============================================================

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        return

    url = extract_url(update.message.text)

    if not url:
        await update.message.reply_text(
            "❌ কোনো valid video URL পাওয়া যায়নি।\n\n"
            "Example:\n"
            "https://www.youtube.com/watch?v=..."
        )
        return

    platform = detect_platform(url)

    status = await update.message.reply_text(
        f"🔎 <b>{platform}</b> link detected...\n\n"
        "⏳ Video information checking করছি...",
        parse_mode="HTML"
    )

    try:

        loop = asyncio.get_running_loop()

        info = await loop.run_in_executor(
            None,
            get_video_info,
            url
        )

        title = safe_filename(
            info.get("title", "Video")
        )

        duration = info.get("duration")

        if duration:
            minutes = duration // 60
            seconds = duration % 60
            duration_text = f"{minutes}:{seconds:02d}"
        else:
            duration_text = "Unknown"

        qualities = get_qualities(info)

        if not qualities:

            await status.edit_text(
                "❌ এই video-র কোনো supported downloadable "
                "video format পাওয়া যায়নি।"
            )
            return

        # Save URL and metadata for callback
        context.user_data["download_url"] = url
        context.user_data["video_title"] = title

        quality_text = "\n".join(
            f"• {q['height']}p"
            for q in qualities
        )

        message = (
            "✅ <b>Video Found!</b>\n\n"
            f"📌 <b>{title}</b>\n"
            f"🌐 Platform: {platform}\n"
            f"⏱ Duration: {duration_text}\n\n"
            "<b>Download Quality Select করো:</b>\n\n"
            f"{quality_text}"
        )

        await status.edit_text(
            message,
            parse_mode="HTML",
            reply_markup=quality_keyboard(qualities)
        )

    except Exception as e:

        print("INFO ERROR:", repr(e))

        await status.edit_text(
            "❌ Video information পাওয়া যায়নি।\n\n"
            "সম্ভবত link private, unavailable, "
            "unsupported অথবা site restriction আছে।"
        )


# ============================================================
# DOWNLOAD VIDEO
# ============================================================

def download_video(url, format_id, height, folder):

    output_template = str(
        Path(folder) / "%(title).80s.%(ext)s"
    )

    options = {

        "format": (
            f"{format_id}+bestaudio/"
            f"{format_id}/"
            f"best[height<={height}]"
        ),

        "outtmpl": output_template,

        "merge_output_format": "mp4",

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "retries": 2,

        "fragment_retries": 2,

        "concurrent_fragment_downloads": 4,

        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        ydl.download([url])

    files = list(Path(folder).glob("*"))

    video_files = [
        f for f in files
        if f.is_file()
        and f.suffix.lower()
        in [".mp4", ".mkv", ".webm", ".mov"]
    ]

    if not video_files:
        raise RuntimeError("Video file তৈরি হয়নি")

    return max(
        video_files,
        key=lambda f: f.stat().st_size
    )


# ============================================================
# AUDIO DOWNLOAD
# ============================================================

def download_audio(url, folder):

    output_template = str(
        Path(folder) / "%(title).80s.%(ext)s"
    )

    options = {

        "format": "bestaudio/best",

        "outtmpl": output_template,

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        ydl.download([url])

    files = list(Path(folder).glob("*.mp3"))

    if not files:
        raise RuntimeError("MP3 তৈরি হয়নি")

    return max(
        files,
        key=lambda f: f.stat().st_size
    )


# ============================================================
# QUALITY CALLBACK
# ============================================================

async def quality_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "cancel":

        await query.edit_message_text(
            "❌ Download cancelled."
        )

        return

    if data == "audio":

        await process_audio(
            update,
            context
        )

        return

    if not data.startswith("quality|"):
        return

    parts = data.split("|")

    if len(parts) != 3:
        return

    format_id = parts[1]
    height = parts[2]

    url = context.user_data.get("download_url")

    if not url:

        await query.edit_message_text(
            "❌ Download session expired.\n"
            "Link আবার send করো।"
        )

        return

    await process_video(
        update,
        context,
        url,
        format_id,
        height
    )


# ============================================================
# PROCESS VIDEO
# ============================================================

async def process_video(
    update,
    context,
    url,
    format_id,
    height
):

    query = update.callback_query

    user_id = query.from_user.id

    folder = BASE_DIR / str(user_id)

    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)

    folder.mkdir(parents=True, exist_ok=True)

    try:

        await query.edit_message_text(
            f"⏳ <b>{height}p</b> video processing হচ্ছে...\n\n"
            "এটা video size অনুযায়ী কিছুটা সময় নিতে পারে।",
            parse_mode="HTML"
        )

        async with DOWNLOAD_SEMAPHORE:

            await context.bot.send_chat_action(
                chat_id=query.message.chat_id,
                action=ChatAction.UPLOAD_VIDEO
            )

            loop = asyncio.get_running_loop()

            video_file = await loop.run_in_executor(
                None,
                download_video,
                url,
                format_id,
                height,
                str(folder)
            )

        size_mb = video_file.stat().st_size / (
            1024 * 1024
        )

        await query.edit_message_text(
            f"✅ <b>Video Ready!</b>\n\n"
            f"🎬 Quality: {height}p\n"
            f"📦 Size: {size_mb:.1f} MB\n\n"
            "⬆️ Telegram-এ upload করছি...",
            parse_mode="HTML"
        )

        with open(video_file, "rb") as video:

            await context.bot.send_video(
                chat_id=query.message.chat_id,
                video=video,
                caption=(
                    f"🎬 <b>Downloaded</b>\n\n"
                    f"📺 Quality: {height}p"
                ),
                parse_mode="HTML",
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
            )

    except Exception as e:

        print("DOWNLOAD ERROR:", repr(e))

        await query.edit_message_text(
            "❌ Download failed.\n\n"
            "সম্ভবত selected quality available নয় "
            "অথবা file খুব বড়।"
        )

    finally:

        shutil.rmtree(
            folder,
            ignore_errors=True
        )


# ============================================================
# PROCESS AUDIO
# ============================================================

async def process_audio(
    update,
    context
):

    query = update.callback_query

    url = context.user_data.get("download_url")

    if not url:

        await query.edit_message_text(
            "❌ Download session expired.\n"
            "Link আবার send করো।"
        )

        return

    user_id = query.from_user.id

    folder = BASE_DIR / str(user_id)

    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)

    folder.mkdir(parents=True, exist_ok=True)

    try:

        await query.edit_message_text(
            "🎵 <b>MP3 তৈরি হচ্ছে...</b>\n\n"
            "⏳ Please wait...",
            parse_mode="HTML"
        )

        async with DOWNLOAD_SEMAPHORE:

            loop = asyncio.get_running_loop()

            audio_file = await loop.run_in_executor(
                None,
                download_audio,
                url,
                str(folder)
            )

        size_mb = audio_file.stat().st_size / (
            1024 * 1024
        )

        await query.edit_message_text(
            f"✅ <b>MP3 Ready!</b>\n\n"
            f"📦 Size: {size_mb:.1f} MB\n\n"
            "⬆️ Telegram-এ upload করছি...",
            parse_mode="HTML"
        )

        with open(audio_file, "rb") as audio:

            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=audio,
                caption="🎵 <b>Audio Ready</b>",
                parse_mode="HTML",
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
            )

    except Exception as e:

        print("AUDIO ERROR:", repr(e))

        await query.edit_message_text(
            "❌ MP3 download failed."
        )

    finally:

        shutil.rmtree(
            folder,
            ignore_errors=True
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print("======================================")
    print(" Telegram Video Downloader Bot")
    print("======================================")
    print("Bot starting...")
    print("")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    app.add_handler(
        CallbackQueryHandler(
            quality_callback
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url
        )
    )

    print("✅ Bot is running!")
    print("Send /start on Telegram.")
    print("")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
