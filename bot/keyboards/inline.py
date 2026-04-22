from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def channel_keyboard(channel_username: str) -> InlineKeyboardMarkup:
    ch = channel_username.lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Kanalga obuna bo‘lish",
                    url=f"https://t.me/{ch}",
                )
            ],
            [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")],
        ]
    )


def instagram_keyboard(instagram_username: str) -> InlineKeyboardMarkup:
    u = instagram_username.lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📸 Instagramga o‘tish",
                    url=f"https://instagram.com/{u}",
                )
            ],
            [InlineKeyboardButton(text="✅ Instagramni ko‘rdim", callback_data="ig_ok")],
        ]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Do‘stlarni taklif qilish", callback_data="invite")],
            [InlineKeyboardButton(text="📊 Mening statistikam", callback_data="stats")],
            [InlineKeyboardButton(text="✏️ TOP uchun taxallus", callback_data="alias_start")],
            [InlineKeyboardButton(text="🏆 Musobaqa haqida", callback_data="about")],
        ]
    )


def alias_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="alias_cancel")],
            [InlineKeyboardButton(text="◀️ Bosh menyu", callback_data="home")],
        ]
    )


def back_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Bosh menyu", callback_data="home")]]
    )
