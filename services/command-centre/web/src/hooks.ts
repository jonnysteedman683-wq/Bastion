import { useEffect, useRef, useState } from "react";
import type { SpendResponse } from "@/lib/spend";
import type { ChecksResponse, WatcherResponse } from "@/lib/health";
import { cursorOf, getJSON, mergeEvents, type ApprovalsResponse, type FeedResponse, type LedgerEvent, type OperationsResponse, type RosterResponse } from "@/lib/ledger";

const FEED_POLL_MS = 5_000;
const ROSTER_POLL_MS = 30_000;
const SPEND_POLL_MS = 60_000;
const HEALTH_POLL_MS = 60_000;

/** Live ledger feed: loads the latest events, then polls for newer ones. */
export function useFeed() {
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [source, setSource] = useState<FeedResponse["source"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef(0);

  useEffect(() => {
    const ctrl = new AbortController();
    let timer: number | undefined;
    const tick = async () => {
      try {
        const data = await getJSON<FeedResponse>(`/api/feed?after=${cursor.current}`, ctrl.signal);
        setSource(data.source);
        setError(null);
        setEvents((prev) => {
          const next = mergeEvents(prev, data.events);
          cursor.current = cursorOf(next);
          return next;
        });
      } catch (err) {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err));
      }
      if (!ctrl.signal.aborted) timer = window.setTimeout(tick, FEED_POLL_MS);
    };
    void tick();
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, []);

  return { events, source, error };
}

export function useRoster() {
  const [data, setData] = useState<RosterResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    let timer: number | undefined;
    const tick = async () => {
      try {
        setData(await getJSON<RosterResponse>("/api/roster", ctrl.signal));
        setError(null);
      } catch (err) {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err));
      }
      if (!ctrl.signal.aborted) timer = window.setTimeout(tick, ROSTER_POLL_MS);
    };
    void tick();
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, []);
  return { data, error };
}

export function useOperations() {
  const [data, setData] = useState<OperationsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    let timer: number | undefined;
    const tick = async () => {
      try { setData(await getJSON<OperationsResponse>("/api/operations", ctrl.signal)); setError(null); }
      catch (err) { if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err)); }
      if (!ctrl.signal.aborted) timer = window.setTimeout(tick, FEED_POLL_MS);
    };
    void tick();
    return () => { ctrl.abort(); window.clearTimeout(timer); };
  }, []);
  return { data, error };
}

export function useApprovals() {
  const [data, setData] = useState<ApprovalsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    let timer: number | undefined;
    const tick = async () => {
      try { setData(await getJSON<ApprovalsResponse>("/api/approvals", ctrl.signal)); setError(null); }
      catch (err) { if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err)); }
      if (!ctrl.signal.aborted) timer = window.setTimeout(tick, FEED_POLL_MS);
    };
    void tick();
    return () => { ctrl.abort(); window.clearTimeout(timer); };
  }, []);
  return { data, error };
}

export function useSpend() {
  const [data, setData] = useState<SpendResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    let timer: number | undefined;
    const tick = async () => {
      try { setData(await getJSON<SpendResponse>("/api/spend", ctrl.signal)); setError(null); }
      catch (err) { if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err)); }
      if (!ctrl.signal.aborted) timer = window.setTimeout(tick, SPEND_POLL_MS);
    };
    void tick();
    return () => { ctrl.abort(); window.clearTimeout(timer); };
  }, []);
  return { data, error };
}

/** A JSON endpoint polled every `ms`; the server caches outside sources, so a minute is plenty. */
function usePolled<T>(path: string, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    let timer: number | undefined;
    const tick = async () => {
      try { setData(await getJSON<T>(path, ctrl.signal)); setError(null); }
      catch (err) { if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err)); }
      if (!ctrl.signal.aborted) timer = window.setTimeout(tick, ms);
    };
    void tick();
    return () => { ctrl.abort(); window.clearTimeout(timer); };
  }, [path, ms]);
  return { data, error };
}

export function useChecks() {
  return usePolled<ChecksResponse>("/api/checks", HEALTH_POLL_MS);
}

export function useWatcher() {
  return usePolled<WatcherResponse>("/api/watcher", HEALTH_POLL_MS);
}

/** Re-render every `ms` so relative times stay fresh. */
export function useNow(ms = 10_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}
