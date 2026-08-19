"""
test_search_client.py — тест парсера и сборщика выдачи Kaspi (без сети).

Запуск: .venv/bin/python test_search_client.py
"""
from connectors.search_client import parse_filters_page, fetch_listing, Card


def _page(ids_titles):
    return {"data": {
        "total": 2697,
        "cards": [
            {"id": pid, "configSku": pid, "title": t, "brand": "Без бренда",
             "unitPrice": 59900, "unitSalePrice": 47900}
            for pid, t in ids_titles
        ],
    }}


def test_parse_assigns_sequential_ranks_and_reads_total():
    data = _page([("111", "A"), ("222", "B")])["data"]
    cards, total = parse_filters_page(data, start_rank=1)
    assert total == 2697
    assert [c.rank for c in cards] == [1, 2]
    assert cards[0].product_id == "111" and cards[0].title == "A"
    assert cards[0].price == 47900          # prefers unitSalePrice
    assert cards[0].is_ad is False


def test_parse_continues_rank_across_pages():
    data = _page([("333", "C"), ("444", "D")])["data"]
    cards, _ = parse_filters_page(data, start_rank=13)
    assert [c.rank for c in cards] == [13, 14]


def test_parse_handles_missing_cards():
    cards, total = parse_filters_page({"total": 0}, start_rank=1)
    assert cards == [] and total == 0


def test_fetch_listing_finds_our_rank_across_pages():
    pages = {
        0: _page([(str(i), f"t{i}") for i in range(100, 112)]),   # ranks 1..12
        1: _page([("999", "OURS")] + [(str(i), f"t{i}") for i in range(200, 211)]),  # rank 13 = ours
    }
    calls = []

    def fake_get(url):
        calls.append(url)
        page = 1 if "page=1" in url else 0
        return pages[page]

    lst = fetch_listing("аэрогриль", "750000000", "Magnum_ZONE1",
                        our_product_id="999", max_depth=100, http_get=fake_get)
    assert lst.our_rank == 13
    assert lst.total == 2697
    assert lst.cards[12].product_id == "999"
    assert any("c=750000000" in u for u in calls)
    assert any("availableInZones:Magnum_ZONE1" in u for u in calls)


def test_fetch_listing_our_rank_none_when_beyond_depth():
    empty = {"data": {"total": 5, "cards": []}}
    lst = fetch_listing("x", "750000000", "Z", our_product_id="absent",
                        max_depth=24, http_get=lambda url: empty)
    assert lst.our_rank is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK test_search_client")
