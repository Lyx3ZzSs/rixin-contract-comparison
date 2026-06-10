import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const stylesPath = join(process.cwd(), "src", "styles.css");
const css = readFileSync(stylesPath, "utf8");

describe("diff color styles", () => {
  it("keeps audit type badge color contrast readable", () => {
    expect(contrastRatio(cssVariable("--diff-add-text"), cssVariable("--diff-add-soft"))).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(cssVariable("--diff-delete-text"), cssVariable("--diff-delete-soft"))).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(cssVariable("--diff-modify-text"), cssVariable("--diff-modify-soft"))).toBeGreaterThanOrEqual(4.5);
  });

  it("adds non-color axis marker symbols for each diff type", () => {
    expect(ruleContent(".axis-marker.add::after")).toContain('content: "+"');
    expect(ruleContent(".axis-marker.delete::after")).toContain('content: "-"');
    expect(ruleContent(".axis-marker.modify::after")).toContain('content: "~"');
  });
});

function cssVariable(name: string): string {
  const match = new RegExp(`${escapeRegExp(name)}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(css);
  if (!match) {
    throw new Error(`Missing CSS variable ${name}`);
  }
  return match[1];
}

function ruleContent(selector: string): string {
  const match = new RegExp(`${escapeRegExp(selector)}\\s*\\{(?<body>[^}]+)\\}`).exec(css);
  if (!match?.groups?.body) {
    throw new Error(`Missing CSS rule ${selector}`);
  }
  return match.groups.body.replace(/\s+/g, " ").trim();
}

function contrastRatio(left: string, right: string): number {
  const leftLum = relativeLuminance(left);
  const rightLum = relativeLuminance(right);
  const lighter = Math.max(leftLum, rightLum);
  const darker = Math.min(leftLum, rightLum);
  return (lighter + 0.05) / (darker + 0.05);
}

function relativeLuminance(hex: string): number {
  const [red, green, blue] = hexToRgb(hex).map(linearizedChannel);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function hexToRgb(hex: string): [number, number, number] {
  return [
    Number.parseInt(hex.slice(1, 3), 16),
    Number.parseInt(hex.slice(3, 5), 16),
    Number.parseInt(hex.slice(5, 7), 16),
  ];
}

function linearizedChannel(value: number): number {
  const normalized = value / 255;
  return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
