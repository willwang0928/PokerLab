import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { leakReport } from "../test/leaks";
import { useLeakReport } from "./useLeakReport";

afterEach(() => vi.unstubAllGlobals());

function serve(...bodies: (object | number)[]) {
  const fn = vi.fn(async () => {
    const b = bodies.length > 1 ? bodies.shift()! : bodies[0];
    return typeof b === "number" ? new Response("{}", { status: b }) : new Response(JSON.stringify(b));
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("useLeakReport", () => {
  it("does nothing without a server profile", () => {
    const fn = serve(leakReport);
    const { result } = renderHook(() => useLeakReport(undefined, 0, null));
    expect(result.current.state).toEqual({ status: "none" });
    expect(fn).not.toHaveBeenCalled();
  });

  it("loads, refetches when hands are saved or a sync completes, and sends the key only in a header", async () => {
    const fn = serve(leakReport);
    const { result, rerender } = renderHook(({ v, s }) => useLeakReport("tok", v, s), { initialProps: { v: 0, s: null as number | null } });
    await waitFor(() => expect(result.current.state.status).toBe("ready"));
    expect(fn).toHaveBeenCalledTimes(1);
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/profile/leaks");
    expect(url).not.toContain("tok");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
    rerender({ v: 1, s: null });
    rerender({ v: 1, s: 123 });
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(3));
  });

  it("keeps the last report on an error and recovers on retry", async () => {
    serve(leakReport, 503, leakReport);
    const { result, rerender } = renderHook(({ v }) => useLeakReport("tok", v, null), { initialProps: { v: 0 } });
    await waitFor(() => expect(result.current.state.status).toBe("ready"));
    rerender({ v: 1 });
    await waitFor(() => expect(result.current.state.status).toBe("error"));
    expect(result.current.state).toMatchObject({ report: leakReport, message: expect.stringMatching(/storage is unavailable/) });
    await act(() => result.current.reload());
    expect(result.current.state.status).toBe("ready");
  });

  it("forgets the report when the profile is reset or switched", async () => {
    serve(leakReport);
    const { result, rerender } = renderHook(({ t }) => useLeakReport(t, 0, null), { initialProps: { t: "tok" as string | undefined } });
    await waitFor(() => expect(result.current.state.status).toBe("ready"));
    rerender({ t: undefined });
    await waitFor(() => expect(result.current.state).toEqual({ status: "none" }));
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    rerender({ t: "other" });
    await waitFor(() => expect(result.current.state).toEqual({ status: "loading", report: null }));
  });
});
