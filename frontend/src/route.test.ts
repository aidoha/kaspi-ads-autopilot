import { describe, it, expect } from "vitest";
import { pathForScreen, screenFromPath } from "./route";

describe("screenFromPath", () => {
  it("/settings (и вложенные пути) — экран настроек", () => {
    expect(screenFromPath("/settings")).toBe("settings");
    expect(screenFromPath("/settings/")).toBe("settings");
  });

  it("любой другой путь — экран товаров", () => {
    expect(screenFromPath("/")).toBe("products");
    expect(screenFromPath("")).toBe("products");
    expect(screenFromPath("/products")).toBe("products");
  });
});

describe("pathForScreen", () => {
  it("обратное отображение для history.pushState", () => {
    expect(pathForScreen("settings")).toBe("/settings");
    expect(pathForScreen("products")).toBe("/");
  });
});
