import { describe, it, expect } from "vitest";
import { splitByVisibility } from "./visibility";
import type { Product } from "./types";

function product(over: Partial<Product> & { sku: string }): Product {
  return {
    merchant_sku: "m" + over.sku, campaign_ids: ["c1"], name: null, bid: 40,
    cost: null, revenue: null, clicks: null, carts: null, tacos: null,
    roas: null, ctr: null, cr: null, enabled: false, status: "активен",
    bid_spark: [], hidden: false, ...over,
  };
}

describe("разбиение списка на видимые и скрытые", () => {
  it("раскладывает по флагу, сохраняя порядок сервера", () => {
    const got = splitByVisibility([
      product({ sku: "s1" }),
      product({ sku: "s2", hidden: true }),
      product({ sku: "s3" }),
      product({ sku: "s4", hidden: true }),
    ]);
    expect(got.visible.map((p) => p.sku)).toEqual(["s1", "s3"]);
    expect(got.hidden.map((p) => p.sku)).toEqual(["s2", "s4"]);
  });

  it("«биддер ведёт N из M» считает только видимые", () => {
    // Иначе уборка в списке теряет смысл: скрыл четыре мусорных товара, а
    // знаменатель как был девять.
    const got = splitByVisibility([
      product({ sku: "s1", enabled: true }),
      product({ sku: "s2", enabled: true }),
      product({ sku: "s3" }),
      product({ sku: "s4", hidden: true, enabled: true }),
      product({ sku: "s5", hidden: true }),
    ]);
    expect(got.enabledVisible).toBe(2);
    expect(got.visible.length).toBe(3);
  });

  it("считает скрытых, которых биддер всё ещё ведёт", () => {
    // Это предупреждение в шапке раздела: спрятанный товар продолжает тратить
    // деньги, и владелец должен это видеть, не раскрывая раздел.
    const got = splitByVisibility([
      product({ sku: "s1", enabled: true }),
      product({ sku: "s2", hidden: true, enabled: true }),
      product({ sku: "s3", hidden: true }),
    ]);
    expect(got.enabledHidden).toBe(1);
    expect(got.hidden.length).toBe(2);
  });

  it("кампании в подзаголовке — только по видимым, без повторов", () => {
    const got = splitByVisibility([
      product({ sku: "s1", campaign_ids: ["c1", "c2"] }),
      product({ sku: "s2", campaign_ids: ["c2"] }),
      product({ sku: "s3", campaign_ids: ["c9"], hidden: true }),
    ]);
    expect(got.campaignCount).toBe(2);
  });

  it("пустой список не ломает счётчики", () => {
    const got = splitByVisibility([]);
    expect(got).toEqual({
      visible: [], hidden: [], enabledVisible: 0, enabledHidden: 0,
      campaignCount: 0,
    });
  });
});
