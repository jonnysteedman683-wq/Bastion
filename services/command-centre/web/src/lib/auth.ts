export type CurrentUser = { id: string; email: string | null };

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: string };
    return body.error ?? "authentication request failed";
  } catch {
    return "authentication request failed";
  }
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await fetch("/api/auth/me", { credentials: "same-origin" });
  if (!response.ok) throw new Error(await errorMessage(response));
  return ((await response.json()) as { user: CurrentUser }).user;
}

export async function requestMagicLink(email: string): Promise<void> {
  const response = await fetch("/auth/magic-link", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
}

export async function signOut(): Promise<void> {
  const response = await fetch("/auth/sign-out", { method: "POST", credentials: "same-origin" });
  if (!response.ok) throw new Error(await errorMessage(response));
}
