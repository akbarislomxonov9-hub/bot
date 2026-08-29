

import asyncio
import logging
import os
import re
import subprocess
import tempfile
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

# ------------------------------------------------------------------
# SOZLAMALAR
# ------------------------------------------------------------------

# .env faylidan BOT_TOKEN ni o'qiydi (pastdagi izohga qarang)
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi! Loyiha papkasida .env nomli fayl yarating "
        "va ichiga shu qatorni yozing:\n"
        'BOT_TOKEN=7123456789:AAHk3jXn9dQpL8mZ2vR7tYw...\n'
        "(qo'shtirnoqsiz, bo'sh joysiz)"
    )

# Watermark/username odatda shu burchaklarda bo'ladi.
# Kerakli variantni tanlang: "bottom_left", "bottom_right",
# "top_left", "top_right", yoki "none" (blur qilinmasin).
WATERMARK_POSITION = "bottom_left"

# Blur qilinadigan hudud o'lchami (video kengligi/balandligiga nisbatan foiz)
WATERMARK_WIDTH_RATIO = 0.35   # kenglikning 35%
WATERMARK_HEIGHT_RATIO = 0.12  # balandlikning 12%

INSTAGRAM_URL_RE = re.compile(
    r"(https?://)?(www\.)?instagram\.com/(reel|reels|p|tv)/[A-Za-z0-9_\-]+/?"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ------------------------------------------------------------------
# YORDAMCHI FUNKSIYALAR
# ------------------------------------------------------------------

def extract_instagram_url(text: str) -> str | None:
    match = INSTAGRAM_URL_RE.search(text)
    return match.group(0) if match else None


def download_instagram_video(url: str, out_dir: str) -> str:
    """yt-dlp yordamida Instagram videosini yuklab, fayl yo'lini qaytaradi."""
    out_template = os.path.join(out_dir, "%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_template,
        "format": "mp4/best",
        "quiet": True,
        "noplaylist": True,
        # Agar login talab qilinsa, cookies faylini shu yerga ko'rsating:
        # "cookiefile": "cookies.txt",
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
    return filepath


def get_video_resolution(filepath: str) -> tuple[int, int]:
    """ffprobe orqali video o'lchamini (width, height) olish."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        filepath,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    width_str, height_str = result.stdout.strip().split("x")
    return int(width_str), int(height_str)


def blur_watermark(input_path: str, output_path: str, position: str) -> None:
    """
    ffmpeg yordamida videoning belgilangan burchagini blur qiladi.
    Bu username/watermark ko'rinmasligi uchun ishlatiladi.
    """
    if position == "none":
        # Blur kerak bo'lmasa, faylni shunchaki nusxalaymiz
        subprocess.run(["cp", input_path, output_path], check=True)
        return

    width, height = get_video_resolution(input_path)
    box_w = int(width * WATERMARK_WIDTH_RATIO)
    box_h = int(height * WATERMARK_HEIGHT_RATIO)

    if position == "bottom_left":
        x, y = 0, height - box_h
    elif position == "bottom_right":
        x, y = width - box_w, height - box_h
    elif position == "top_left":
        x, y = 0, 0
    elif position == "top_right":
        x, y = width - box_w, 0
    else:
        raise ValueError(f"Noto'g'ri position: {position}")

    # boxblur filtri: faqat belgilangan hududni xiralashtiramiz.
    # Buning uchun videoni ikkiga bo'lib, blur qilingan qismni ustiga qo'yamiz.
    filter_complex = (
        f"[0:v]split=2[base][blur_src];"
        f"[blur_src]crop={box_w}:{box_h}:{x}:{y},boxblur=20:5[blurred];"
        f"[base][blurred]overlay={x}:{y}[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ------------------------------------------------------------------
# BOT HANDLERLARI
# ------------------------------------------------------------------

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Salom! 👋\n\n"
        "Menga Instagram post yoki reels linkini yuboring — "
        "men videoni yuklab, username/watermark ko'rinadigan "
        "burchagini xiralashtirib, tayyor holda qaytaraman.\n\n"
        "Masalan: https://www.instagram.com/reel/XXXXXXXXXXX/"
    )


@dp.message(F.text)
async def handle_message(message: Message) -> None:
    url = extract_instagram_url(message.text or "")
    if not url:
        await message.answer(
            "Iltimos, to'g'ri Instagram post yoki reels linkini yuboring."
        )
        return

    status_msg = await message.answer("⏳ Video yuklanmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            raw_path = download_instagram_video(url, tmp_dir)
        except Exception as e:
            logger.exception("Yuklab olishda xatolik")
            await status_msg.edit_text(
                f"❌ Videoni yuklab bo'lmadi. Sabab: {e}\n"
                "Video maxfiy akkauntdan bo'lishi yoki link noto'g'ri bo'lishi mumkin."
            )
            return

        await status_msg.edit_text("🎬 Watermark tozalanmoqda...")

        processed_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}_clean.mp4")
        try:
            blur_watermark(raw_path, processed_path, WATERMARK_POSITION)
        except Exception as e:
            logger.exception("Blur qilishda xatolik")
            # Blur muvaffaqiyatsiz bo'lsa, asl videoni yuboramiz
            processed_path = raw_path
            await status_msg.edit_text(
                "⚠️ Watermarkni tozalab bo'lmadi, asl video yuborilmoqda..."
            )

        await status_msg.edit_text("📤 Video yuborilmoqda...")

        video_file = FSInputFile(processed_path)
        await message.answer_video(video_file, caption="✅ Tayyor!")
        await status_msg.delete()


# ------------------------------------------------------------------
# ISHGA TUSHIRISH
# ------------------------------------------------------------------

async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
