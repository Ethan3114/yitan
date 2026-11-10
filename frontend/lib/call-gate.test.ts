import { describe, expect, it } from "vitest";
import { canStartCall } from "./call-gate";

describe("canStartCall", () => {
  it("needs both consent and a configured voice service", () => {
    expect(canStartCall(false, true)).toBe(false);
    expect(canStartCall(true, false)).toBe(false);
    expect(canStartCall(true, true)).toBe(true);
  });
});
