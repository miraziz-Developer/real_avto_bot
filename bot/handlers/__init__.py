from aiogram import Dispatcher

from bot.handlers import (
    ad_admin,
    ad_listing,
    ai_chat,
    callbacks,
    contest,
    listing_chat,
    listing_sale_followup,
    profile_alias,
    start,
    wishlist,
)


def register_handlers(dp: Dispatcher) -> None:
    # MUHIM: /start va deep-link (masalan lq_12) birinchi tekshirilsin — boshqa routerlarda
    # StateFilter(...) bilan umumiy fallback lar /start ni «oddiy matn» deb ushlab qolmasin.
    dp.include_router(start.router)
    # Admin (/cancel moderatsiya va h.k.) state ichidagi umumiy handlerlardan OLDIN ishlashi kerak.
    dp.include_router(ad_admin.router)
    dp.include_router(listing_sale_followup.router)
    dp.include_router(listing_chat.router)
    dp.include_router(ad_listing.router)
    dp.include_router(wishlist.router)
    dp.include_router(contest.router)
    dp.include_router(profile_alias.router)
    dp.include_router(callbacks.router)
    # AI maslahatchi oxirida: holatsiz oddiy matnni (boshqa oqimlar ushlamagan) AI ga yo‘naltiradi.
    dp.include_router(ai_chat.router)
