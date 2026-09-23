# Activon

**Telegram Mini App + Python bot + MySQL + boshqaruv paneli.** Mijoz mahsulotlarni **faqat so‘mda (UZS)** sotib oladi. Karta to‘lovlari [Hamyon API](https://hamyon-api.uz/#api) orqali olinadi; tasdiqdan keyin mahsulot [ggsoma Partner API v1](https://ggsoma.store/api/partner/v1/health) orqali avtomatik yetkaziladi. LINK (havola), COUPON (kod) va READY_ACCOUNT (tayyor akkaunt) mavjud. Yetkazuvchining ichki hamyoni USD’da ishlaydi; mijoz va admin interfeysida narxlar/konvertatsiya **so‘mda** ko‘rsatiladi.

## Imkoniyatlar

- Botda **faqat `/start`** bor: Activon rasmi va Telegram WebApp tugmasini yuboradi. Katalog, hamyon, xarid va buyurtmalar WebApp ichida.
- To‘q ko‘k va och ko‘k rangli Soft UI, **tun/kun** rejimi, telefon va kompyuterga mos dizayn. Activon muqova rasmi, logotip, SVG ikonlar va mahsulot nomiga mos SVG illyustratsiyalar; admin o‘z PNG/JPEG/WebP rasmini ham yuklay oladi. Foydalanuvchi Telegram profil rasmini ishlatadi yoki profiliga o‘z rasmini yuklaydi.
- **O‘zbekcha, ruscha, inglizcha:** har bir tilda 361 ta tarjima kaliti. Bot/ilova matnlari hamda mahsulot nomi va tavsifi paneldan uch tilda tahrirlanadi. Yetkazuvchi nomlari — brend nomlari; jonli katalog tavsiflarini admin kerakli tilga tarjima qilishi mumkin.
- Admin panel: savdo va taxminiy foyda, katalog ko‘rinishi/narx/ustama/tartib/rasm/tarjima, buyurtma qayta yetkazish yoki hamyonga qaytarish, karta to‘lovlarini tekshirish, foydalanuvchilar/blok/rol/balans, matn muharriri, media, integratsiyalar, kurs, texnik xizmat va harakatlar jurnali.
- Mijoz hamyoni **UZS**: kirishi bilan balans ko‘rinadi; karta orqali hamyon to‘ldirish yoki mahsulot uchun to‘g‘ridan-to‘g‘ri to‘lash mumkin. Yetkazuvchining bot hamyoni — **alohida** balans, panelda so‘mga hisoblab ko‘rsatiladi.
- Telegram `initData` imzosi, HttpOnly sessiya va CSRF, admin huquqlari, shifrlangan API kalitlari va yetkazma ma’lumotlari, Hamyon callback imzosi **hamda** serverdan to‘lov holatini qayta tekshirish; idempotent Partner API buyurtmalari.

## Production: MySQL bilan ishga tushirish

MySQL 8+ da `utf8mb4` baza va alohida foydalanuvchi yarating (parolni **o‘zingiz** tanlang):

```sql
CREATE DATABASE activon CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'activon'@'localhost' IDENTIFIED BY 'YOUR_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON activon.* TO 'activon'@'localhost';
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# .env ichida DATABASE_URL, BOT_TOKEN, WEBAPP_URL (HTTPS),
# ADMIN_TELEGRAM_IDS, SESSION_SECRET, FIELD_ENCRYPTION_KEY ni kiriting.
.venv/bin/python main.py
```

Kutubxonalar tizimda bo‘lsa, oddiy `python main.py` yetarli. `main.py` **bitta jarayonda WebApp, Telegram polling bot va qayta urinish fon ishchisini** ishga tushiradi. SQLAlchemy dastlabki MySQL jadvallarini o‘zi yaratadi. Bot uchun bu jarayonni **bitta nusxada** ishlating; MySQL server alohida yuradi. Port `8000` oldiga HTTPS reverse proxy qo‘ying, `WEBAPP_URL=https://sizning-domeningiz/` ni yozing va zarur bo‘lsa BotFather’da WebApp domenini ro‘yxatdan o‘tkazing. `.env`, shifrlash kaliti va MySQL zaxira nusxalarini maxfiy saqlang. `create_all()` faqat yangi bazaga sxema yaratadi; keyingi sxema o‘zgarishlaridan oldin backup va tekshirilgan migratsiya qiling.

### Jonli integratsiyani yoqish

1. `ADMIN_TELEGRAM_IDS` ichidagi Telegram akkaunt bilan botga kiring. WebApp profilidan **Boshqaruv paneli** (`/admin`) ni oching. Boshqalarga admin API yopiq.
2. [@HamyonAPIBot](https://t.me/HamyonAPIBot) dan `shop_id` va `shop_key` oling. Hamyon do‘koni sozlamalarida **`prepare_url`** = `https://DOMEN/webhooks/hamyon/prepare` va **`complete_url`** = `https://DOMEN/webhooks/hamyon/complete` ni kiriting. Panelga shop ID/kalitni yozing. Callback uchun ommaviy **HTTPS** manzil zarur.
3. ggsoma Telegram botidan `sk_live_...` Partner API kalitini oling va Activon paneliga kiriting. Yetkazuvchi API manzili: **`https://ggsoma.store/api/partner/v1`** (Hamyon domeni emas). **Katalogni yangilash** va **Ulanishni tekshirish** tugmalarini bosing.
4. Amaldagi kursni (yetkazuvchining 1 ichki narx birligi uchun so‘m) va foyda ustamasini kiriting; istasangiz mahsulotga alohida so‘mdagi narx qo‘ying. Standart kurs — **namuna**, real birja kursi emas. Yetkazuvchining Telegram bot hamyonini alohida to‘ldiring.
5. Ikkala integratsiya va kichik real to‘lovni HTTPS callback bilan tekshiring. **Shundan keyingina** panelda xaridni yoqing: production avvaliga o‘chirilgan. Agar mahsulot butunlay yetkazilmasa, pul mijozning **Activon hamyoniga** qaytariladi; kartaga avtomatik qaytarilmaydi, chunki Hamyon hujjatida refund endpoint ko‘rsatilmagan.

> Bot tokeni, `shop_key` va API kalitlarini chat, frontend, Git yoki skrinshotga qo‘ymang. Bootstrap maxfiy ma’lumotlar `.env` da, hamkor/do‘kon kalitlari admin panel orqali serverda shifrlanadi. `FIELD_ENCRYPTION_KEY` ni eski qiymatni saqlamay almashtirsangiz, oldingi kalit va yetkazmalarni ochib bo‘lmaydi.

## To‘lovdan mahsulotgacha

1. Mijoz mahsulot va sonini tanlaydi. Activon yangi katalogdan zaxira, ulgurji tarif, narx, kurs va ustamani tekshiradi. Yakuniy summa **butun so‘m**.
2. **Karta**: `POST https://hamyon-api.uz/payment/create` ga URL-encoded form (`shop_id`, `shop_key`, `amount`, `order_id`) yuboriladi; karta raqami va 5 daqiqalik to‘lov oynasi olinadi. Hamyon bir vaqtning o‘zida bir xil summali ikkita ochiq to‘lovni qabul qilmaydi: Activon faqat kerak bo‘lsa summaga **1–100 so‘m** qo‘shadi va to‘lanadigan **aniq** summani mijozga ko‘rsatadi. **Hamyon**: lokal UZS balansidan atomar yechilib, mahsulot darhol so‘raladi.
3. Hamyon `prepare` va so‘ng `complete` (`paid` yoki `cancel`) callback yuboradi. Activon `md5(shop_id + payment_id + amount + shop_key)` imzosini, shop/invoice/summa mosligini **hamda** serverdan `GET /payment/status?payment_id=...` javobini tekshirmaguncha mahsulot bermaydi. Yo‘qolgan callback holati qayta tekshiriladi. Takroriy callback ikki marta balans oshirmaydi yoki mahsulot bermaydi.
4. Tasdiqlangan to‘lovdan keyin ggsoma `POST /orders` **barqaror `externalOrderId`** bilan chaqiriladi. Takrorlashda ID o‘zgarmaydi; Partner API idempotentligi qayta yechimning oldini oladi. Bitta yetkazma yoki ulgurji `lines[]` MySQL’da shifrlanib, faqat xaridorga ko‘rsatiladi; buyurtmalar ro‘yxati va logga xom akkaunt paroli yozilmaydi.
5. Vaqtinchalik xatoda oraliq bilan qayta urinish bo‘ladi. Admin qayta yetkazishi yoki mijozning Activon hamyoniga qaytarishi mumkin. Mahsulot zaxirasi tugagan kabi doimiy xatoda mablag‘ avtomatik **Activon hamyoniga** tushadi. Harakatlar jurnalida sirlar yo‘q.

Hamyonning ochiq hujjatida `sign` uchun kalitli MD5 va ixtiyoriy `GET /payment/status` ko‘rsatilgan. Activon webhook’ga **yolg‘iz** ishonmaydi. Provayder callback/status formatini o‘zgartirsa, jonli savdodan oldin `activon/commerce.py` va `activon/providers.py` ni moslashtiring.

## Lokal demo (haqiqiy to‘lov **yo‘q**)

```bash
APP_DEMO=1 .venv/bin/python main.py
# http://localhost:8000 va http://localhost:8000/admin
```

Demo SQLite fayl, namuna katalog va demo admin akkauntdan foydalanadi; karta o‘rnida faqat **sinov to‘lovini tasdiqlash** tugmasi chiqadi. U hech qachon kartadan pul yechmaydi, ggsoma’ga ulanmaydi; `BOT_TOKEN` + `WEBAPP_URL` berilmagan bo‘lsa, botni ham ishga tushirmaydi. Production (`APP_DEMO=0`, odatiy holat) uchun MySQL va Telegram majburiy, demo kirish **yo‘q**. Demo rejimini real do‘kon sifatida chiqarmang. Demo rasmlar `static/img/products/` ichida; boshqa katalog mahsulotlari uchun nomga bog‘liq SVG rasmi avtomatik tayyorlanadi va paneldan almashtiriladi.

## Tekshiruvlar

```bash
.venv/bin/python -m pytest -q
```

Testlar ulgurji narx, CSRF/rol izolatsiyasi, shifrlangan yetkazma, hamyon va karta, callback imzosi/statusi, dublikatlar, timeout tiklanishi, panel tahriri, idempotent qayta urinish va qaytarimni tekshiradi. Ular **demo SQLite + soxta tashqi xizmatlar** bilan ishlaydi, real kalitlarni talab qilmaydi. Jonli ishlatishdan oldin o‘z Hamyon do‘koningiz, ggsoma kalitingiz va HTTPS domeningiz bilan kichik to‘lovni sinang.

## Papkalar

| Fayl | Vazifasi |
|---|---|
| `main.py` | Web, bot va worker uchun bitta kirish nuqtasi |
| `activon/web.py` | Mijoz/public/admin HTTP API |
| `activon/auth.py` | Telegram `initData`, sessiya, CSRF |
| `activon/commerce.py` | Karta, hamyon, buyurtma va yetkazish holatlari |
| `activon/providers.py` | Hamyon va Partner API HTTP mijozlari |
| `activon/catalog.py` | Katalog, ustama, ulgurji narx |
| `activon/models.py`, `activon/db.py` | MySQL sxemasi, shifrlash, sozlamalar |
| `activon/bot.py` | Faqat `/start` va WebApp tugmasi |
| `static/` | WebApp, admin panel, rasmlar, SVG va tarjimalar |
| `tests/` | Izolyatsiyalangan integratsiya testlari |

**Ma’lumotlar:** `uploads/` va `.env` Git’da saqlanmaydi; `uploads/` ni MySQL bilan birga backup qiling. READY_ACCOUNT ma’lumotlarini logga yozmang va hamkor kalitini frontendga yubormang.
