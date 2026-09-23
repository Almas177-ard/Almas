"""Only /start: one branded WebApp entry message. All shopping happens on the WebApp."""

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import select

from .config import config
from .db import get_setting, session
from .models import TextOverride, User

router = Router()
BOT_COPY = {
    "uz": ("Activon — raqamli imkoniyatlar bir joyda. Do‘konni ochib, mahsulotlarni kashf eting.", "WebApp’ni ochish"),
    "ru": ("Activon — цифровые возможности в одном месте. Откройте магазин и выберите продукт.", "Открыть WebApp"),
    "en": ("Activon — digital access in one place. Open the store and discover products.", "Open WebApp"),
}


@router.message(CommandStart())
async def start(message: Message):
    if message.chat.type != "private":
        return
    lang = (message.from_user.language_code or "uz")[:2]
    lang = lang if lang in BOT_COPY else "uz"
    with session() as db:
        saved = db.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if saved and saved.language in BOT_COPY:
            lang = saved.language
        caption, button = BOT_COPY[lang]
        hero_path = get_setting(db, "hero_image")
        for key, fallback in (("bot.start", caption), ("bot.open", button)):
            override = db.get(TextOverride, (key, lang))
            if key == "bot.start":
                caption = override.value if override else fallback
            else:
                button = override.value if override else fallback
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=button, web_app=WebAppInfo(url=config.webapp_url))]
    ])
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    hero = root / hero_path.lstrip("/")
    if not hero.is_file():
        hero = root / "static/img/activon-hero.png"
    await message.answer_photo(FSInputFile(hero), caption=caption, reply_markup=keyboard)


async def run_bot():
    bot = Bot(config.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()
