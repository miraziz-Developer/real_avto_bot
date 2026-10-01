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
            [InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="home_root")],
        ]
    )


def contest_hub_keyboard(*, show_join: bool) -> InlineKeyboardMarkup:
    _ = show_join  # signature saqlanadi, lekin endi ishlatilmaydi
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚧 Konkurs vaqtincha to‘xtatilgan", callback_data="home")],
            [InlineKeyboardButton(text="◀️ Menyu", callback_data="home")],
        ]
    )


def root_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Mashina tanlashda yordam", callback_data="agent_start")],
            [InlineKeyboardButton(text="📢 E’lon berish", callback_data="ad_start")],
            [InlineKeyboardButton(text="🔍 Qidiruv saqlash", callback_data="wishlist_start")],
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
