import { describe, it, expect } from "vitest";
import { buildSettingsPayload, formatCampaignIds, parseCampaignIds, visibleFields } from "./settingsForm";

describe("visibleFields", () => {
  it("прячет dry_run и campaign_ids из общей формы — у них своя вёрстка", () => {
    expect(visibleFields(["target_tacos_low", "dry_run", "campaign_ids", "min_bid"]))
      .toEqual(["target_tacos_low", "min_bid"]);
  });
});

describe("parseCampaignIds", () => {
  it("разбирает строку через запятую, обрезая пробелы и пустые элементы", () => {
    expect(parseCampaignIds("2899523, 3032419 ,,")).toEqual(["2899523", "3032419"]);
  });

  it("пустая строка (и строка из пробелов/запятых) — пустой список: значит «все кампании»", () => {
    expect(parseCampaignIds("")).toEqual([]);
    expect(parseCampaignIds("   ")).toEqual([]);
    expect(parseCampaignIds(" , , ")).toEqual([]);
  });
});

describe("formatCampaignIds", () => {
  it("список id → строка через запятую без пробелов", () => {
    expect(formatCampaignIds(["2899523", "3032419"])).toBe("2899523,3032419");
  });

  it("null/undefined (кампании не заданы) — пустая строка", () => {
    expect(formatCampaignIds(null)).toBe("");
    expect(formatCampaignIds(undefined)).toBe("");
  });
});

describe("buildSettingsPayload", () => {
  it("склеивает значения полей формы и разобранный список кампаний, без dry_run", () => {
    expect(buildSettingsPayload({ min_bid: "10", bid_ceiling: "50" }, "c1, c2"))
      .toEqual({ min_bid: "10", bid_ceiling: "50", campaign_ids: ["c1", "c2"] });
  });

  it("пустое поле кампаний → campaign_ids: []", () => {
    expect(buildSettingsPayload({ min_bid: "10" }, "")).toEqual({ min_bid: "10", campaign_ids: [] });
  });
});
