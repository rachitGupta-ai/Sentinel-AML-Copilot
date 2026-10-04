/**
 * Live-updates WebSocket helpers for the Command_Centre (Req 10.5, 11.1).
 *
 * The backend broadcasts lightweight, sensitive-data-free {@link LiveEvent}s on
 * `/api/stream` after each workflow transition. {@link liveSocketUrl} derives the
 * ws(s) URL from configuration, and {@link useLiveEvents} is a small React hook
 * that subscribes, exposes a connection status, and auto-reconnects with backoff
 * so a dropped socket never leaves the Command_Centre stale without recovery.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { API_BASE_URL } from "@/lib/api";
import type { LiveEvent } from "@/lib/types";

/** Derive the WebSocket URL from explicit config or the REST base URL. */
export function liveSocketUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_BASE_URL;
  const base = (explicit ?? API_BASE_URL).replace(/\/$/, "");
  const ws = base.replace(/^http(s?):\/\//, (_m, s) => `ws${s}://`);
  return `${ws}/api/stream`;
}

export type LiveStatus = "connecting" | "open" | "closed";

export interface UseLiveEventsResult {
  status: LiveStatus;
  /** Most-recent-first buffer of received events (capped). */
  events: LiveEvent[];
  /** The latest event, or null before any arrive — handy for triggering refetch. */
  latest: LiveEvent | null;
}

const MAX_BUFFER = 50;

/**
 * Subscribe to `/api/stream` and surface received {@link LiveEvent}s.
 *
 * Guards: only runs in the browser; reconnects with a bounded backoff when the
 * socket closes; and is a no-op (status `closed`) when `enabled` is false so a
 * view can opt out. Parse failures are ignored defensively — a malformed frame
 * must never crash the dashboard.
 */
export function useLiveEvents(enabled: boolean = true): UseLiveEventsResult {
  const [status, setStatus] = useState<LiveStatus>(enabled ? "connecting" : "closed");
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [latest, setLatest] = useState<LiveEvent | null>(null);
  const retryRef = useRef(0);

  useEffect(() => {
    if (!enabled || typeof window === "undefined") {
      setStatus("closed");
      return;
    }

    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const connect = () => {
      setStatus("connecting");
      try {
        socket = new WebSocket(liveSocketUrl());
      } catch {
        scheduleReconnect();
        return;
      }

      socket.onopen = () => {
        retryRef.current = 0;
        setStatus("open");
      };

      socket.onmessage = (ev) => {
        try {
          const parsed = JSON.parse(ev.data) as LiveEvent;
          setLatest(parsed);
          setEvents((prev) => [parsed, ...prev].slice(0, MAX_BUFFER));
        } catch {
          // Ignore malformed frames.
        }
      };

      socket.onclose = () => {
        setStatus("closed");
        if (!disposed) scheduleReconnect();
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    const scheduleReconnect = () => {
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000);
      retryRef.current += 1;
      reconnectTimer = setTimeout(connect, delay);
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [enabled]);

  return { status, events, latest };
}
