# Real Avto × AutoAI CRM — birlashgan loyiha rejasi

## Nima uchun birlashtiramiz

Real Avto'da allaqachon ishlab turgan narsalar bor:

| Mavjud (repo'da) | AutoAI CRM g'oyasidagi o'rni |
|---|---|
| Telegram bot: e'lon berish, admin moderatsiya, kanalga post | **Cars katalogi** — tasdiqlangan e'lonlar AI uchun tayyor baza |
| Wishlist (saqlangan qidiruv) + mos e'lon chiqqanda xabar | Mijozni ushlab qolish (retention) |
| Anonim savol-javob, «Sotildi/Sotilmadi» kuzatuvi, sharhlar kanali | Ishonch va shaffoflik |
| Node.js CRM + React panel, PostgreSQL, Redis, Docker | Admin panel va infratuzilma |

Yetishmayotgani esa aynan AutoAI CRM g'oyasining yuragi edi: **har bir savolga soniyalar ichida javob berish
va pul bilan kelgan «issiq» mijozni ajratib olish**. Shu bosqichda qo'shildi.

## Raqobatchilardan qanday o'zamiz

Avtoelon, Uzum Avto, OLX va katta Instagram sahifalarida e'lon ko'p, lekin:

1. **Javob sekin.** Xaridor «Narxi qancha?» deb yozadi, soatlab javob kutadi va boshqa sahifaga o'tib ketadi.
   → Bizda AI 24/7, 5 soniyada javob beradi va mos variantlarni o'zi topib ko'rsatadi.
2. **Ishonch past.** Sotilgan mashina e'loni osilib turadi, soxta narxlar ko'p.
   → Bizda har bir e'lon moderatsiyadan o'tadi, sotilgani kanalda «SOTILDI» bo'ladi, sharhlar alohida kanalda.
3. **Mijoz eslab qolinmaydi.** Bugun mashina topolmagan xaridor ertaga raqobatchidan oladi.
   → Wishlist + AI suhbat tarixi + lead: mashina kelishi bilan xaridorga birinchi bo'lib xabar beramiz.
4. **Bitta oyna yo'q.** Menejer Instagram, Telegram, telefon orasida adashadi.
   → Barcha kanallardagi issiq mijozlar CRM'dagi bitta «Issiq mijozlar» ro'yxatida, holati bilan.

Asosiy o'lchov (KPI): **javob vaqti**, **kunlik issiq leadlar soni**, **lead → savdo konversiyasi**,
**kanal/Instagram faol obunachilar**. CRM bosh sahifasida birinchi to'rtta kartochka aynan shular.

## Bosqichlar

### ✅ 1-bosqich — AI maslahatchi + issiq mijozlar (shu PR)

- Botda «🤖 Mashina tanlashda yordam (AI)» tugmasi va `/ai` buyrug'i. Menyudan tashqarida yozilgan har qanday
  oddiy matn ham javobsiz qolmaydi — AI ulanadi.
- AI (OpenAI `gpt-4o-mini` yoki Gemini) function calling orqali **faqat bazadagi sotuvdagi e'lonlardan** qidiradi,
  mashina va narxni to'qimaydi, kanal havolasini beradi.
- Xaridor tayyor bo'lsa, AI raqam so'raydi (yoki «📱 Raqamimni yuborish» tugmasi) → `leads` jadvali →
  adminlarga Telegramda: «🔥 Yangi issiq mijoz! Ism, tel, mashina, to'lov turi, izoh».
- CRM: «🔥 Issiq mijozlar» (holat: yangi → bog'lanildi → sotildi / yo'qotildi) va «🤖 AI suhbatlar» (to'liq yozishma).
- Instagram uchun tayyor webhook: `POST /webhooks/inbound` (Make.com / ManyChat).

### 2-bosqich — Instagram'ni ulash (1–2 hafta)

1. `.env` da `INBOUND_WEBHOOK_SECRET` ni to'ldiring, server 8080-portini oching.
2. Make.com (yoki ManyChat) ssenariysi:
   - **Trigger:** Instagram for Business → *Watch direct messages* (va/yoki *Watch comments*).
   - **HTTP → Make a request:** `POST http://SERVER_IP:8080/webhooks/inbound`,
     header `X-Webhook-Secret: <secret>`, JSON body:
     ```json
     {"platform": "instagram", "user_id": "{{sender.id}}", "username": "{{sender.username}}", "text": "{{message.text}}"}
     ```
   - **Instagram → Send a message:** matn = javobdagi `reply`.
3. Kommentga javob: kommentga qisqa «Direct'ga yozdik ✅» va Direct'ga to'liq javob (Instagram qoidasiga mos).
4. Mijozlar ko'paygach — Meta App Review'dan o'tib to'g'ridan-to'g'ri Instagram Graph API'ga (webhook endpoint o'sha).

### 3-bosqich — Avto-posting va kontent (2–3 hafta)

- E'lon tasdiqlanganda: rasmga brend shablon (logo, narx, yil) qo'yish, AI chiroyli matn yozadi,
  Telegram kanal + Instagram (post/stories) ga avtomatik joylash. Kanal faolligi va qamrov o'sadi.
- Haftalik «TOP arzon mashinalar» avto-posti (dadangiz sahifasi nomiga mos: *real_avto_arzon*).
- Narx tahlili: bazadagi o'xshash e'lonlar bo'yicha «bozor narxi» — sotuvchiga ham, xaridorga ham foydali.

### 4-bosqich — Kengayish va B2B SaaS (AutoAI CRM)

- Multi-tenant: `dealerships` jadvali, har bir salon/bloger o'z boti, o'z Instagrami, o'z katalogi bilan.
  Hozirgi kod bitta tenant (Real Avto) uchun — keyin `dealership_id` ustuni qo'shiladi.
- Oylik obuna ($50–150): boshqa salonlar va avto-blogerlar. Real Avto — birinchi va eng yaxshi keys (case study).
- Telegram Mini App: salon egasi telefondan mashina qo'shadi va leadlarni ko'radi.

## Ishga tushirish (1-bosqich)

```bash
# .env ga qo'shing:
AI_API_KEY=sk-...                  # OpenAI kaliti (yoki Gemini + AI_BASE_URL)
ADMIN_TELEGRAM_IDS=123456789       # issiq mijoz xabari shu ID larga keladi
# ixtiyoriy: AI_BUSINESS_INFO, LEADS_CHAT_ID, INBOUND_WEBHOOK_SECRET

docker compose build bot backend frontend && docker compose up -d
```

`AI_API_KEY` bo'sh bo'lsa bot avvalgidek ishlaydi — yangi funksiyalar shunchaki yashirin turadi.

**Xarajat taxmini:** `gpt-4o-mini` bilan bitta suhbat (5–8 xabar) ≈ $0.001–0.003. Kuniga 300 ta suhbat —
oyiga taxminan $10–25. `AI_DAILY_LIMIT` bitta mijozning kunlik limitini cheklaydi.
