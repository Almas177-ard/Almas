"""Clearly fictional catalog and delivery for isolated APP_DEMO=1 preview."""
PRODUCTS = [
    dict(slug="gemini-pro-monthly", productCode="GEM-PRO-30", name="Gemini Pro · 30 days", provider=dict(key="gemini", name="Gemini"), deliveryType="LINK", yourPrice="12.50", durationDays=30, stock=dict(inStock=True, count=48, maxQuantity=48), description="Instant activation link for Gemini Pro.", instructions="Open the link and sign in.", demoPrice=199000, sortOrder=1, bulkDiscount=dict(enabled=True, tiers=[dict(minQuantity=5, maxQuantity=9, unitPrice="11.50"), dict(minQuantity=10, maxQuantity=None, unitPrice="10.00")])),
    dict(slug="netflix-premium", productCode="NET-PRE-30", name="Netflix Premium · 30 days", provider=dict(key="netflix", name="Netflix"), deliveryType="READY_ACCOUNT", yourPrice="5.00", durationDays=30, stock=dict(inStock=True, count=24, maxQuantity=24), description="Ready-to-use account credentials.", instructions="Keep credentials private.", demoPrice=89000, sortOrder=2),
    dict(slug="canva-pro", productCode="CAN-PRO-30", name="Canva Pro · 30 days", provider=dict(key="canva", name="Canva"), deliveryType="LINK", yourPrice="2.80", durationDays=30, stock=dict(inStock=True, count=76, maxQuantity=76), description="Canva Pro invitation link.", instructions="Follow the invitation link.", demoPrice=49000, sortOrder=3),
    dict(slug="spotify-premium", productCode="SPO-PRE-30", name="Spotify Premium · 30 days", provider=dict(key="spotify", name="Spotify"), deliveryType="COUPON", yourPrice="3.20", durationDays=30, stock=dict(inStock=True, count=15, maxQuantity=15), description="Instant digital redemption code.", instructions="Redeem your code in your account.", demoPrice=59000, sortOrder=4),
    dict(slug="capcut-pro", productCode="CAP-PRO-30", name="CapCut Pro · 30 days", provider=dict(key="capcut", name="CapCut"), deliveryType="COUPON", yourPrice="6.50", durationDays=30, stock=dict(inStock=True, count=31, maxQuantity=31), description="Pro editing access coupon.", instructions="Redeem the code in your account.", demoPrice=109000, sortOrder=5),
    dict(slug="chatgpt-plus", productCode="GPT-PLUS-30", name="ChatGPT Plus · 30 days", provider=dict(key="chatgpt", name="ChatGPT"), deliveryType="LINK", yourPrice="19.00", durationDays=30, stock=dict(inStock=True, count=18, maxQuantity=18), description="Instant activation link.", instructions="Open the activation link to get started.", demoPrice=329000, sortOrder=6),
]

# Translated demo copy; brand/provider names are proper names and remain unchanged.
LOCAL_COPY = {
    "gemini-pro-monthly": {
        "uz": ("Gemini Pro · 30 kun", "Gemini Pro xizmatiga tezkor faollashtirish havolasi."),
        "ru": ("Gemini Pro · 30 дней", "Мгновенная ссылка для активации Gemini Pro."),
        "en": ("Gemini Pro · 30 days", "Instant activation link for Gemini Pro."),
    },
    "netflix-premium": {
        "uz": ("Netflix Premium · 30 kun", "Darhol foydalanish uchun tayyor akkaunt ma’lumotlari."),
        "ru": ("Netflix Premium · 30 дней", "Готовые данные для входа в аккаунт."),
        "en": ("Netflix Premium · 30 days", "Ready-to-use account credentials."),
    },
    "canva-pro": {
        "uz": ("Canva Pro · 30 kun", "Canva Pro obunasi uchun taklif havolasi."),
        "ru": ("Canva Pro · 30 дней", "Ссылка-приглашение в Canva Pro."),
        "en": ("Canva Pro · 30 days", "Canva Pro invitation link."),
    },
    "spotify-premium": {
        "uz": ("Spotify Premium · 30 kun", "Bir zumda yetkaziladigan faollashtirish kodi."),
        "ru": ("Spotify Premium · 30 дней", "Мгновенный цифровой код активации."),
        "en": ("Spotify Premium · 30 days", "Instant digital redemption code."),
    },
    "capcut-pro": {
        "uz": ("CapCut Pro · 30 kun", "Pro montaj imkoniyatlari uchun faollashtirish kodi."),
        "ru": ("CapCut Pro · 30 дней", "Код активации профессионального редактора."),
        "en": ("CapCut Pro · 30 days", "Pro editing access coupon."),
    },
    "chatgpt-plus": {
        "uz": ("ChatGPT Plus · 30 kun", "Obunani faollashtirish uchun tezkor havola."),
        "ru": ("ChatGPT Plus · 30 дней", "Ссылка для мгновенной активации подписки."),
        "en": ("ChatGPT Plus · 30 days", "Instant activation link."),
    },
}
