
import os
import asyncio
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
INSTAGRAM_USER_ID = os.getenv("INSTAGRAM_USER_ID", "").strip()
INSTAGRAM_GRAPH_URL = os.getenv(
    "INSTAGRAM_GRAPH_URL", "https://graph.instagram.com"
).rstrip("/")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_UPLOAD_PRESET = os.getenv("CLOUDINARY_UPLOAD_PRESET", "").strip()

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "bot_uploads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

MENU = ReplyKeyboardMarkup(
    [
        ["📸 Upload Photos", "⏱️ Set Interval"],
        ["✅ Done Uploading", "🚀 Start Posting"],
        ["🛑 Stop Posting", "❓ Help"],
    ],
    resize_keyboard=True,
)

# One independent session per Telegram chat.
SESSIONS = {}
POSTING_TASKS = {}


def get_session(chat_id):
    if chat_id not in SESSIONS:
        SESSIONS[chat_id] = {
            "photos": [],
            "captions": [],
            "caption_index": None,
            "upload_mode": False,
            "interval": 60,
            "posting": False,
            "next_post": 0,
        }
    return SESSIONS[chat_id]


def main_menu_text():
    return (
        "Choose an option:\n\n"
        "📸 Upload Photos — add photos\n"
        "✅ Done Uploading — finish adding photos\n"
        "⏱️ Set Interval — choose time between posts\n"
        "🚀 Start Posting — begin publishing\n"
        "🛑 Stop Posting — stop publishing"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    get_session(chat_id)
    await update.effective_message.reply_text(
        "Welcome! Your photo posting bot is ready.\n\n"
        + main_menu_text(),
        reply_markup=MENU,
    )


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "HOW TO USE\n\n"
        "1. Press 📸 Upload Photos.\n"
        "2. Send your photos one by one or as an album.\n"
        "3. Press ✅ Done Uploading.\n"
        "4. The bot asks for a caption for each photo.\n"
        "5. Type a caption, or send /skipcaption.\n"
        "6. Set an interval and press 🚀 Start Posting.\n\n"
        "Note: Instagram publishing requires a properly configured "
        "Instagram API account and permissions.",
        reply_markup=MENU,
    )


async def upload_photos_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if session["posting"]:
        await update.effective_message.reply_text(
            "Stop posting before starting a new upload."
        )
        return

    session["upload_mode"] = True
    session["caption_index"] = None
    session["photos"] = []
    session["captions"] = []

    await update.effective_message.reply_text(
        "📸 Send your photos now.\n\n"
        "When all photos are sent, press ✅ Done Uploading.",
        reply_markup=MENU,
    )


async def receive_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if session["caption_index"] is not None:
        await message.reply_text(
            "Please send a caption as text, or type /skipcaption."
        )
        return

    if not session["upload_mode"]:
        await message.reply_text(
            "First press 📸 Upload Photos."
        )
        return

    photo_file = None
    original_name = "photo.jpg"

    if message.photo:
        photo_file = await message.photo[-1].get_file()
    elif message.document and message.document.mime_type:
        if message.document.mime_type.startswith("image/"):
            photo_file = await message.document.get_file()
            original_name = message.document.file_name or original_name

    if photo_file is None:
        await message.reply_text("I could not read that image.")
        return

    index = len(session["photos"]) + 1
    local_path = DOWNLOAD_DIR / f"{chat_id}_{index}_{photo_file.file_unique_id}.jpg"

    try:
        await photo_file.download_to_drive(custom_path=str(local_path))
    except Exception:
        logger.exception("Photo download failed")
        await message.reply_text(
            "Photo download failed. Please try sending it again."
        )
        return

    session["photos"].append(str(local_path))
    session["captions"].append("")

    await message.reply_text(
        f"✅ Image {len(session['photos'])} received."
    )


async def finish_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session["photos"]:
        session["upload_mode"] = False
        await update.effective_message.reply_text(
            "No photos received yet. Press 📸 Upload Photos first."
        )
        return

    session["upload_mode"] = False
    session["caption_index"] = 0

    await update.effective_message.reply_text(
        f"✅ Upload finished. Total photos received: "
        f"{len(session['photos'])}\n\n"
        f"✍️ Enter caption for photo 1 of {len(session['photos'])}.\n"
        "Send /skipcaption if you don't want a caption."
    )


async def receive_caption(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    index = session["caption_index"]

    if index is None:
        return False

    if index >= len(session["photos"]):
        session["caption_index"] = None
        return False

    caption = update.effective_message.text.strip()
    session["captions"][index] = caption
    next_index = index + 1

    if next_index < len(session["photos"]):
        session["caption_index"] = next_index
        await update.effective_message.reply_text(
            f"✅ Caption saved for photo {index + 1}.\n\n"
            f"✍️ Enter caption for photo {next_index + 1} of "
            f"{len(session['photos'])}.\n"
            "Send /skipcaption to leave it blank."
        )
    else:
        session["caption_index"] = None
        await update.effective_message.reply_text(
            f"🎉 All {len(session['photos'])} captions are saved.\n\n"
            "Now press ⏱️ Set Interval, then 🚀 Start Posting.",
            reply_markup=MENU,
        )

    return True


async def skip_caption(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    index = session["caption_index"]

    if index is None:
        await update.effective_message.reply_text(
            "There is no caption waiting right now."
        )
        return

    session["captions"][index] = ""
    next_index = index + 1

    if next_index < len(session["photos"]):
        session["caption_index"] = next_index
        await update.effective_message.reply_text(
            f"Caption skipped for photo {index + 1}.\n"
            f"✍️ Enter caption for photo {next_index + 1} of "
            f"{len(session['photos'])}.\n"
            "Send /skipcaption to leave it blank."
        )
    else:
        session["caption_index"] = None
        await update.effective_message.reply_text(
            "🎉 All captions are complete. Set the interval and start posting.",
            reply_markup=MENU,
        )


async def set_interval(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    keyboard = [
        [
            InlineKeyboardButton("1 minute", callback_data="interval:60"),
            InlineKeyboardButton("2 minutes", callback_data="interval:120"),
        ],
        [
            InlineKeyboardButton("4 minutes", callback_data="interval:240"),
            InlineKeyboardButton("5 minutes", callback_data="interval:300"),
        ],
        [
            InlineKeyboardButton("10 minutes", callback_data="interval:600"),
        ],
    ]

    await update.effective_message.reply_text(
        "Choose the time between posts:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def interval_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    session = get_session(chat_id)

    try:
        seconds = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("Invalid interval.")
        return

    session["interval"] = seconds
    await query.edit_message_text(
        f"✅ Posting interval set to {seconds // 60} minute(s)."
    )


def upload_to_cloudinary(local_path):
    if not CLOUDINARY_CLOUD_NAME or not CLOUDINARY_UPLOAD_PRESET:
        raise RuntimeError(
            "Cloudinary settings are missing from your .env file."
        )

    url = (
        "https://api.cloudinary.com/v1_1/"
        f"{CLOUDINARY_CLOUD_NAME}/image/upload"
    )

    with open(local_path, "rb") as image_file:
        response = requests.post(
            url,
            data={"upload_preset": CLOUDINARY_UPLOAD_PRESET},
            files={"file": image_file},
            timeout=90,
        )

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Cloudinary returned an invalid response.")

    if response.status_code not in (200, 201) or "secure_url" not in data:
        raise RuntimeError(
            data.get("error", {}).get("message", "Cloudinary upload failed.")
        )

    return data["secure_url"]


def instagram_request(endpoint, payload):
    if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_USER_ID:
        raise RuntimeError(
            "Instagram API credentials are missing in .env."
        )

    response = requests.post(
        f"{INSTAGRAM_GRAPH_URL}/{endpoint}",
        data={
            **payload,
            "access_token": INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=90,
    )

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Instagram returned an invalid response.")

    if response.status_code >= 400 or "error" in result:
        error = result.get("error", {})
        raise RuntimeError(
            error.get("message", "Instagram API request failed.")
        )

    return result


def publish_instagram_photo(image_url, caption):
    # Create a media container, then publish it.
    container = instagram_request(
        f"{INSTAGRAM_USER_ID}/media",
        {
            "image_url": image_url,
            "caption": caption,
        },
    )

    creation_id = container.get("id")
    if not creation_id:
        raise RuntimeError("Instagram did not return a media container ID.")

    published = instagram_request(
        f"{INSTAGRAM_USER_ID}/media_publish",
        {"creation_id": creation_id},
    )

    if not published.get("id"):
        raise RuntimeError("Instagram did not confirm publication.")

    return published["id"]


async def post_one_photo(chat_id, index, context):
    session = get_session(chat_id)
    local_path = session["photos"][index]
    caption = session["captions"][index]

    image_url = await asyncio.to_thread(
        upload_to_cloudinary, local_path
    )

    media_id = await asyncio.to_thread(
        publish_instagram_photo, image_url, caption
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ Photo {index + 1}/{len(session['photos'])} "
            f"published successfully.\nMedia ID: {media_id}"
        ),
    )


async def posting_loop(chat_id, context):
    session = get_session(chat_id)

    try:
        while session["posting"]:
            if session["next_post"] >= len(session["photos"]):
                session["posting"] = False
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🎉 All photos have been processed.",
                    reply_markup=MENU,
                )
                break

            index = session["next_post"]

            try:
                await post_one_photo(chat_id, index, context)
            except Exception as exc:
                logger.exception("Instagram publication failed")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"❌ Photo {index + 1} failed.\n"
                        f"Reason: {str(exc)[:800]}\n\n"
                        "Posting stopped. Check your Instagram API "
                        "permissions, account type, token and Cloudinary settings."
                    ),
                    reply_markup=MENU,
                )
                session["posting"] = False
                break

            session["next_post"] += 1

            if session["posting"] and session["next_post"] < len(session["photos"]):
                await asyncio.sleep(session["interval"])

    finally:
        session["posting"] = False
        POSTING_TASKS.pop(chat_id, None)


async def start_posting(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if session["caption_index"] is not None:
        await update.effective_message.reply_text(
            "Finish the captions first, or use /skipcaption."
        )
        return

    if not session["photos"]:
        await update.effective_message.reply_text(
            "No photos found. Press 📸 Upload Photos first."
        )
        return

    if session["posting"]:
        await update.effective_message.reply_text(
            "Posting is already running."
        )
        return

    session["posting"] = True
    session["next_post"] = 0

    await update.effective_message.reply_text(
        f"🚀 Posting started. Total photos: {len(session['photos'])}.\n"
        f"Interval: {session['interval'] // 60} minute(s).",
        reply_markup=MENU,
    )

    task = asyncio.create_task(posting_loop(chat_id, context))
    POSTING_TASKS[chat_id] = task


async def stop_posting(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    session["posting"] = False

    task = POSTING_TASKS.get(chat_id)
    if task and not task.done():
        task.cancel()

    await update.effective_message.reply_text(
        "🛑 Posting stopped.",
        reply_markup=MENU,
    )


async def status_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    session = get_session(update.effective_chat.id)
    await update.effective_message.reply_text(
        f"Photos: {len(session['photos'])}\n"
        f"Captions pending: "
        f"{session['caption_index'] is not None}\n"
        f"Interval: {session['interval'] // 60} minute(s)\n"
        f"Posting: {'Yes' if session['posting'] else 'No'}"
    )


async def text_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    text = (message.text or "").strip()

    # Caption mode must be checked before menu buttons.
    if session["caption_index"] is not None:
        await receive_caption(update, context)
        return

    if text == "📸 Upload Photos":
        await upload_photos_button(update, context)
    elif text == "✅ Done Uploading":
        await finish_upload(update, context)
    elif text == "⏱️ Set Interval":
        await set_interval(update, context)
    elif text == "🚀 Start Posting":
        await start_posting(update, context)
    elif text == "🛑 Stop Posting":
        await stop_posting(update, context)
    elif text == "❓ Help":
        await help_command(update, context)
    else:
        await message.reply_text(
            "Choose a button from the menu, or use /help.",
            reply_markup=MENU,
        )


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
):
    logger.error(
        "Unhandled exception",
        exc_info=context.error,
    )


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN missing. Add it to your .env file."
        )

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("skipcaption", skip_caption))
    app.add_handler(CommandHandler("status", status_command))

    app.add_handler(CallbackQueryHandler(interval_callback))

    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            receive_photo,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )

    app.add_error_handler(error_handler)

    print("NEW bot.py is running. Press Ctrl+C to stop.")

    # Run only one bot instance at a time.
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
