import { afterEach, describe, expect, it, vi } from "vitest";
import { getCurrentUser, requestMagicLink, signOut } from "./auth";

afterEach(() => vi.unstubAllGlobals());

describe("command-centre auth API", () => {
  it("reads the current user with same-origin credentials", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ user: { id: "u1", email: "jonny@example.com" } }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await expect(getCurrentUser()).resolves.toEqual({ id: "u1", email: "jonny@example.com" });
    expect(fetch).toHaveBeenCalledWith("/api/auth/me", { credentials: "same-origin" });
  });

  it("submits an email to the command-centre magic-link endpoint", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("", { status: 202 }));
    vi.stubGlobal("fetch", fetch);

    await expect(requestMagicLink("jonny@example.com")).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledWith("/auth/magic-link", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: "jonny@example.com" }),
    });
  });

  it("reports a rejected magic-link request", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "unable to send sign-in link" }), { status: 400 })));

    await expect(requestMagicLink("bad")).rejects.toThrow("unable to send sign-in link");
  });

  it("clears the server session through the command centre", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);

    await expect(signOut()).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledWith("/auth/sign-out", { method: "POST", credentials: "same-origin" });
  });
});
