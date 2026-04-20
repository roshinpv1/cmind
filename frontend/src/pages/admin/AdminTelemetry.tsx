import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import {
    Activity,
    Pause,
    Play,
    Trash2,
    Loader2,
    AlertTriangle,
    FolderOpen,
} from "lucide-react";
import { authService } from "../../lib/auth";

interface TelemetrySession {
    run_id: string;
    mtime_iso: string;
    size: number;
}

interface TelemetryEvent {
    ts?: string;
    event?: string;
    run_id?: string;
    component?: string;
    playbook?: string;
    [key: string]: unknown;
}

export default function AdminTelemetry() {
    const user = authService.getUser();
    if (user?.role !== "admin") {
        return <Navigate to="/catalog-search" replace />;
    }

    const [events, setEvents] = useState<TelemetryEvent[]>([]);
    const [sessions, setSessions] = useState<TelemetrySession[]>([]);
    const [telemetryEnabled, setTelemetryEnabled] = useState<boolean | null>(null);
    const [telemetryDir, setTelemetryDir] = useState<string>("");
    const [paused, setPaused] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [pollOk, setPollOk] = useState(true);
    const cursorsRef = useRef<Record<string, number>>({});

    const poll = useCallback(async () => {
        if (paused) return;
        try {
            const res = await fetch("/api/v1/admin/telemetry/poll", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...authService.getAuthHeader(),
                },
                body: JSON.stringify({
                    cursors: cursorsRef.current,
                    session_limit: 60,
                    limit_per_run: 150,
                }),
            });
            if (res.status === 403) {
                setError("Admin privileges required.");
                setPollOk(false);
                return;
            }
            if (!res.ok) {
                setError(`Poll failed (${res.status})`);
                setPollOk(false);
                return;
            }
            setError(null);
            setPollOk(true);
            const data = await res.json();
            setTelemetryEnabled(!!data.telemetry_enabled);
            if (typeof data.telemetry_dir === "string") {
                setTelemetryDir(data.telemetry_dir);
            }
            if (Array.isArray(data.sessions)) {
                setSessions(data.sessions);
            }
            if (data.cursors && typeof data.cursors === "object") {
                cursorsRef.current = { ...cursorsRef.current, ...data.cursors };
            }
            const incoming: TelemetryEvent[] = Array.isArray(data.events) ? data.events : [];
            if (incoming.length > 0) {
                setEvents((prev) => {
                    const next = [...prev, ...incoming];
                    return next.length > 8000 ? next.slice(-8000) : next;
                });
            }
        } catch (e) {
            console.error(e);
            setError("Network error while polling telemetry.");
            setPollOk(false);
        }
    }, [paused]);

    useEffect(() => {
        poll();
        const id = setInterval(poll, 2000);
        return () => clearInterval(id);
    }, [poll]);

    const clearFeed = () => setEvents([]);

    const [filterInput, setFilterInput] = useState("");
    const visibleEvents = useMemo(() => {
        const fr = filterInput.trim().toLowerCase();
        if (!fr) return events;
        return events.filter(
            (e) =>
                String(e.run_id || "")
                    .toLowerCase()
                    .includes(fr) ||
                String(e.event || "")
                    .toLowerCase()
                    .includes(fr) ||
                String(e.component || "")
                    .toLowerCase()
                    .includes(fr)
        );
    }, [events, filterInput]);

    return (
        <div className="max-w-[1600px] mx-auto space-y-6">
            <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
                <div>
                    <div className="flex items-center gap-2 mb-1">
                        <Activity className="h-6 w-6 text-primary" />
                        <h1 className="text-2xl font-black text-gray-900">Live telemetry</h1>
                    </div>
                    <p className="text-sm text-gray-500 max-w-2xl">
                        Aggregated agent events from all runs writing to{" "}
                        <code className="text-xs bg-gray-100 px-1 rounded">
                            {telemetryDir || "CODEMIND_TELEMETRY_DIR"}
                        </code>
                        . Requires{" "}
                        <code className="text-xs bg-gray-100 px-1 rounded">
                            CODEMIND_TELEMETRY_ENABLED=1
                        </code>{" "}
                        on the server.
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <button
                        type="button"
                        onClick={() => setPaused((p) => !p)}
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold border border-gray-200 bg-white hover:bg-gray-50"
                    >
                        {paused ? (
                            <>
                                <Play className="h-4 w-4" /> Resume
                            </>
                        ) : (
                            <>
                                <Pause className="h-4 w-4" /> Pause
                            </>
                        )}
                    </button>
                    <button
                        type="button"
                        onClick={clearFeed}
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold border border-gray-200 bg-white hover:bg-gray-50 text-rose-700"
                    >
                        <Trash2 className="h-4 w-4" /> Clear feed
                    </button>
                </div>
            </div>

            {error && (
                <div className="flex items-center gap-2 text-sm text-rose-700 bg-rose-50 border border-rose-100 rounded-xl px-4 py-3">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    {error}
                </div>
            )}

            {telemetryEnabled === false && (
                <div className="flex items-center gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-100 rounded-xl px-4 py-3">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    Telemetry is disabled on the server. Enable{" "}
                    <code className="text-xs">CODEMIND_TELEMETRY_ENABLED</code> and restart the API.
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
                <div className="lg:col-span-1 space-y-3">
                    <div className="bg-white rounded-2xl border border-gray-200 p-4 shadow-sm">
                        <div className="flex items-center gap-2 mb-3 text-xs font-bold text-gray-500 uppercase tracking-wider">
                            <FolderOpen className="h-4 w-4" />
                            Recent runs
                        </div>
                        <div className="space-y-2 max-h-[420px] overflow-y-auto">
                            {sessions.length === 0 && (
                                <p className="text-xs text-gray-400">No JSONL sessions found yet.</p>
                            )}
                            {sessions.map((s) => (
                                <div
                                    key={s.run_id}
                                    className="text-xs border border-gray-100 rounded-lg p-2 bg-gray-50/80"
                                >
                                    <div className="font-mono font-semibold text-gray-800 truncate" title={s.run_id}>
                                        {s.run_id}
                                    </div>
                                    <div className="text-[10px] text-gray-400 mt-1">
                                        {s.mtime_iso} · {(s.size / 1024).toFixed(1)} KB
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                    <div className="bg-white rounded-2xl border border-gray-200 p-4 shadow-sm">
                        <label className="text-xs font-bold text-gray-500 uppercase tracking-wider block mb-2">
                            Filter stream
                        </label>
                        <input
                            type="text"
                            value={filterInput}
                            onChange={(e) => setFilterInput(e.target.value)}
                            placeholder="run id, event, component…"
                            className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 font-mono"
                        />
                        <p className="text-[10px] text-gray-400 mt-2">
                            Client-side filter on the buffered feed (last ~8000 events).
                        </p>
                    </div>
                </div>

                <div className="lg:col-span-3 bg-gray-950 rounded-2xl border border-gray-800 overflow-hidden shadow-sm min-h-[480px] flex flex-col">
                    <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between gap-2">
                        <span className="text-xs font-bold text-gray-400 uppercase tracking-wider">
                            Event stream
                        </span>
                        <span className="text-xs text-gray-500 tabular-nums">
                            {visibleEvents.length}
                            {visibleEvents.length !== events.length ? ` / ${events.length}` : ""} events
                            {!pollOk && (
                                <Loader2 className="inline h-3 w-3 animate-spin ml-2 text-amber-400" />
                            )}
                        </span>
                    </div>
                    <div className="flex-1 overflow-y-auto p-3 space-y-2 font-mono text-[11px] leading-snug max-h-[70vh]">
                        {visibleEvents.length === 0 && (
                            <p className="text-gray-500 px-1 py-8 text-center">
                                {events.length === 0
                                    ? "Waiting for telemetry events…"
                                    : "No events match the filter."}
                            </p>
                        )}
                        {visibleEvents.map((ev, i) => {
                            const ts = String(ev.ts ?? "");
                            const evt = String(ev.event ?? "");
                            const rid = String(ev.run_id ?? "");
                            const comp = String(ev.component ?? "");
                            const rest = { ...ev };
                            delete rest.ts;
                            delete rest.event;
                            delete rest.run_id;
                            delete rest.component;
                            delete rest.playbook;
                            const detail =
                                Object.keys(rest).length > 0 ? JSON.stringify(rest) : "";
                            return (
                                <div
                                    key={`${ts}-${rid}-${evt}-${i}`}
                                    className="border-b border-gray-800/80 pb-2 last:border-0"
                                >
                                    <div className="flex flex-wrap gap-x-2 gap-y-0.5 text-gray-500">
                                        <span className="text-gray-600 shrink-0">{ts}</span>
                                        <span className="text-violet-300 font-semibold">{evt}</span>
                                        {rid && (
                                            <span className="text-cyan-400/90 truncate max-w-[14rem]" title={rid}>
                                                {rid.slice(0, 8)}…
                                            </span>
                                        )}
                                        {comp && <span className="text-gray-400">{comp}</span>}
                                        {ev.playbook != null && (
                                            <span className="text-emerald-400/80">{String(ev.playbook)}</span>
                                        )}
                                    </div>
                                    {detail ? (
                                        <pre className="mt-1 text-gray-300 whitespace-pre-wrap break-all">
                                            {detail}
                                        </pre>
                                    ) : null}
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
}
