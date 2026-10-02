from aiogram import Dispatcher

from bot.handlers import (
    ad_admin,
    ad_listing,
    business_agent,
    buyout,
    callbacks,
    car_admin,
    channel_comments,
    channel_watch,
    lead_admin,
    contest,
    listing_chat,
    listing_sale_followup,
    profile_alias,
    sales_agent,
    start,
    wishlist,
)


def register_handlers(dp: Dispatcher) -> None:
    # MUHIM: /start va deep-link (masalan lq_12) birinchi tekshirilsin — boshqa routerlarda
    # StateFilter(...) bilan umumiy fallback lar /start ni «oddiy matn» deb ushlab qolmasin.
    dp.include_router(start.router)
    # Admin (/cancel moderatsiya va h.k.) state ichidagi umumiy handlerlardan OLDIN ishlashi kerak.
    dp.include_router(ad_admin.router)
    # «Sotib olamiz»: admin narx FSM'i va sotuvchi javobi
    dp.include_router(buyout.router)
    # Admin reply → mijoz (relay) car_admin FSM'dan oldin; relay bo'lmasa SkipHandler bilan o'tkazib yuboradi
    dp.include_router(lead_admin.router)
    dp.include_router(car_admin.router)
    # Faqat channel_post / edited_channel_post — boshqa routerlar bilan to'qnashmaydi
    dp.include_router(channel_watch.router)
    # Telegram Business (shaxsiy akkaunt chatlari) — faqat business_* hodisalar
    dp.include_router(business_agent.router)
    # Kanal muhokama guruhi — faqat group/supergroup xabarlari
    dp.include_router(channel_comments.router)
    dp.include_router(listing_sale_followup.router)
    dp.include_router(listing_chat.router)
    dp.include_router(ad_listing.router)
    dp.include_router(wishlist.router)
    dp.include_router(contest.router)
    dp.include_router(profile_alias.router)
    dp.include_router(callbacks.router)
    # AI savdo agenti — ENG OXIRIDA: holatsiz (FSM yo'q) har qanday shaxsiy xabarni oladi
    dp.include_router(sales_agent.router)
