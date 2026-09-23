"""Isolated demo integration tests: no live money, Telegram calls or upstream keys."""
import asyncio
import hashlib
import json
import os
from datetime import timedelta

os.environ["APP_DEMO"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///./activon-test.sqlite"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from activon.auth import SESSION_COOKIE, serializer
from activon.commerce import fulfill_order
from activon.db import Base, engine, encrypt, session, set_setting
from activon.models import CatalogItem, Order, Payment, Setting, User, WalletEntry, now
from activon.providers import HamyonClient, ServiceError
from activon.web import app


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)


def login(c):
    assert c.post("/api/demo/login").status_code == 200
    return {"X-CSRF-Token": c.get("/api/me").json()["csrfToken"]}


def test_catalog_and_language_preview(client):
    c = client
    assert c.get("/api/health").json() == {"ok": True, "service": "activon", "demo": True}
    config = c.get("/api/public/config").json()
    assert config["checkoutEnabled"] and config["demo"]
    products = c.get("/api/catalog").json()["data"]
    assert len(products) == 6 and {p["deliveryType"] for p in products} == {"LINK", "COUPON", "READY_ACCOUNT"}
    assert all(p["currency"] == "UZS" and p["unitSom"] > 0 for p in products)
    assert c.get("/api/art/some-digital-product.svg").headers["content-type"].startswith("image/svg+xml")
    assert c.get("/api/art/../x.svg").status_code == 404
    with session() as db:
        item = db.scalar(select(CatalogItem).where(CatalogItem.slug == "gemini-pro-monthly"))
        item.custom_price_som = None
    assert c.get("/api/catalog/gemini-pro-monthly?quantity=5").json()["unitSom"] == 184000
    assert c.get("/api/catalog/gemini-pro-monthly?quantity=10").json()["unitSom"] == 160000
    assert c.get("/api/catalog/gemini-pro-monthly?quantity=100").status_code == 400


def test_wallet_purchase_sensitive_delivery_and_access(client):
    c = client
    headers = login(c)
    missing = c.post("/api/checkout", json={"productSlug":"gemini-pro-monthly","method":"WALLET"})
    assert missing.status_code == 403  # CSRF required
    bought = c.post("/api/checkout", headers=headers, json={"productSlug":"gemini-pro-monthly","method":"WALLET"})
    assert bought.status_code == 200, bought.text
    order = bought.json()["order"]
    assert order["status"] == "DELIVERED" and order["delivery"]["item"]["link"].startswith("https://example.invalid/")
    assert c.get("/api/me").json()["balanceSom"] == 350000 - order["totalSom"]
    orders = c.get("/api/orders").json()["data"]
    assert len(orders) == 1 and "delivery" not in orders[0]
    with session() as db:
        saved = db.scalar(select(Order).where(Order.code == order["code"]))
        assert saved.delivery_enc and "example.invalid" not in saved.delivery_enc
        assert db.scalar(select(WalletEntry).where(WalletEntry.order_id == saved.id, WalletEntry.kind == "PURCHASE"))
        stranger = User(telegram_id=888777666, first_name="Other", role="CUSTOMER")
        db.add(stranger)
        db.flush()
        stranger_id = stranger.id
    cookie = serializer().dumps({"uid":stranger_id,"nonce":"test"})
    c.cookies.set(SESSION_COOKIE,cookie)
    assert c.get("/api/orders/"+order["code"]).status_code == 404
    assert c.get("/api/admin/overview").status_code == 403


def test_demo_card_topup_once_and_ready_account(client):
    c=client
    h=login(c)
    result=c.post("/api/wallet/topup",headers=h,json={"amountSom":100000}).json()
    pid=result["payment"]["id"]
    assert result["payment"]["card"] == "TEST PAYMENT ONLY"
    before=c.get("/api/me").json()["balanceSom"]
    assert c.post(f"/api/demo/payments/{pid}/confirm",headers=h,json={}).status_code == 200
    assert c.post(f"/api/demo/payments/{pid}/confirm",headers=h,json={}).status_code == 200
    assert c.get("/api/me").json()["balanceSom"] == before+100000
    with session() as db:
        assert db.scalar(select(WalletEntry).where(WalletEntry.payment_id == pid))
        assert len(db.scalars(select(WalletEntry).where(WalletEntry.payment_id == pid)).all()) == 1
    order_response=c.post("/api/checkout",headers=h,json={"productSlug":"netflix-premium","method":"CARD","quantity":1}).json()
    assert order_response["order"]["status"] == "AWAITING_PAYMENT"
    code=order_response["order"]["code"]
    detail=c.get("/api/orders/"+code).json()
    assert detail["payment"]["id"] == order_response["payment"]["id"]
    assert c.post(f'/api/demo/payments/{detail["payment"]["id"]}/confirm',headers=h,json={}).status_code == 200
    delivered=c.get("/api/orders/"+code).json()
    assert delivered["status"] == "DELIVERED"
    assert "demo-1@example.invalid" in delivered["delivery"]["item"]["content"]
    assert "delivery" not in c.get("/api/orders").json()["data"][0]


def test_signed_hamyon_callback_never_fulfills_without_provider_confirmation(client,monkeypatch):
    c=client
    login(c)
    with session() as db:
        user=db.scalar(select(User).where(User.telegram_id == 900000001))
        set_setting(db,"hamyon_shop_id","shop-12")
        set_setting(db,"hamyon_shop_key","test-shop-secret")
        p=Payment(id="PAY-MOCK-SIGNED",user_id=user.id,purpose="TOPUP",amount_som=52001,
                  status="PENDING",provider_payment_id="pm-mock-001",shop_id_snapshot="shop-12",
                  signing_key_enc=encrypt("test-shop-secret"),expires_at=now()+timedelta(minutes=5))
        db.add(p)
    sign=hashlib.md5(b"shop-12pm-mock-00152001test-shop-secret").hexdigest()
    payload={"shop_id":"shop-12","payment_id":"pm-mock-001","order_id":"PAY-MOCK-SIGNED",
             "amount":"52001","status":"paid","sign":sign}
    initial=c.get("/api/me").json()["balanceSom"]
    bad={**payload,"sign":"0"*32}
    assert c.post("/webhooks/hamyon/complete",data=bad).status_code==403
    async def pending(self,pid):return {"status":"pending","payment_id":pid,"amount":52001,"order_id":"PAY-MOCK-SIGNED"}
    monkeypatch.setattr(HamyonClient,"status",pending)
    assert c.post("/webhooks/hamyon/complete",data=payload).status_code==503
    assert c.get("/api/me").json()["balanceSom"]==initial
    async def paid(self,pid):return {"status":"paid","payment_id":pid,"amount":52001,"order_id":"PAY-MOCK-SIGNED"}
    monkeypatch.setattr(HamyonClient,"status",paid)
    assert c.post("/webhooks/hamyon/complete",data=payload).status_code==200
    assert c.post("/webhooks/hamyon/complete",data=payload).status_code==200
    assert c.get("/api/me").json()["balanceSom"]==initial+52001
    with session() as db:
        assert len(db.scalars(select(WalletEntry).where(WalletEntry.payment_id=="PAY-MOCK-SIGNED")).all())==1


def test_timed_out_invoice_can_recover_via_signed_callback(client,monkeypatch):
    c=client;login(c)
    with session() as db:
        user=db.scalar(select(User).where(User.telegram_id==900000001))
        set_setting(db,"hamyon_shop_id","shop-12");set_setting(db,"hamyon_shop_key","key-rotated")
        db.add(Payment(id="PAY-TIMEOUT",user_id=user.id,purpose="TOPUP",amount_som=70000,status="SETUP_UNKNOWN",
                       provider_payment_id=None,shop_id_snapshot="shop-12",signing_key_enc=encrypt("old-secret")))
    async def paid(self,pid):return {"status":"paid","payment_id":pid,"amount":70000,"order_id":"PAY-TIMEOUT"}
    monkeypatch.setattr(HamyonClient,"status",paid)
    payload={"shop_id":"shop-12","payment_id":"pm-from-callback","order_id":"PAY-TIMEOUT","amount":"70000","status":"paid",
             "sign":hashlib.md5(b"shop-12pm-from-callback70000old-secret").hexdigest()}
    assert c.post("/webhooks/hamyon/complete",data=payload).status_code==200
    with session() as db:
        payment=db.get(Payment,"PAY-TIMEOUT")
        assert payment.status=="PAID" and payment.provider_payment_id=="pm-from-callback"


def test_admin_secret_redaction_editor_and_user_isolation(client):
    c=client;h=login(c)
    assert c.get('/api/admin/settings').json()['partner_api_key_configured'] is False
    response=c.put('/api/admin/settings',headers=h,json={"partner_api_key":"sk_live_0123456789","hamyon_shop_key":"secret-merchant","hamyon_shop_id":"shop-123","checkout_enabled":"1"})
    assert response.status_code==200,response.text
    settings=c.get('/api/admin/settings').json()
    assert settings['partner_api_key_configured'] and settings['hamyon_shop_key_configured']
    assert 'partner_api_key' not in settings and 'hamyon_shop_key' not in settings
    with session() as db:
        assert db.get(Setting,'partner_api_key').value != 'sk_live_0123456789'
    assert c.put('/api/admin/texts',headers=h,json=[{"key":"hero.title","lang":"ru","value":"Мои возможности"}]).status_code==200
    assert c.get('/api/public/config').json()['textOverrides']['ru']['hero.title']=='Мои возможности'
    edited=c.put('/api/admin/products/canva-pro',headers=h,json={"visible":True,"customPriceSom":63000,"markupPercent":None,"sortOrder":2,"titles":{"ru":"Канва Про"},"descriptions":{"ru":"Доступ на месяц"},"imagePath":"/static/img/products/canva.svg"})
    assert edited.status_code==200,edited.text
    assert c.get('/api/catalog/canva-pro?lang=ru').json()['name']=='Канва Про'
    assert c.get('/api/catalog/canva-pro?lang=ru').json()['unitSom']==63000
    assert c.post('/api/admin/media',headers={**h,'Content-Type':'image/svg+xml'},content=b'<svg/>').status_code==400
    with session() as db:
        db.add(User(telegram_id=333,first_name='Buyer',role='CUSTOMER'))
    user_id=next(x['id'] for x in c.get('/api/admin/users').json()['data'] if x['telegramId']==333)
    assert c.patch('/api/admin/users/'+str(user_id),headers=h,json={"adjustmentSom":50000,"note":"Support credit"}).status_code==200
    assert next(x['balanceSom'] for x in c.get('/api/admin/users').json()['data'] if x['id']==user_id)==50000
    assert c.get('/api/admin/audit').json()['data']


def test_partner_retry_reuses_external_id_and_permanent_failure_refunds(client,monkeypatch):
    import activon.commerce as commerce
    c=client;login(c)
    with session() as db:
        user=db.scalar(select(User).where(User.telegram_id==900000001))
        o=Order(code="ACT-TEST-RETRY",user_id=user.id,product_slug="gemini-pro-monthly",product_name="Gemini",delivery_type="LINK",
                quantity=1,unit_som=200000,total_som=200000,upstream_unit_usd=12.5,rate_snapshot=12800,method="WALLET",
                status="READY",external_order_id="activon-stable-retry-id")
        db.add(o);db.flush()
        user.balance_som-=200000
        db.add(WalletEntry(user_id=user.id,order_id=o.id,kind="PURCHASE",amount_som=-200000,balance_after=user.balance_som))
        oid=o.id
    calls=[]
    class FakePartner:
        def __init__(self,db):pass
        async def health(self):return {"ok":True}
        async def create_order(self,slug,quantity,external):
            calls.append(external)
            if len(calls)==1:raise ServiceError('RATE_LIMIT_EXCEEDED','rate',429,True)
            return {"ok":True,"orderCode":"SO-MOCK-1","delivery":{"link":"https://example.invalid/activation"}}
    monkeypatch.setattr(commerce,'PartnerClient',FakePartner)
    monkeypatch.setattr(commerce,'config',type('Config',(),{'demo':False})())
    asyncio.run(fulfill_order(oid))
    with session() as db:
        assert db.get(Order,oid).status=='RETRY_WAIT'
        db.get(Order,oid).retry_at=now()-timedelta(seconds=5)
    asyncio.run(fulfill_order(oid));asyncio.run(fulfill_order(oid))
    with session() as db:
        assert db.get(Order,oid).status=='DELIVERED'
        assert len(db.scalars(select(WalletEntry).where(WalletEntry.order_id==oid,WalletEntry.kind=='PURCHASE')).all())==1
    assert calls==['activon-stable-retry-id','activon-stable-retry-id']
    with session() as db:
        user=db.scalar(select(User).where(User.telegram_id==900000001))
        card_order=Order(code='ACT-TEST-REFUND',user_id=user.id,product_slug='missing',product_name='Missing',delivery_type='COUPON',
                         quantity=1,unit_som=55000,total_som=55000,upstream_unit_usd=3,rate_snapshot=12800,method='CARD',status='READY',external_order_id='activon-stable-refund')
        db.add(card_order);db.flush();db.add(Payment(id='PAY-FOR-REFUND',user_id=user.id,order_id=card_order.id,
            purpose='PRODUCT',amount_som=55000,status='PAID'))
        oid2=card_order.id;balance=user.balance_som
    class OutOfStock(FakePartner):
        async def create_order(self,slug,quantity,external):raise ServiceError('OUT_OF_STOCK','out',400,request_id='req_mock123')
    monkeypatch.setattr(commerce,'PartnerClient',OutOfStock)
    asyncio.run(fulfill_order(oid2));asyncio.run(fulfill_order(oid2))
    with session() as db:
        assert db.get(Order,oid2).status=='REFUNDED'
        assert db.get(Order,oid2).last_error_request_id=='req_mock123'
        assert db.scalar(select(User).where(User.telegram_id==900000001)).balance_som==balance+55000
        assert len(db.scalars(select(WalletEntry).where(WalletEntry.order_id==oid2,WalletEntry.kind=='REFUND')).all())==1


def test_telegram_initdata_hmac_and_freshness(monkeypatch):
    import hmac
    import time
    from urllib.parse import urlencode
    import activon.auth as auth
    monkeypatch.setattr(auth, 'config', type('BotConfig', (), {'bot_token': '123456:TEST_TOKEN'})())
    fields={'auth_date':str(int(time.time())), 'query_id':'AA-test',
            'user':json.dumps({'id':12345,'first_name':'Alice','language_code':'en'},separators=(',',':'))}
    secret=hmac.new(b'WebAppData',b'123456:TEST_TOKEN',hashlib.sha256).digest()
    check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    fields['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    raw=urlencode(fields)
    assert auth.verify_init_data(raw)['id']==12345
    with pytest.raises(Exception):auth.verify_init_data(raw.replace('Alice','Mallory'))
    with pytest.raises(Exception):auth.verify_init_data(raw+'&user=duplicate')
    with pytest.raises(Exception):auth.verify_init_data('auth_date=1&user=bad&hash=0')


def test_simultaneous_invoices_use_distinct_exact_som_amount(client):
    c=client;h=login(c)
    one=c.post('/api/wallet/topup',headers=h,json={'amountSom':50000}).json()['payment']
    two=c.post('/api/wallet/topup',headers=h,json={'amountSom':50000}).json()['payment']
    assert one['amountSom']==50000 and two['amountSom']==50001
    assert one['id']!=two['id']
    c.post('/api/demo/payments/'+one['id']+'/confirm',headers=h,json={})
    c.post('/api/demo/payments/'+two['id']+'/confirm',headers=h,json={})
    assert c.get('/api/me').json()['balanceSom']==350000+50000+50001


def test_http_adapters_send_documented_contract(client,monkeypatch):
    import httpx
    import activon.providers as providers
    c=client;login(c)
    with session() as db:
        set_setting(db,'partner_api_key','sk_live_test_only')
        set_setting(db,'hamyon_shop_id','shop-123')
        set_setting(db,'hamyon_shop_key','test-key')
    requests=[]
    def handler(req):
        requests.append(req)
        if req.url.path.endswith('/orders'):
            return httpx.Response(200,json={'ok':True,'orderCode':'SO-MOCK','delivery':{'link':'https://example.invalid/'}})
        if req.url.path.endswith('/payment/create'):
            import urllib.parse
            values=dict(urllib.parse.parse_qsl(req.content.decode()))
            assert values=={'shop_id':'shop-123','shop_key':'test-key','amount':'95001','order_id':'PAY-CONTRACT'}
            return httpx.Response(200,json={'payment_id':'hamyon-mock','order_id':'PAY-CONTRACT','amount':95001,'card':'8600 0000 0000 0000','expires_in':300})
        if req.url.path.endswith('/payment/status'):
            assert req.url.params['payment_id']=='hamyon-mock'
            return httpx.Response(200,json={'payment_id':'hamyon-mock','amount':95001,'status':'paid'})
        return httpx.Response(200,json={'ok':True})
    BaseAsyncClient=httpx.AsyncClient
    class MockAsyncClient(BaseAsyncClient):
        def __init__(self,*args,**kwargs):super().__init__(*args,transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(providers.httpx,'AsyncClient',MockAsyncClient)
    async def run():
        with session() as db:
            partner=providers.PartnerClient(db);hamyon=providers.HamyonClient(db)
        assert (await partner.create_order('gemini-pro-monthly',2,'activon-fixed-id'))['orderCode']=='SO-MOCK'
        assert (await hamyon.create('PAY-CONTRACT',95001))['payment_id']=='hamyon-mock'
        assert (await hamyon.status('hamyon-mock'))['status']=='paid'
    asyncio.run(run())
    order_req=requests[0]
    assert str(order_req.url)=='https://ggsoma.store/api/partner/v1/orders'
    assert order_req.headers['Authorization']=='Bearer sk_live_test_only'
    assert order_req.read() and json.loads(order_req.content)['externalOrderId']=='activon-fixed-id'
    assert requests[1].headers['Content-Type'].startswith('application/x-www-form-urlencoded')


def test_profile_photo_upload_is_sanitized_and_owned(client):
    from io import BytesIO
    from PIL import Image
    c=client;h=login(c)
    buff=BytesIO();Image.new('RGB',(96,96),(52,125,240)).save(buff,format='PNG')
    assert c.post('/api/profile/avatar',headers={'Content-Type':'image/png'},content=buff.getvalue()).status_code==403
    result=c.post('/api/profile/avatar',headers={**h,'Content-Type':'image/png'},content=buff.getvalue())
    assert result.status_code==200,result.text
    url=result.json()['photoUrl']
    assert url.startswith('/uploads/') and url.endswith('.webp')
    assert c.get('/api/me').json()['photoUrl']==url
    assert c.get(url).headers['content-type'].startswith('image/webp')
    assert c.post('/api/profile/avatar',headers={**h,'Content-Type':'image/svg+xml'},content=b'<svg/>').status_code==400
    with session() as db:
        user=db.scalar(select(User).where(User.telegram_id==900000001))
        assert user.avatar_path==url
    from pathlib import Path
    (Path('uploads') / url.rsplit('/',1)[-1]).unlink(missing_ok=True)
