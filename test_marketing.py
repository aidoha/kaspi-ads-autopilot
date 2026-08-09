"""
test_marketing.py — оффлайн-тест marketing_client на фейковых ответах кабинета.

Мокаем HTTP через httpx.MockTransport, живые креды НЕ нужны.
Проверяем: парсинг GET products, ключ сшивки merchant_sku, dry-run НЕ шлёт PUT,
боевой PUT формирует правильные URL/тело, ретрай на 429.

Запуск: .venv/bin/python test_marketing.py
"""

import json

import httpx

from connectors.marketing_client import MarketingClient, CampaignProduct

MERCHANT_ID = "832398"
CAMPAIGN_ID = "2711494"

# Фейковый ответ GET .../products — форма {result:"Ok", data:[...]}
SAMPLE_PRODUCTS = {
    "result": "Ok",
    "data": [
        {
            "sku": "166350900",
            "merchantSku": "432085472",
            "campaignProductId": 5551,
            "bid": 18,
            "avgCpc": 12.5,
            "score": 7.0,
            "buyBox": True,
            "productState": "Active",
            "cost": 3600,
            "costToday": 420,
            "gmv": 97800,
            "crr": 3.68,
            "cr": 4.1,
            "ctr": 2.2,
            "views": 5400,
            "clicks": 120,
            "carts": 9,
            "transactions": 5,
            "price": 48900,
        },
        {
            "sku": "166350901",
            "merchantSku": "608122048",
            "campaignProductId": 5552,
            "bid": 25,
            "avgCpc": 22.0,
            "score": 5.5,
            "buyBox": False,
            "productState": "Paused",
            "cost": 1500,
            "costToday": 0,
            "gmv": 0,
            "crr": 0,
            "cr": 0,
            "ctr": 1.1,
            "views": 800,
            "clicks": 10,
            "carts": 0,
            "transactions": 0,
            "price": 59900,
        },
    ],
}


def make_client(handler, *, dry_run=True, max_retries=3):
    """MarketingClient c инъекцией httpx-клиента на MockTransport."""
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://marketing.kaspi.kz")
    return MarketingClient(
        merchant_id=MERCHANT_ID,
        client=http,
        dry_run=dry_run,
        max_retries=max_retries,
        backoff_base=0,  # без реальных пауз в тесте
    )


def test_get_products_parses_fields():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=SAMPLE_PRODUCTS)

    with make_client(handler) as mc:
        products = mc.get_campaign_products(CAMPAIGN_ID, "2026-08-08", "2026-08-09")

    # URL содержит merchant/campaign и даты
    assert f"merchant/{MERCHANT_ID}" in captured["url"], captured["url"]
    assert f"campaign/{CAMPAIGN_ID}" in captured["url"], captured["url"]
    assert "StartDate=2026-08-08" in captured["url"], captured["url"]
    assert "EndDate=2026-08-09" in captured["url"], captured["url"]

    assert len(products) == 2
    p = products[0]
    assert isinstance(p, CampaignProduct)
    assert p.sku == "166350900"
    assert p.merchant_sku == "432085472"   # КЛЮЧ сшивки с выручкой
    assert p.bid == 18
    assert p.avg_cpc == 12.5
    assert p.score == 7.0
    assert p.buy_box is True
    assert p.product_state == "Active"
    assert p.cost_today == 420
    assert p.clicks == 120
    assert p.carts == 9
    print("✓ get_campaign_products: парсинг полей и URL")


def test_dry_run_does_not_send_put():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"result": "Ok"})

    with make_client(handler, dry_run=True) as mc:
        result = mc.update_bids(CAMPAIGN_ID, ["166350900"], 20)

    assert calls["n"] == 0, "dry_run НЕ должен слать HTTP-запрос"
    assert result["dry_run"] is True
    assert result["sent"] is False
    assert result["skuList"] == ["166350900"]
    assert result["bid"] == 20
    print("✓ update_bids dry_run: PUT не отправлен")


def test_live_put_sends_correct_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"result": "Ok"})

    with make_client(handler, dry_run=False) as mc:
        result = mc.update_bids(CAMPAIGN_ID, ["166350900", "166350901"], 20)

    assert captured["method"] == "PUT"
    assert "update-bid" in captured["url"], captured["url"]
    assert f"merchant/{MERCHANT_ID}" in captured["url"]
    assert f"campaign/{CAMPAIGN_ID}" in captured["url"]
    assert captured["body"] == {"skuList": ["166350900", "166350901"], "bid": 20}
    assert result["sent"] is True
    assert result["dry_run"] is False
    print("✓ update_bids боевой: PUT с правильными URL и телом (skuList массив)")


def test_get_retries_on_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"result": "TooMany"})
        return httpx.Response(200, json=SAMPLE_PRODUCTS)

    with make_client(handler, max_retries=5) as mc:
        products = mc.get_campaign_products(CAMPAIGN_ID, "2026-08-08", "2026-08-09")

    assert calls["n"] == 3, f"ожидали 3 попытки, было {calls['n']}"
    assert len(products) == 2
    print("✓ get_campaign_products: ретрай на 429")


if __name__ == "__main__":
    test_get_products_parses_fields()
    test_dry_run_does_not_send_put()
    test_live_put_sends_correct_request()
    test_get_retries_on_429()
    print("-" * 60)
    print("✓ Все проверки marketing_client прошли")
