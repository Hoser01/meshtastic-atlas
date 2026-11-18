"use client";

import { Activity, Antenna, MapPin, RadioTower, RefreshCw, Search, Server, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

type ObserverHealth = {
  observer_id: string;
  last_heartbeat: string | null;
  observer_node_num?: number | null;
  mux_connected?: number | null;
  delivery_queue_depth?: number | null;
  collector_uptime_seconds?: number | null;
  mux_frames?: number | null;
  connection_epoch?: number | null;
  last_rf_observation?: string | null;
  errors_24h?: number | null;
  heartbeat_age_seconds?: number | null;
  status: "online" | "degraded" | "offline";
  node_id?: string | null;
  short_name?: string | null;
  long_name?: string | null;
  latitude?: number | null;
  longitude?: number | null;
};

const ageLabel = (timestamp: string | null | undefined, asOf: number) => {
  if (!timestamp) return "NEVER";
  const seconds = Math.max(0, Math.floor((asOf - new Date(timestamp).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s AGO`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m AGO`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h AGO`;
  return `${Math.floor(seconds / 86400)}d AGO`;
};

const durationLabel = (seconds?: number | null) => {
  if (seconds == null) return "—";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
};

const observerName = (row: ObserverHealth) => row.short_name && row.long_name
  ? `[${row.short_name}] ${row.long_name}`
  : row.long_name || row.short_name || row.observer_id;

const isRfQuiet = (row: ObserverHealth, asOf: number) => row.status === "online" && (
  !row.last_rf_observation || asOf - new Date(row.last_rf_observation).getTime() > 60 * 60_000
);

export function ObserverCenter({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<ObserverHealth[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asOf, setAsOf] = useState(0);

  useEffect(() => {
    let closed = false;
    const load = async (initial = false) => {
      if (initial) setLoading(true); else setRefreshing(true);
      try {
        const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
        const response = await fetch(`${base}/api/v1/observer-health`, { cache: "no-store" });
        if (!response.ok) throw new Error(`Observer health returned ${response.status}`);
        const next = await response.json() as ObserverHealth[];
        if (closed) return;
        setRows(next);
        setAsOf(Date.now());
        setSelectedId((current) => current && next.some((row) => row.observer_id === current)
          ? current
          : next.find((row) => row.status !== "online")?.observer_id ?? next[0]?.observer_id ?? null);
        setError(null);
      } catch (reason) {
        if (!closed) setError(reason instanceof Error ? reason.message : "Observer health unavailable");
      } finally {
        if (!closed) { setLoading(false); setRefreshing(false); }
      }
    };
    void load(true);
    const timer = window.setInterval(() => void load(false), 15_000);
    return () => { closed = true; window.clearInterval(timer); };
  }, []);

  const sorted = useMemo(() => {
    const rank = { degraded: 0, offline: 1, online: 2 };
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => `${row.observer_id} ${row.node_id ?? ""} ${row.short_name ?? ""} ${row.long_name ?? ""}`.toLowerCase().includes(needle))
      .sort((a, b) => rank[a.status] - rank[b.status] || observerName(a).localeCompare(observerName(b)));
  }, [query, rows]);
  const selected = rows.find((row) => row.observer_id === selectedId) ?? sorted[0] ?? null;
  const counts = {
    online: rows.filter((row) => row.status === "online").length,
    degraded: rows.filter((row) => row.status === "degraded").length,
    offline: rows.filter((row) => row.status === "offline").length,
    rfActive: rows.filter((row) => row.last_rf_observation && asOf - new Date(row.last_rf_observation).getTime() <= 60 * 60_000).length,
    queued: rows.reduce((sum, row) => sum + (row.delivery_queue_depth ?? 0), 0),
    warnings: rows.reduce((sum, row) => sum + (row.errors_24h ?? 0), 0),
  };

  return <div className="help-backdrop observer-backdrop" role="presentation" onClick={onClose}>
    <section className="observer-center glass-panel" role="dialog" aria-modal="true" aria-labelledby="observer-center-title" onClick={(event) => event.stopPropagation()}>
      <button className="detail-close" onClick={onClose} aria-label="Close observers"><X size={17} /></button>
      <header className="observer-center-heading">
        <span className="observer-center-icon"><RadioTower size={20} /></span>
        <span><small>ATLAS NETWORK OPERATIONS</small><h2 id="observer-center-title">Observers</h2><p>Live collector, MUX, delivery, and RF health</p></span>
        <RefreshCw className={refreshing ? "spinning" : ""} size={15} aria-label={refreshing ? "Refreshing" : "Refreshes every 15 seconds"} />
      </header>

      <div className="observer-summary">
        <span><small>REGISTERED</small><strong>{rows.length}</strong></span>
        <span className="online"><small>ONLINE</small><strong>{counts.online}</strong></span>
        <span className="degraded"><small>DEGRADED</small><strong>{counts.degraded}</strong></span>
        <span className="offline"><small>OFFLINE</small><strong>{counts.offline}</strong></span>
        <span><small>RF ACTIVE · 1H</small><strong>{counts.rfActive}</strong></span>
        <span><small>QUEUED</small><strong>{counts.queued}</strong></span>
        <span><small>WARNINGS · 24H</small><strong>{counts.warnings}</strong></span>
      </div>

      <div className="observer-center-body">
        <aside className="observer-directory">
          <label className="observer-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find observer or node…" /></label>
          <div className="observer-directory-label"><span>{sorted.length} OBSERVERS</span><span>HEALTH</span></div>
          <div className="observer-directory-list">
            {loading && <div className="observer-loading"><RefreshCw className="spinning" size={17} /> LOADING OBSERVER HEALTH…</div>}
            {!loading && error && <div className="observer-error"><TriangleAlert size={15} />{error}</div>}
            {!loading && !error && sorted.map((row) => <button key={row.observer_id} className={selected?.observer_id === row.observer_id ? "active" : ""} onClick={() => setSelectedId(row.observer_id)}>
              <i className={`observer-health-dot ${row.status}`} />
              <span><strong>{observerName(row)}</strong><small>{row.observer_id} · {row.node_id ?? "NODE ID UNKNOWN"}</small></span>
              <em>{row.status === "online" && isRfQuiet(row, asOf) ? "QUIET" : row.status.toUpperCase()}</em>
              <time>{ageLabel(row.last_heartbeat, asOf)}</time>
            </button>)}
            {!loading && !error && !sorted.length && <div className="observer-empty">NO OBSERVERS MATCH</div>}
          </div>
        </aside>

        <article className="observer-detail" data-tour="observer-detail">
          {selected ? <>
            <div className="observer-detail-title">
              <span className={`observer-center-icon ${selected.status}`}><Antenna size={20} /></span>
              <span><small>{selected.observer_id}</small><h3>{observerName(selected)}</h3><code>{selected.node_id ?? "NODE ID UNKNOWN"}</code></span>
              <b className={`observer-status-badge ${selected.status}`}>{selected.status.toUpperCase()}</b>
            </div>
            {isRfQuiet(selected, asOf) && <div className="observer-quiet-note"><Activity size={14} /><span><b>COLLECTOR HEALTHY · RF QUIET</b>No unique RF observation has arrived within the last hour.</span></div>}
            <h4>CONNECTION</h4>
            <div className="observer-metrics">
              <span><small>LAST HEARTBEAT</small><strong>{ageLabel(selected.last_heartbeat, asOf)}</strong><em>{selected.last_heartbeat ? new Date(selected.last_heartbeat).toLocaleString() : "Never connected"}</em></span>
              <span><small>MUX</small><strong className={selected.mux_connected ? "good" : "bad"}>{selected.mux_connected ? "CONNECTED" : "DISCONNECTED"}</strong><em>Epoch {selected.connection_epoch ?? "—"}</em></span>
              <span><small>COLLECTOR UPTIME</small><strong>{durationLabel(selected.collector_uptime_seconds)}</strong><em>Since the collector last started</em></span>
              <span><small>MUX FRAMES</small><strong>{selected.mux_frames?.toLocaleString() ?? "—"}</strong><em>Current collector connection</em></span>
            </div>
            <h4>DELIVERY & RF</h4>
            <div className="observer-metrics">
              <span><small>DELIVERY QUEUE</small><strong className={(selected.delivery_queue_depth ?? 0) > 0 ? "warn" : "good"}>{selected.delivery_queue_depth ?? "—"}</strong><em>Events waiting for ATLAS</em></span>
              <span><small>LAST RF OBSERVATION</small><strong>{ageLabel(selected.last_rf_observation, asOf)}</strong><em>Unique direct reception</em></span>
              <span><small>WARNINGS · 24H</small><strong className={(selected.errors_24h ?? 0) > 0 ? "warn" : "good"}>{selected.errors_24h ?? 0}</strong><em>Collector connection errors</em></span>
              <span><small>HEARTBEAT AGE</small><strong>{selected.heartbeat_age_seconds == null ? "—" : `${Math.round(selected.heartbeat_age_seconds)}s`}</strong><em>Offline after 180 seconds</em></span>
            </div>
            <h4>SITE</h4>
            <div className="observer-site">
              <MapPin size={15} /><span><small>REGISTERED LOCATION</small><strong>{selected.latitude != null && selected.longitude != null ? `${selected.latitude.toFixed(5)}, ${selected.longitude.toFixed(5)}` : "LOCATION NOT CONFIGURED"}</strong></span>
              <Server size={15} /><span><small>OBSERVER NODE NUMBER</small><strong>{selected.observer_node_num ?? "UNKNOWN"}</strong></span>
            </div>
          </> : <div className="observer-empty-detail"><RadioTower size={26} /><span>Select an observer to inspect its health.</span></div>}
        </article>
      </div>
      <footer className="observer-center-footer"><span><i className="observer-health-dot online" /> Online</span><span><i className="observer-health-dot degraded" /> Degraded</span><span><i className="observer-health-dot offline" /> Offline</span><p>Automatically refreshes every 15 seconds</p></footer>
    </section>
  </div>;
}
