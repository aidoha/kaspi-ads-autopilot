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

SAMPLE_CAMPAIGNS = {
    "data": [
        {"id": 2899523, "name": "Бритвы", "state": "Enabled", "dailyBudget": 10000.0},
        {"id": 3032419, "name": "Аэрогриль 08.08.2026", "state": "Enabled", "dailyBudget": 20000.0},
        {"id": 2711494, "name": "Аэрогриль", "state": "Paused", "dailyBudget": 5000.0},
        {"id": 2268077, "name": "5 ноября", "state": "Archived", "dailyBudget": 0.0},
    ],
}


def make_client(handler, *, dry_run=True, max_retries=3, on_auth_error=None):
    """MarketingClient c инъекцией httpx-клиента на MockTransport."""
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://marketing.kaspi.kz")
    return MarketingClient(
        merchant_id=MERCHANT_ID,
        client=http,
        dry_run=dry_run,
        max_retries=max_retries,
        backoff_base=0,  # без реальных пауз в тесте
        on_auth_error=on_auth_error,
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


def test_list_active_campaigns_filters_enabled():
    from connectors.marketing_client import Campaign
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=SAMPLE_CAMPAIGNS)

    with make_client(handler) as mc:
        campaigns = mc.list_active_campaigns("2026-08-10", "2026-08-11")

    assert f"merchant/{MERCHANT_ID}/Campaigns" in captured["url"], captured["url"]
    assert "state=Enabled" in captured["url"], captured["url"]
    assert "StartDate=2026-08-10" in captured["url"], captured["url"]
    assert "EndDate=2026-08-11" in captured["url"], captured["url"]
    # только Enabled, id приведён к строке
    assert [(c.id, c.name) for c in campaigns] == [
        ("2899523", "Бритвы"), ("3032419", "Аэрогриль 08.08.2026")
    ]
    assert all(isinstance(c, Campaign) and c.state == "Enabled" for c in campaigns)
    print("✓ marketing: list_active_campaigns фильтрует Enabled и парсит поля")


def test_list_active_campaigns_parses_daily_budget():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_CAMPAIGNS)

    with make_client(handler) as mc:
        campaigns = mc.list_active_campaigns("2026-08-10", "2026-08-11")

    budgets = {c.id: c.daily_budget for c in campaigns}
    assert budgets == {"2899523": 10000.0, "3032419": 20000.0}, budgets
    print("✓ marketing: list_active_campaigns парсит dailyBudget")


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


def test_401_refreshes_session_and_retries():
    # Сессия протухла (сервер инвалидировал раньше таймстампа) → 401.
    # Клиент обязан один раз обновить куки через on_auth_error и повторить запрос.
    calls = {"n": 0}
    refresh = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json=SAMPLE_PRODUCTS)

    def on_auth_error():
        refresh["n"] += 1
        return {"X-Kb-Session-Id": "fresh-cookie"}   # свежие куки

    with make_client(handler, on_auth_error=on_auth_error) as mc:
        products = mc.get_campaign_products(CAMPAIGN_ID, "2026-08-08", "2026-08-09")

    assert refresh["n"] == 1, "на 401 сессия должна обновиться ровно один раз"
    assert calls["n"] == 2, "после обновления сессии — повторный запрос"
    assert len(products) == 2, "повтор вернул данные"
    print("✓ marketing: 401 → обновление сессии + повтор запроса")


def test_401_gives_up_if_refresh_does_not_help():
    # Если после обновления сессии всё равно 401 — не виснем: обновляем один раз,
    # дальше отдаём ошибку (RuntimeError), а не крутимся бесконечно.
    calls = {"n": 0}
    refresh = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    def on_auth_error():
        refresh["n"] += 1
        return {"X-Kb-Session-Id": "still-bad"}

    raised = False
    with make_client(handler, max_retries=4, on_auth_error=on_auth_error) as mc:
        try:
            mc.get_campaign_products(CAMPAIGN_ID, "2026-08-08", "2026-08-09")
        except RuntimeError:
            raised = True

    assert raised, "стойкий 401 должен привести к ошибке, а не к зависанию"
    assert refresh["n"] == 1, "обновляем сессию не более одного раза за запрос"
    print("✓ marketing: стойкий 401 → одна попытка refresh, затем ошибка")


def test_401_without_provider_raises():
    # Без on_auth_error 401 просто приводит к ошибке (обратная совместимость).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    raised = False
    with make_client(handler, max_retries=2) as mc:
        try:
            mc.get_campaign_products(CAMPAIGN_ID, "2026-08-08", "2026-08-09")
        except RuntimeError:
            raised = True

    assert raised, "401 без провайдера рефреша — ошибка, не зависание"
    print("✓ marketing: 401 без провайдера — ошибка")


if __name__ == "__main__":
    test_get_products_parses_fields()
    test_list_active_campaigns_filters_enabled()
    test_list_active_campaigns_parses_daily_budget()
    test_dry_run_does_not_send_put()
    test_live_put_sends_correct_request()
    test_get_retries_on_429()
    test_401_refreshes_session_and_retries()
    test_401_gives_up_if_refresh_does_not_help()
    test_401_without_provider_raises()
    print("-" * 60)
    print("✓ Все проверки marketing_client прошли")
