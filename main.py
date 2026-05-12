#!/usr/bin/env python3
import os
import re
import asyncio
import tempfile
from pathlib import Path

from openai import OpenAI
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
import yt_dlp

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

URL_RE = re.compile(r'https?://\S+')


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я Говорилка 🎙\n\n"
        "Отправь мне:\n"
        "• Видео или аудио файл (до 20 МБ)\n"
        "• Ссылку на YouTube, VK и другие сайты\n\n"
        "Получишь текст с таймкодами."
    )


def _format(response) -> str:
    lines = []
    for seg in response.segments:
        start = int(seg.start)
        lines.append(f"[{start // 60:02d}:{start % 60:02d}]  {seg.text.strip()}")
    return "\n".join(lines) if lines else "(речь не обнаружена)"


def _transcribe(file_path: Path) -> str:
    with open(file_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
    return _format(resp)


def _download_audio(url: str, out_dir: str) -> Path:
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(Path(out_dir) / "audio"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = list(Path(out_dir).glob("audio*"))
    if not files:
        raise RuntimeError("Файл не найден после скачивания")
    return files[0]


async def _send_result(message, text: str):
    if len(text) <= 4000:
        await message.reply_text(text)
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(text)
            tmp = f.name
        try:
            with open(tmp, "rb") as f:
                await message.reply_document(f, filename="транскрипция.txt")
        finally:
            os.unlink(tmp)


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    file_obj = msg.video or msg.audio or msg.voice or msg.document

    if not file_obj:
        await msg.reply_text("Отправь видео или аудио файл.")
        return

    await msg.reply_text("⏳ Получил, транскрибирую...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tg_file = await context.bot.get_file(file_obj.file_id)
            mime = getattr(file_obj, "mime_type", None) or "video/mp4"
            ext = "." + mime.split("/")[-1]
            file_path = Path(tmp_dir) / f"input{ext}"
            await tg_file.download_to_drive(file_path)

            text = await asyncio.to_thread(_transcribe, file_path)
            await _send_result(msg, text)
    except Exception as e:
        await msg.reply_text(f"❌ Ошибка: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text or ""
    match = URL_RE.search(raw)

    if not match:
        await update.message.reply_text("Отправь видеофайл или ссылку на YouTube/VK.")
        return

    url = match.group(0)
    await update.message.reply_text("⏳ Скачиваю видео...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = await asyncio.to_thread(_download_audio, url, tmp_dir)
            await update.message.reply_text("✅ Скачал, транскрибирую...")
            text = await asyncio.to_thread(_transcribe, audio_path)
            await _send_result(update.message, text)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL,
        handle_media,
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()


if __name__ == "__main__":
    main()
