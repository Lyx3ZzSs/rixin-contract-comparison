import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProgressRing } from "./ProgressRing";

describe("ProgressRing", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders an accessible SVG progress ring without visible percent text", () => {
    const { container } = render(<ProgressRing value={35} label="文档解析中" />);

    const ring = screen.getByRole("progressbar", { name: /文档解析中/ });
    expect(ring).toHaveAttribute("aria-valuenow", "35");
    expect(ring).toHaveAttribute("data-progress-target", "35");
    expect(screen.queryByText("35%")).not.toBeInTheDocument();
    expect(container.querySelector(".progress-ring-value")).toBeInTheDocument();
  });

  it("smoothly advances the displayed ring value toward a new target", () => {
    vi.useFakeTimers();
    const { rerender } = render(<ProgressRing value={10} label="文档解析中" />);

    act(() => {
      vi.advanceTimersByTime(300);
    });

    rerender(<ProgressRing value={80} label="证据定位中" />);
    const ring = screen.getByRole("progressbar", { name: /证据定位中/ });

    expect(ring).toHaveAttribute("aria-valuenow", "80");
    expect(Number(ring.getAttribute("data-progress-display"))).toBeLessThan(80);

    act(() => {
      vi.advanceTimersByTime(2200);
    });

    expect(ring).toHaveAttribute("data-progress-display", "80");
  });
});
