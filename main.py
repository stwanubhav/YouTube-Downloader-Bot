import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import asyncio

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "your_bot_token"

class SimpleProgressHook:
    def __init__(self, message_func, download_type):
        self.message_func = message_func
        self.download_type = download_type
    
    def hook(self, d):
        if d['status'] == 'downloading':
            if 'total_bytes' in d and d['total_bytes']:
                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
                asyncio.create_task(self.message_func(f"📥 Downloading {self.download_type}: {percent:.1f}%"))
        elif d['status'] == 'finished':
            asyncio.create_task(self.message_func("✅ Download completed! 📤 Uploading..."))


async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, download_type: str, format_id: str | None = None):
    """
    download_type: 'audio' or 'video'
    format_id: for video quality (yt-dlp format_id). If None, uses default best[height<=480]
    """
    query = update.callback_query
    await query.answer()
    
    youtube_url = context.user_data.get('youtube_url')
    chat_id = query.message.chat_id
    
    if not youtube_url:
        await query.edit_message_text("No YouTube URL found.")
        return
    
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    
    # Edit message to show download started
    status_message = await query.edit_message_text(f"🔄 Starting {download_type} download...")
    
    async def update_status(text):
        await context.bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=status_message.message_id
        )
    
    try:
        if download_type == 'audio':
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': 'downloads/%(title)s.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'progress_hooks': [SimpleProgressHook(update_status, "audio").hook],
            }
        else:
            # If a specific format_id is provided, use it; otherwise fall back to best<=480p
            video_format = format_id if format_id else 'best[height<=480]'
            ydl_opts = {
                'format': video_format,
                'outtmpl': 'downloads/%(title)s.%(ext)s',
                'progress_hooks': [SimpleProgressHook(update_status, "video").hook],
            }
        
        # Download file
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            file_path = ydl.prepare_filename(info)
            if download_type == 'audio':
                file_path = os.path.splitext(file_path)[0] + '.mp3'
        
        # Send file
        file_size = os.path.getsize(file_path) / (1024 * 1024)
        await update_status(f"📤 Uploading... Size: {file_size:.1f}MB")
        
        if download_type == 'audio':
            with open(file_path, 'rb') as f:
                await context.bot.send_audio(chat_id=chat_id, audio=f, title=info.get('title', 'Audio'))
        else:
            with open(file_path, 'rb') as f:
                await context.bot.send_video(chat_id=chat_id, video=f, caption=info.get('title', 'Video'))
        
        await update_status("✅ Download and upload completed!")
        
        # Cleanup
        os.remove(file_path)
        
    except Exception as e:
        logger.error(f"Download error: {e}")
        await update_status("❌ Download failed. Please try again.")


async def show_video_quality_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """After user clicks 'video', show available mp4 qualities as inline buttons."""
    query = update.callback_query
    await query.answer()

    youtube_url = context.user_data.get('youtube_url')
    if not youtube_url:
        await query.edit_message_text("No YouTube URL found.")
        return

    try:
        # Get info & formats without downloading
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)

        formats = info.get('formats', [])

        # Filter for mp4 with both audio and video (progressive downloads)
        mp4_formats = []
        for f in formats:
            if (
                f.get('ext') == 'mp4'
                and f.get('vcodec') != 'none'
                and f.get('acodec') != 'none'
                and f.get('height') is not None
            ):
                mp4_formats.append(f)

        if not mp4_formats:
            # Fallback: no proper mp4 formats found
            await query.edit_message_text(
                "Couldn't find separate MP4 qualities. Downloading default 480p (or best available)..."
            )
            # Use old behavior
            await download_and_send(update, context, 'video', format_id=None)
            return

        # Sort by resolution (height) descending
        mp4_formats.sort(key=lambda x: x.get('height', 0), reverse=True)

        # Keep only one entry per unique height (best quality for that height)
        unique_height_formats = {}
        for f in mp4_formats:
            h = f.get('height')
            if h not in unique_height_formats:
                unique_height_formats[h] = f

        # Build inline keyboard
        buttons = []
        row = []
        for height, f in unique_height_formats.items():
            size_bytes = f.get('filesize') or f.get('filesize_approx')
            if size_bytes:
                size_mb = size_bytes / (1024 * 1024)
                label = f"{height}p (~{size_mb:.1f}MB)"
            else:
                label = f"{height}p"

            callback_data = f"video_format_{f.get('format_id')}"
            row.append(InlineKeyboardButton(label, callback_data=callback_data))

            # 2 buttons per row
            if len(row) == 2:
                buttons.append(row)
                row = []

        if row:
            buttons.append(row)

        # Add a "default" / "auto" option
        buttons.append(
            [InlineKeyboardButton("🤖 Auto (best ≤ 480p)", callback_data="video_auto_default")]
        )

        reply_markup = InlineKeyboardMarkup(buttons)

        await query.edit_message_text(
            "🎬 Choose video quality (MP4):",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error while fetching video qualities: {e}")
        await query.edit_message_text(
            "❌ Failed to get video qualities. Downloading default 480p (or best available)..."
        )
        await download_and_send(update, context, 'video', format_id=None)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # AUDIO
    if data == 'audio':
        await download_and_send(update, context, 'audio')

    # USER CHOSE 'VIDEO' → SHOW QUALITY OPTIONS
    elif data == 'video':
        await show_video_quality_options(update, context)

    # USER CHOSE A SPECIFIC VIDEO FORMAT (QUALITY)
    elif data.startswith('video_format_'):
        format_id = data.replace('video_format_', '', 1)
        await download_and_send(update, context, 'video', format_id=format_id)

    # USER CHOSE AUTO DEFAULT VIDEO
    elif data == 'video_auto_default':
        await download_and_send(update, context, 'video', format_id=None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Welcome to YouTube Downloader Bot!\n\n"
        "Send me a YouTube link and I'll download it for you!"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    
    if 'youtube.com' in message_text or 'youtu.be' in message_text:
        context.user_data['youtube_url'] = message_text
        
        keyboard = [
            [
                InlineKeyboardButton("🎵 Download Audio (MP3)", callback_data="audio"),
                InlineKeyboardButton("🎬 Download Video (MP4)", callback_data="video")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("Choose download type:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Please send a valid YouTube URL")


def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot is running...")
    application.run_polling()


if __name__ == '__main__':
    main()
