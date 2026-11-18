import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useTypewriter } from "@/features/chat/use-typewriter";

/** 一条典型的短回答（远小于旧实现里 160 字的"直接补齐"阈值）。 */
const SHORT_ANSWER = "库存票据每月至少抽点一次，并编制盘点表。";

describe("useTypewriter", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("收到 done 后短回答仍逐字放出，而不是一次补齐", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ text, enabled }: { text: string; enabled: boolean }) =>
        useTypewriter(text, { enabled }),
      { initialProps: { text: "", enabled: true } },
    );

    // 流式期间正文到齐，先放出一部分
    rerender({ text: SHORT_ANSWER, enabled: true });
    act(() => {
      vi.advanceTimersByTime(120);
    });

    // done 到达
    rerender({ text: SHORT_ANSWER, enabled: false });

    // 旧实现有 160 字补齐阈值：done 一到就把短回答整段贴出，等于没有打字机。
    // 新实现应在这时"还没放完"。
    expect(result.current).not.toBe(SHORT_ANSWER);

    // 继续走时间，最终应匀速放完
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current).toBe(SHORT_ANSWER);
  });

  it("帧间隔小于一个 tick 时游标仍持续推进（逐字放出没有卡死）", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ text }: { text: string }) => useTypewriter(text, { enabled: true }),
      { initialProps: { text: "" } },
    );

    // 模拟多个快速到达的 token 帧：每帧间隔 20ms，都不到一次 60ms tick
    rerender({ text: SHORT_ANSWER.slice(0, 4) });
    act(() => {
      vi.advanceTimersByTime(20);
    });
    rerender({ text: SHORT_ANSWER.slice(0, 9) });
    act(() => {
      vi.advanceTimersByTime(20);
    });
    rerender({ text: SHORT_ANSWER });
    act(() => {
      vi.advanceTimersByTime(20);
    });

    // 累计 60ms 后至少应有一个 tick 把游标推离 0；旧实现每帧重建定时器、
    // 帧间隔 <60ms 时一个 tick 都触发不了，游标永远停在 0。
    expect(result.current.length).toBeGreaterThan(0);
    expect(result.current.length).toBeLessThan(SHORT_ANSWER.length);
  });

  it("reduced-motion 下正文增长时立即显示全文，不做逐字动画", () => {
    const matchSpy = vi
      .spyOn(window, "matchMedia")
      .mockImplementation(
        (query: string) =>
          ({
            matches: query === "(prefers-reduced-motion: reduce)",
            media: query,
            onchange: null,
            addListener: vi.fn(),
            removeListener: vi.fn(),
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            dispatchEvent: vi.fn(),
          }) as unknown as MediaQueryList,
      );
    try {
      const { result, rerender } = renderHook(
        ({ text }: { text: string }) => useTypewriter(text, { enabled: true }),
        { initialProps: { text: "" } },
      );
      rerender({ text: SHORT_ANSWER });
      expect(result.current).toBe(SHORT_ANSWER);
    } finally {
      matchSpy.mockRestore();
    }
  });
});
