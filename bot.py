"""
Ko'p funksiyali media bot (5 bo'lim)
======================================

BO'LIMLAR:
----------
1) 📥 Instagram   — post/reels linkidan video yuklab, username/watermarkni
                     yashiradi.
2) 🎬 TikTok       — TikTok linkidan video yuklab, watermarkni yashiradi.
3) 🎵 Musiqa       — qo'shiq nomi bo'yicha qidirib, audio faylni topib
                     yuboradi.
4) 🖼 Galereya video — foydalanuvchi telefondan/kompyuterdan yuborgan
                     videoning ham watermark/belgi hududini yashiradi
                     (link shart emas, faylning o'zini yuborsa bo'ladi).
5) ⚙️ Sozlamalar   — bildirishnoma va boshqa sozlamalar haqida ma'lumot.

O'RNATISH:
----------
1) Python 3.10+ kerak.
2) Kutubxonalarni o'rnating:
       pip install -r requirements.txt
3) ffmpeg o'rnatilgan bo'lishi kerak:
       Ubuntu/Debian: sudo apt install ffmpeg
       Windows: https://ffmpeg.org/download.html dan yuklab, PATH ga qo'shing
4) Loyiha papkasida .env fayl yarating:
       BOT_TOKEN=sizning_tokeningiz
5) Ishga tushirish:
       python bot.py

BILDIRISHNOMA HAQIDA:
----------------------
Har qanday media (video/audio) muvaffaqiyatli yuborilgandan
FOLLOWUP_DELAY_SECONDS soniya o'tgach (standart: 1 soat), foydalanuvchiga
do'stona eslatma xabari yuboriladi. Xabar matni har safar tasodifiy
tanlanadi — lekin BARCHASI ijobiy va do'stona ohangda, foydalanuvchini
hissiy bosim yoki "asabiylashtirish" bilan majburlashga urinmaydi, chunki
bu yomon foydalanuvchi tajribasi va axloqiy jihatdan noto'g'ri hisoblanadi.

MUALLIFLIK HUQUQI HAQIDA ESLATMA:
-----------------------------------
Ushbu bot boshqa mualliflarning video va musiqa kontentiga kirish imkonini
beradi. Iltimos, botni faqat shaxsiy/ta'lim maqsadida, yoki o'zingiz
foydalanish huquqiga ega bo'lgan kontent uchun ishlating. Musiqa bo'limi
faqat bitta eng mos natijani topib beradi (ommaviy tarqatish xizmati
emas) — original ijrochiga hurmat va litsenziyalarga rioya qiling.
"""

import asyncio
import logging
import os
import random
import re
import subprocess
import tempfile
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

# ------------------------------------------------------------------
# SOZLAMALAR
# ------------------------------------------------------------------

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi! Loyiha papkasida .env nomli fayl yarating "
        "va ichiga shu qatorni yozing:\n"
        'BOT_TOKEN=7123456789:AAHk3jXn9dQpL8mZ2vR7tYw...\n'
        "(qo'shtirnoqsiz, bo'sh joysiz)"
    )

WATERMARK_POSITION = "bottom_left"   # bottom_left / bottom_right / top_left / top_right / none
WATERMARK_MODE = "box"               # "box" (kafolatlangan) yoki "blur"
WATERMARK_WIDTH_RATIO = 0.40
WATERMARK_HEIGHT_RATIO = 0.14

# Media yuborilgandan necha soniyadan keyin eslatma yuborilsin
FOLLOWUP_DELAY_SECONDS = 60 * 60  # 1 soat

FOLLOWUP_MESSAGES = [
    "🙂 Salom! Yana biror video yoki qo'shiq kerak bo'lsa, shu yerdaman.",
    "🎧 Yangi musiqa kerakmi? Nomini yuboring, topib beraman.",
    "📥 Yana Instagram yoki TikTok video yuklab olmoqchimisiz?",
    "👋 Ishlaringiz yaxshimi? Kerak bo'lsa yana buyurtma bering.",
]

INSTAGRAM_URL_RE = re.compile(
    r"(https?://)?(www\.)?instagram\.com/(reel|reels|p|tv)/[A-Za-z0-9_\-]+/?"
)
TIKTOK_URL_RE = re.compile(
    r"(https?://)?(www\.|vm\.|vt\.)?tiktok\.com/\S+"
)

MENU_INSTAGRAM = "📥 Instagram"
MENU_TIKTOK = "🎬 TikTok"
MENU_MUSIC = "🎵 Musiqa"
MENU_GALLERY = "🖼 Galereya video"
MENU_SETTINGS = "⚙️ Sozlamalar"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Har bir foydalanuvchi hozir qaysi bo'limda turganini saqlaymiz (oddiy holat).
# chat_id -> "instagram" | "tiktok" | "music" | "gallery" | None
user_mode: dict[int, str | None] = {}


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_INSTAGRAM), KeyboardButton(text=MENU_TIKTOK)],
            [KeyboardButton(text=MENU_MUSIC), KeyboardButton(text=MENU_GALLERY)],
            [KeyboardButton(text=MENU_SETTINGS)],
        ],
        resize_keyboard=True,
    )


# ------------------------------------------------------------------
# YORDAMCHI FUNKSIYALAR — VIDEO
# ------------------------------------------------------------------

def extract_url(text: str, pattern: re.Pattern) -> str | None:
    match = pattern.search(text)
    return match.group(0) if match else None


def download_video(url: str, out_dir: str) -> str:
    """yt-dlp yordamida (Instagram/TikTok) videoni yuklab, fayl yo'lini qaytaradi."""
    out_template = os.path.join(out_dir, "%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_template,
        "format": "mp4/best",
        "quiet": True,
        "noplaylist": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def get_video_resolution(filepath: str) -> tuple[int, int]:
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


def remove_watermark(input_path: str, output_path: str, position: str, mode: str) -> None:
    """Videoning belgilangan burchagidagi username/watermarkni yo'q qiladi."""
    if position == "none":
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

    if mode == "box":
        vf = f"drawbox=x={x}:y={y}:w={box_w}:h={box_h}:color=black@1.0:t=fill"
        cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-c:a", "copy", output_path]
    elif mode == "blur":
        filter_complex = (
            f"[0:v]split=2[base][blur_src];"
            f"[blur_src]crop={box_w}:{box_h}:{x}:{y},"
            f"boxblur=40:10:enable=1,boxblur=40:10:enable=1[blurred];"
            f"[base][blurred]overlay={x}:{y}[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "0:a?", "-c:a", "copy",
            output_path,
        ]
    else:
        raise ValueError(f"Noto'g'ri mode: {mode}")

    subprocess.run(cmd, check=True, capture_output=True)


# ------------------------------------------------------------------
# YORDAMCHI FUNKSIYALAR — MUSIQA
# ------------------------------------------------------------------

def search_and_download_music(query: str, out_dir: str) -> tuple[str, str]:
    """
    Berilgan qo'shiq nomi bo'yicha eng mos natijani qidirib, audio (mp3)
    ko'rinishida yuklab oladi. (title, filepath) qaytaradi.

    Eslatma: faqat BITTA eng mos natija yuklanadi — bu ommaviy musiqa
    arxivi emas, oddiy shaxsiy qidiruv vositasi.
    """
    out_template = os.path.join(out_dir, f"{uuid.uuid4().hex}.%(ext)s")
    ydl_opts = {
        "outtmpl": out_template,
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
        "default_search": "ytsearch1",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=True)
        if "entries" in info:
            info = info["entries"][0]
        title = info.get("title", query)
        filepath = ydl.prepare_filename(info)
        # Postprocessor mp3'ga o'zgartirgani uchun kengaytmani tuzatamiz
        filepath = os.path.splitext(filepath)[0] + ".mp3"
    return title, filepath


# ------------------------------------------------------------------
# BILDIRISHNOMA
# ------------------------------------------------------------------

async def send_followup_reminder(chat_id: int) -> None:
    """Media yuborilgandan FOLLOWUP_DELAY_SECONDS o'tgach do'stona eslatma yuboradi."""
    await asyncio.sleep(FOLLOWUP_DELAY_SECONDS)
    try:
        text = random.choice(FOLLOWUP_MESSAGES)
        await bot.send_message(chat_id, text)
    except Exception:
        logger.exception("Follow-up eslatma yuborishda xatolik")


def schedule_followup(chat_id: int) -> None:
    asyncio.create_task(send_followup_reminder(chat_id))


# ------------------------------------------------------------------
# ASOSIY HANDLERLAR
# ------------------------------------------------------------------

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_mode[message.chat.id] = None
    await message.answer(
        "Salom! 👋 Men ko'p funksiyali media botman.\n\n"
        "Quyidagi bo'limlardan birini tanlang:\n"
        f"{MENU_INSTAGRAM} — post/reels video yuklab, watermarkni tozalayman\n"
        f"{MENU_TIKTOK} — TikTok video yuklab, watermarkni tozalayman\n"
        f"{MENU_MUSIC} — qo'shiq nomini yozing, topib beraman\n"
        f"{MENU_GALLERY} — o'zingiz yuborgan videodagi belgini tozalayman\n"
        f"{MENU_SETTINGS} — sozlamalar haqida ma'lumot",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(F.text == MENU_INSTAGRAM)
async def menu_instagram(message: Message) -> None:
    user_mode[message.chat.id] = "instagram"
    await message.answer("📥 Instagram post yoki reels linkini yuboring.")


@dp.message(F.text == MENU_TIKTOK)
async def menu_tiktok(message: Message) -> None:
    user_mode[message.chat.id] = "tiktok"
    await message.answer("🎬 TikTok video linkini yuboring.")


@dp.message(F.text == MENU_MUSIC)
async def menu_music(message: Message) -> None:
    user_mode[message.chat.id] = "music"
    await message.answer("🎵 Qo'shiq nomini (va ijrochisini) yozing, masalan: 'Ummon guruhi - Ohangim'.")


@dp.message(F.text == MENU_GALLERY)
async def menu_gallery(message: Message) -> None:
    user_mode[message.chat.id] = "gallery"
    await message.answer("🖼 Endi telefon/kompyuteringizdagi videoni shu yerga yuboring (fayl sifatida).")


@dp.message(F.text == MENU_SETTINGS)
async def menu_settings(message: Message) -> None:
    hours = FOLLOWUP_DELAY_SECONDS // 3600
    await message.answer(
        "⚙️ Sozlamalar\n\n"
        f"• Watermark yashirish usuli: {WATERMARK_MODE}\n"
        f"• Watermark joylashuvi: {WATERMARK_POSITION}\n"
        f"• Eslatma xabari: media yuborilgandan {hours} soat o'tgach\n\n"
        "Bu qiymatlarni bot.py faylidagi sozlamalar qismidan o'zgartirishingiz mumkin."
    )


# ---- Instagram / TikTok link qayta ishlash (umumiy funksiya) ----

async def process_video_link(message: Message, url: str, platform_name: str) -> None:
    await message.answer("✅ Video qabul qilindi! Ishlov berilmoqda...")
    status_msg = await message.answer("⏳ Video yuklanmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            raw_path = await asyncio.to_thread(download_video, url, tmp_dir)
        except Exception as e:
            logger.exception("Yuklab olishda xatolik")
            await status_msg.edit_text(
                f"❌ Videoni yuklab bo'lmadi. Sabab: {e}\n"
                "Video maxfiy akkauntdan bo'lishi yoki link noto'g'ri bo'lishi mumkin."
            )
            return

        await status_msg.edit_text("🎬 Username/watermark tozalanmoqda...")
        processed_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}_clean.mp4")
        try:
            await asyncio.to_thread(
                remove_watermark, raw_path, processed_path, WATERMARK_POSITION, WATERMARK_MODE
            )
        except Exception:
            logger.exception("Watermarkni tozalashda xatolik")
            processed_path = raw_path
            await status_msg.edit_text("⚠️ Watermarkni tozalab bo'lmadi, asl video yuborilmoqda...")

        await status_msg.edit_text("📤 Video yuborilmoqda...")
        await message.answer_video(FSInputFile(processed_path), caption=f"✅ Tayyor! ({platform_name})")
        await status_msg.delete()

    schedule_followup(message.chat.id)


# ---- Galereyadan yuborilgan video ----

@dp.message(F.video)
async def handle_uploaded_video(message: Message) -> None:
    await message.answer("✅ Video qabul qilindi! Ishlov berilmoqda...")
    status_msg = await message.answer("⏳ Video yuklab olinmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}.mp4")
        try:
            file_info = await bot.get_file(message.video.file_id)
            await bot.download_file(file_info.file_path, destination=raw_path)
        except Exception as e:
            logger.exception("Videoni yuklab olishda xatolik")
            await status_msg.edit_text(f"❌ Videoni yuklab bo'lmadi: {e}")
            return

        await status_msg.edit_text("🎬 Watermark/belgi tozalanmoqda...")
        processed_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}_clean.mp4")
        try:
            await asyncio.to_thread(
                remove_watermark, raw_path, processed_path, WATERMARK_POSITION, WATERMARK_MODE
            )
        except Exception:
            logger.exception("Watermarkni tozalashda xatolik")
            processed_path = raw_path
            await status_msg.edit_text("⚠️ Tozalab bo'lmadi, asl video yuborilmoqda...")

        await status_msg.edit_text("📤 Video yuborilmoqda...")
        await message.answer_video(FSInputFile(processed_path), caption="✅ Tayyor!")
        await status_msg.delete()

    schedule_followup(message.chat.id)


# ---- Musiqa qidirish ----

async def process_music_query(message: Message, query: str) -> None:
    await message.answer("✅ So'rov qabul qilindi! Qidirilmoqda...")
    status_msg = await message.answer("🔎 Qidirilmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            title, filepath = await asyncio.to_thread(search_and_download_music, query, tmp_dir)
        except Exception as e:
            logger.exception("Musiqa qidirishda xatolik")
            await status_msg.edit_text("❌ Topilmadi yoki yuklab bo'lmadi. Sabab: ")
            return

        await status_msg.edit_text("📤 Yuborilmoqda...")
        await message.answer_audio(FSInputFile(filepath), title=title, caption=f"🎵 {title}")
        await status_msg.delete()

    schedule_followup(message.chat.id)


# ---- Matnli xabarlarni yo'naltirish (link yoki musiqa so'rovi) ----

@dp.message(F.text)
async def handle_text(message: Message) -> None:
    text = message.text or ""
    chat_id = message.chat.id
    mode = user_mode.get(chat_id)

    ig_url = extract_url(text, INSTAGRAM_URL_RE)
    tt_url = extract_url(text, TIKTOK_URL_RE)

    # Link avtomatik aniqlansa, bo'lim tanlanmagan bo'lsa ham ishlaydi
    if ig_url:
        await process_video_link(message, ig_url, "Instagram")
        return
    if tt_url:
        await process_video_link(message, tt_url, "TikTok")
        return

    if mode == "music":
        await process_music_query(message, text)
        return

    if mode == "gallery":
        await message.answer("🖼 Iltimos, videoni matn emas, fayl sifatida yuboring.")
        return

    await message.answer(
        "Tushunmadim 🙂 Pastdagi menyudan bo'lim tanlang yoki to'g'ridan-to'g'ri "
        "Instagram/TikTok link yuboring.",
        reply_markup=main_menu_keyboard(),
    )


# ------------------------------------------------------------------
# ISHGA TUSHIRISH
# ------------------------------------------------------------------

async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())