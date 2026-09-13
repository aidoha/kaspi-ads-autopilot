import { describe, it, expect } from "vitest";
import { fmtMoney, fmtPct, fmtTime, fmtX, tacosHealth } from "./format";

describe("форматтеры", () => {
  it("null — это прочерк, а не ноль", () => {
    // Сервер шлёт null, когда величина НЕ ОПРЕДЕЛЕНА: выручки за период нет,
    // делить не на что. Ноль означал бы «посчитали и получилось ноль» —
    // другое утверждение, и владелец принял бы по нему другое решение.
    expect(fmtMoney(null)).toBe("—");
    expect(fmtPct(null)).toBe("—");
    expect(fmtX(null)).toBe("—");
    expect(fmtMoney(0)).toBe("0");
    expect(fmtPct(0)).toBe("0,0%");
  });

  it("деньги — с разделителем тысяч и без копеек", () => {
    expect(fmtMoney(15200)).toBe("15 200");
    expect(fmtMoney(222381.4)).toBe("222 381");
  });

  it("доли приходят как 0..1 и показываются процентами", () => {
    expect(fmtPct(0.058)).toBe("5,8%");
    expect(fmtPct(0.245)).toBe("24,5%");
  });

  it("ROAS — кратность", () => {
    expect(fmtX(14.6)).toBe("14,6×");
  });

  it("время решения — часы:минуты Алматы, без даты", () => {
    expect(fmtTime(null)).toBe("—");
    // 1700000000с = 15.11.2023 04:13:20 в Алматы (+06:00).
    expect(fmtTime(1_700_000_000)).toBe("04:13");
  });

  it("здоровье TACoS: пороги те же, что были в старой панели", () => {
    expect(tacosHealth(0.05)).toBe("good");
    expect(tacosHealth(0.12)).toBe("warn");
    expect(tacosHealth(0.25)).toBe("crit");
    expect(tacosHealth(null)).toBe("na");
  });
});
