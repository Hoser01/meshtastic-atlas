"use client";

import "maplibre-gl/dist/maplibre-gl.css";

import {
  Activity,
  Antenna,
  CircleHelp,
  Crosshair,
  Eye,
  EyeOff,
  Layers3,
  Menu,
  Minus,
  Pause,
  Play,
  Plus,
  Radio,
  Search,
  Settings2,
  Signal,
  SlidersHorizontal,
  Upload,
  X,
  Zap,
} from "lucide-react";
import {
  AttributionControl,
  Map as MapLibreMap,
  LngLatBounds,
  Marker,
  MercatorCoordinate,
  Popup,
  type GeoJSONSource,
} from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";

type Provenance = "RF OBSERVED" | "REMOTE GATEWAY RF" | "MQTT NETWORK" | "LOCAL TX" | "UNKNOWN";
type ApiProvenance = "RF_OBSERVED" | "REMOTE_GATEWAY_RF" | "MQTT_NETWORK" | "LOCAL_TX" | "UNKNOWN";

const isInvalidZeroPosition = (longitude: number, latitude: number) =>
  Math.abs(longitude) < 0.000001 && Math.abs(latitude) < 0.000001;

type MeshNode = {
  id: string;
  label: string;
  role: string;
  lng: number;
  lat: number;
  hops: number;
  lastSeen: string;
  snr: number;
  rssi: number;
  provenance: Provenance;
  color: string;
  observedAt?: string;
  altitude?: number | null;
  hardwareModel?: string | null;
  shortName?: string | null;
};

const demoNodes: MeshNode[] = [];
const LZ_REGION_BOUNDS: [[number, number], [number, number]] = [[-94.85, 35.82], [-93.02, 37.48]];

type ApiNode = {
  node_num: number;
  observer_id: string;
  observed_at: string;
  source: ApiProvenance;
  latitude: number;
  longitude: number;
  altitude: number | null;
  rx_rssi: number | null;
  rx_snr: number | null;
  long_name: string | null;
  short_name: string | null;
  hardware_model: string | null;
  map_source?: ApiProvenance;
};

type Health = {
  single_observer_mode: boolean;
  transmissions: number;
  observations: number;
  observers: number;
  events: number;
  rf_observations: number;
  remote_rf_observations: number;
  mqtt_packets: number;
  positioned_nodes: number;
};

type ActivityEvent = {
  event_id: string;
  event: string;
  observed_at: string;
  source?: ApiProvenance;
  observer_node_num?: number;
  observer_id?: string;
  mqtt_gateway_id?: string;
  mqtt_classification?: string;
  channel_id?: string;
  evidence?: string[];
  node_direction?: "sent" | "received" | "both";
  packet_id?: number;
  from_node?: number;
  to_node?: number;
  portnum?: string;
  rx_rssi?: number;
  rx_snr?: number;
  hop_start?: number;
  hop_limit?: number;
  position?: { latitude: number; longitude: number };
  node_info?: { long_name?: string; short_name?: string; hardware_model?: string; role?: string; is_licensed?: boolean; is_unmessagable?: boolean };
  device_metadata?: { firmware_version?: string; hardware_model?: string; role?: string; has_wifi?: boolean; has_bluetooth?: boolean; has_ethernet?: boolean; has_remote_hardware?: boolean; has_pki?: boolean };
  lifecycle_status?: string;
  want_response?: boolean;
  request_id?: number;
  reply_id?: number;
  traceroute?: {
    route: number[];
    snr_towards: Array<number | null>;
    route_back: number[];
    snr_back: Array<number | null>;
  };
};

type TimelineData = {
  start: string;
  end: string;
  bins: Array<{ start: string; rf: number; mqtt: number; other: number }>;
};

type NodeSummary = {
  node_num: number;
  node_id: string | null;
  long_name: string | null;
  short_name: string | null;
  hardware_model: string | null;
  role: string | null;
  first_heard: string;
  last_heard: string;
  last_event_id: string;
  last_event_type: string;
  last_source: ApiProvenance;
  last_observer_id: string;
  last_rssi: number | null;
  last_snr: number | null;
  last_packet_seen: string | null;
  sent_observations: number;
  received_observations: number;
  positioned: number;
  latitude: number | null;
  longitude: number | null;
  altitude: number | null;
  position_observed_at: string | null;
  rf_observations: number;
  remote_rf_observations: number;
  mqtt_observations: number;
  display_provenance: ApiProvenance;
  firmware_version: string | null;
  device_state_version: number | null;
  has_wifi: number | null;
  has_bluetooth: number | null;
  has_ethernet: number | null;
  has_remote_hardware: number | null;
  has_pki: number | null;
  is_licensed: number | null;
  is_unmessagable: number | null;
  last_any_seen?: string;
  last_rf_seen?: string | null;
  recently_heard_by?: Array<{
    observer_id: string; observer_node_num: number | null; last_observed_at: string;
    observation_count: number; average_rssi: number | null; average_snr: number | null;
    best_rssi: number | null; node_id: string | null; short_name: string | null; long_name: string | null;
  }>;
};

type QualityMetrics = {
  unique_packets: number;
  repeated_observations: number;
  total_packet_observations: number;
  rf_unique_packets: number;
  mqtt_unique_packets: number;
  active_gateways_1h: number;
  mqtt_decodable: number;
  mqtt_encrypted_unknown: number;
  decrypt_success_percent: number;
  collector_errors: number;
  packet_types: Record<string, number>;
  deduplication_rule: string;
};

type GatewayQuality = {
  observer_id: string; observer_node_num: number; node_id: string;
  short_name: string | null; long_name: string | null; hardware_model: string | null;
  last_report: string; observation_count: number; unique_packets: number;
  repeat_observations: number; repeat_ratio: number; direct_rf_observations: number;
  average_rssi: number | null; average_snr: number | null; decode_success_percent: number;
  latitude: number | null; longitude: number | null; position_age_seconds: number | null;
  trusted_for_coverage: boolean; warnings: string[]; packets?: LogicalPacket[];
};

type LogicalPacket = {
  sender: number; packet_id: number; to_node: number | null; portnum: string;
  first_observed_at: string; last_observed_at: string; provenance: ApiProvenance;
  observation_count: number; repeat_count: number; gateway_count: number;
  rf_observations: number; mqtt_observations: number; decodable_observations: number;
  encrypted_unknown_observations: number; conflicting_destination: boolean;
  conflicting_portnum: boolean; observations?: ActivityEvent[];
};

type QualityWarning = {
  severity: "high" | "medium" | "low"; kind: string; scope: string;
  title: string; detail: string; observed_at: string; observer_node_num?: number;
  observer_id?: string; sender?: number; packet_id?: number;
};

type CoverageMeasurement = {
  event_id: string;
  observer_id: string;
  transmitter_node: number;
  receiver_node: number;
  observed_at: string;
  latitude: number;
  longitude: number;
  receiver_latitude: number | null;
  receiver_longitude: number | null;
  position_observed_at: string;
  rssi: number | null;
  snr: number | null;
  distance_km: number | null;
  age_seconds: number;
  position_age_seconds: number;
  evidence: "DIRECT_RF_OBSERVATION" | "NEIGHBORINFO_SNR";
  confidence: "measured" | "neighbor_report";
};

type ApiObserver = {
  observer_id: string;
  observer_node_num: number;
  name?: string;
  short_name?: string;
  long_name?: string;
  node_id?: string;
  latitude?: number;
  longitude?: number;
  last_observed_at: string;
  average_rssi: number | null;
  average_snr: number | null;
};

const provenanceColor = (source: Provenance) =>
  source === "RF OBSERVED" ? "#ff7a1a" : source === "REMOTE GATEWAY RF" ? "#f5d547" : source === "MQTT NETWORK" ? "#a56bff" : source === "LOCAL TX" ? "#45e06f" : "#789097";

const displayProvenance = (source: ApiProvenance): Provenance =>
  source === "RF_OBSERVED" ? "RF OBSERVED" : source === "REMOTE_GATEWAY_RF" ? "REMOTE GATEWAY RF" : source === "MQTT_NETWORK" ? "MQTT NETWORK" : source === "LOCAL_TX" ? "LOCAL TX" : "UNKNOWN";

function toMeshNode(node: ApiNode): MeshNode {
  const unsigned = node.node_num >>> 0;
  const id = `!${unsigned.toString(16).padStart(8, "0")}`;
  const advertisedName = node.short_name && node.long_name
    ? `[${node.short_name}] ${node.long_name}`
    : node.long_name || node.short_name;
  const knownName = advertisedName || id.toUpperCase();
  return {
    id,
    label: knownName,
    role: `Position via ${node.observer_id}`,
    lng: node.longitude,
    lat: node.latitude,
    hops: 0,
    lastSeen: new Date(node.observed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    snr: node.rx_snr ?? 0,
    rssi: node.rx_rssi ?? 0,
    provenance: displayProvenance(node.map_source ?? node.source),
    color: provenanceColor(displayProvenance(node.map_source ?? node.source)),
    observedAt: node.observed_at,
    altitude: node.altitude,
    hardwareModel: node.hardware_model,
    shortName: node.short_name,
  };
}

function StatusPill({ live }: { live: boolean }) {
  return (
    <span className={`status-pill ${live ? "status-live" : "status-paused"}`}>
      <span className="status-dot" /> {live ? "LIVE" : "PAUSED"}
    </span>
  );
}

export default function Home() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const markers = useRef<Marker[]>([]);
  const hoverPopup = useRef<Popup | null>(null);
  const predictionInput = useRef<HTMLInputElement>(null);
  const layersPanel = useRef<HTMLDetailsElement>(null);
  const locationWarningTimer = useRef<number | undefined>(undefined);
  const summaryRefreshAt = useRef(0);
  const healthRefreshAt = useRef(0);
  const displayedNodesRef = useRef<MeshNode[]>([]);
  const nodeSummariesRef = useRef<NodeSummary[]>([]);
  const animationLinks = useRef<Array<{ from: [number, number]; to: [number, number]; kind: "reception" | "addressed" | "mqtt" | "rf_text" | "traceroute"; pathStyle: "logical" | "confirmed" | "trace"; observedAt: string }>>([]);
  const [selected, setSelected] = useState<MeshNode | null>(null);
  const [selectedNode, setSelectedNode] = useState<NodeSummary | null>(null);
  const [selectedNodeActivity, setSelectedNodeActivity] = useState<ActivityEvent[]>([]);
  const [selectedActivity, setSelectedActivity] = useState<ActivityEvent | null>(null);
  const [liveNodes, setLiveNodes] = useState<MeshNode[]>([]);
  const [positionHistory, setPositionHistory] = useState<ApiNode[]>([]);
  const [coverageMeasurements, setCoverageMeasurements] = useState<CoverageMeasurement[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [timelineData, setTimelineData] = useState<TimelineData | null>(null);
  const [observers, setObservers] = useState<ApiObserver[]>([]);
  const [nodeSummaries, setNodeSummaries] = useState<NodeSummary[]>([]);
  const [quality, setQuality] = useState<QualityMetrics | null>(null);
  const [qualityView, setQualityView] = useState<"gateways" | "packets" | "warnings" | null>(null);
  const [gatewayQuality, setGatewayQuality] = useState<GatewayQuality[]>([]);
  const [logicalPackets, setLogicalPackets] = useState<LogicalPacket[]>([]);
  const [qualityWarnings, setQualityWarnings] = useState<QualityWarning[]>([]);
  const [selectedGateway, setSelectedGateway] = useState<GatewayQuality | null>(null);
  const [selectedLogicalPacket, setSelectedLogicalPacket] = useState<LogicalPacket | null>(null);
  const [followedPacket, setFollowedPacket] = useState<LogicalPacket | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [mapZoom, setMapZoom] = useState(9.2);
  const [drawablePathCount, setDrawablePathCount] = useState(0);
  const [live, setLive] = useState(true);
  const [showRf, setShowRf] = useState(true);
  const [showRemoteRf, setShowRemoteRf] = useState(true);
  const [showMqtt, setShowMqtt] = useState(true);
  const [showNodes, setShowNodes] = useState(true);
  const [showCoverage, setShowCoverage] = useState(false);
  const [showReachability, setShowReachability] = useState(false);
  const [showLinks, setShowLinks] = useState(true);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [showPrediction, setShowPrediction] = useState(false);
  const [predictionName, setPredictionName] = useState<string | null>(null);
  const [selectedTimelineBin, setSelectedTimelineBin] = useState<number | null>(null);
  const [replayEvents, setReplayEvents] = useState<ActivityEvent[] | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [panelOpen, setPanelOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(true);
  const [helpOpen, setHelpOpen] = useState(false);
  const [uiScale, setUiScale] = useState(1.05);
  const [apiHealthy, setApiHealthy] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [qualityViewLoading, setQualityViewLoading] = useState(false);
  const [locationWarning, setLocationWarning] = useState<string | null>(null);
  const showLocationWarning = () => {
    setLocationWarning("LOCATION SET INCORRECTLY · NODE REPORTED 0,0");
    if (locationWarningTimer.current !== undefined) window.clearTimeout(locationWarningTimer.current);
    locationWarningTimer.current = window.setTimeout(() => setLocationWarning(null), 3_500);
  };
  useEffect(() => {
    const stored = Number(window.localStorage.getItem("atlas-ui-scale"));
    if (Number.isFinite(stored) && stored >= 0.9 && stored <= 1.4) setUiScale(stored);
  }, []);
  const observerNodes: MeshNode[] = observers
    .filter((observer) => observer.latitude !== undefined && observer.longitude !== undefined)
    .map((observer) => {
      const id = observer.node_id ?? `!${(observer.observer_node_num >>> 0).toString(16).padStart(8, "0")}`;
      const advertisedName = observer.short_name && observer.long_name
        ? `[${observer.short_name}] ${observer.long_name}`
        : observer.long_name || observer.short_name || id.toUpperCase();
      return ({
      id,
      label: advertisedName,
      role: `Observer · ${observer.short_name ?? observer.observer_id}`,
      lng: observer.longitude!, lat: observer.latitude!, hops: 0,
      lastSeen: new Date(observer.last_observed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      snr: observer.average_snr ?? 0, rssi: observer.average_rssi ?? 0,
      provenance: "LOCAL TX", color: "#45e06f", observedAt: observer.last_observed_at,
    }); });
  const displayedNodes = apiHealthy
    ? [...observerNodes, ...liveNodes.filter((node) => {
        const nodeNum = parseInt(node.id.slice(1), 16) >>> 0;
        const lastPacketSeen = nodeSummaries.find((summary) => summary.node_num === nodeNum)?.last_packet_seen;
        const newestLivePacket = activity.reduce((latest, item) => {
          if (item.packet_id === undefined || ![item.from_node, item.to_node, item.observer_node_num].includes(nodeNum)) return latest;
          return Math.max(latest, new Date(item.observed_at).getTime());
        }, 0);
        const evidenceTime = Math.max(
          node.observedAt ? new Date(node.observedAt).getTime() : 0,
          lastPacketSeen ? new Date(lastPacketSeen).getTime() : 0,
          newestLivePacket,
        );
        const age = evidenceTime ? Date.now() - evidenceTime : Number.POSITIVE_INFINITY;
        const retention = node.provenance === "REMOTE GATEWAY RF" || node.provenance === "MQTT NETWORK"
          ? 24 * 60 * 60_000 : 30 * 24 * 60 * 60_000;
        return age <= retention && !observerNodes.some((observer) => observer.id === node.id);
      })]
    : demoNodes;
  displayedNodesRef.current = displayedNodes;
  nodeSummariesRef.current = nodeSummaries;

  const filteredNodeSummaries = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return nodeSummaries.filter((node) => {
      if (node.display_provenance === "RF_OBSERVED" && !showRf) return false;
      if (node.display_provenance === "REMOTE_GATEWAY_RF" && !showRemoteRf) return false;
      if (node.display_provenance === "MQTT_NETWORK" && !showMqtt) return false;
      if (!query) return true;
      const id = `!${(node.node_num >>> 0).toString(16).padStart(8, "0")}`;
      return `${id} ${node.short_name ?? ""} ${node.long_name ?? ""}`.toLowerCase().includes(query);
    });
  }, [nodeSummaries, searchQuery, showMqtt, showRemoteRf, showRf]);

  const timeline = useMemo(() => {
    if (timelineData) {
      const bins = timelineData.bins.map((bin) => ({ ...bin, start: new Date(bin.start).getTime() }));
      const peak = Math.max(1, ...bins.map((bin) => bin.rf + bin.mqtt + bin.other));
      const format = (timestamp: string) => new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      return { bins, peak, start: new Date(timelineData.start).getTime(), end: new Date(timelineData.end).getTime(), startLabel: format(timelineData.start), endLabel: format(timelineData.end) };
    }
    const end = Date.now();
    const start = end - 15 * 60_000;
    const bins = Array.from({ length: 15 }, (_, index) => ({ rf: 0, mqtt: 0, other: 0, start: start + index * 60_000 }));
    for (const item of activity) {
      const timestamp = new Date(item.observed_at).getTime();
      if (!Number.isFinite(timestamp) || timestamp < start || timestamp > end) continue;
      const bin = Math.min(14, Math.floor((timestamp - start) / 60_000));
      if (item.source === "RF_OBSERVED") bins[bin].rf += 1;
      else if (item.source === "MQTT_NETWORK") bins[bin].mqtt += 1;
      else bins[bin].other += 1;
    }
    const peak = Math.max(1, ...bins.map((bin) => bin.rf + bin.mqtt + bin.other));
    const format = (timestamp: number) => new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return { bins, peak, start, end, startLabel: format(start), endLabel: format(end) };
  }, [activity, timelineData]);

  const selectedTimelineEvents = useMemo(() => {
    if (selectedTimelineBin === null) return activity;
    const start = timeline.bins[selectedTimelineBin]?.start;
    if (start === undefined) return activity;
    return activity.filter((item) => {
      const timestamp = new Date(item.observed_at).getTime();
      return timestamp >= start && timestamp < start + 60_000;
    });
  }, [activity, selectedTimelineBin, timeline]);
  const animationEvents = replayEvents ?? activity;

  const playTimeline = () => {
    if (selectedTimelineBin === null) {
      setLive(!live);
      return;
    }
    const startedAt = Date.now();
    setReplayEvents(selectedTimelineEvents.map((item, index) => ({
      ...item,
      observed_at: new Date(startedAt + Math.min(index * 120, 2_000)).toISOString(),
    })));
    window.setTimeout(() => setReplayEvents(null), 15_500);
    setLive(true);
  };

  useEffect(() => {
    const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
    let closed = false;
    let refreshing = false;
    let refreshPending = false;
    let refreshTimer: number | undefined;
    const refresh = async () => {
      if (refreshing) {
        refreshPending = true;
        return;
      }
      refreshing = true;
      try {
        const [nodeResponse, activityResponse, observerResponse, timelineResponse] = await Promise.all([
          fetch(`${base}/api/v1/nodes?limit=500`),
          fetch(`${base}/api/v1/activity?limit=250`),
          fetch(`${base}/api/v1/observers`),
          fetch(`${base}/api/v1/activity/timeline?minutes=15`),
        ]);
        if (!nodeResponse.ok || !activityResponse.ok || !observerResponse.ok || !timelineResponse.ok) throw new Error("ATLAS API unavailable");
        if (closed) return;
        setApiHealthy(true);
        const positionedNodes = ((await nodeResponse.json()) as ApiNode[]).map(toMeshNode);
        setActivity((await activityResponse.json()) as ActivityEvent[]);
        setObservers((await observerResponse.json()) as ApiObserver[]);
        setTimelineData((await timelineResponse.json()) as TimelineData);
        setLiveNodes(positionedNodes);
        setInitialLoading(false);
        setSelected((current) => current
          ? positionedNodes.find((node) => node.id === current.id) ?? current
          : null);
        const now = Date.now();
        const enrichmentRequests: Array<Promise<void>> = [];
        if (now >= summaryRefreshAt.current) {
          summaryRefreshAt.current = now + 30_000;
          enrichmentRequests.push(fetch(`${base}/api/v1/node-summaries?limit=1000`)
            .then((response) => response.ok ? response.json() as Promise<NodeSummary[]> : Promise.reject())
            .then((summaries) => {
              if (closed) return;
              setNodeSummaries(summaries);
              setSelectedNode((current) => {
                if (!current) return null;
                const refreshed = summaries.find((node) => node.node_num === current.node_num);
                return refreshed ? { ...current, ...refreshed } : current;
              });
            }));
        }
        if (now >= healthRefreshAt.current) {
          healthRefreshAt.current = now + 30_000;
          enrichmentRequests.push(fetch(`${base}/api/v1/health`)
            .then((response) => response.ok ? response.json() as Promise<Health> : Promise.reject())
            .then((data) => { if (!closed) setHealth(data); }));
        }
        await Promise.allSettled(enrichmentRequests);
      } catch {
        if (!closed) {
          setApiHealthy(false);
          setInitialLoading(false);
        }
      } finally {
        refreshing = false;
        if (refreshPending && !closed) {
          refreshPending = false;
          refreshTimer = window.setTimeout(() => {
            refreshTimer = undefined;
            void refresh();
          }, 5_000);
        }
      }
    };
    const scheduleRefresh = () => {
      refreshPending = true;
      if (refreshing || refreshTimer !== undefined) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined;
        refreshPending = false;
        void refresh();
      }, 5_000);
    };
    void refresh();
    const stream = new EventSource(`${base}/api/v1/live`);
    stream.onmessage = scheduleRefresh;
    ["rf_observation", "network_packet", "local_transmission", "unclassified_packet",
      "observer_connected", "observer_connection_error"].forEach((eventName) =>
      stream.addEventListener(eventName, scheduleRefresh),
    );
    return () => {
      closed = true;
      stream.close();
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
    };
  }, []);

  useEffect(() => {
    if (!showHeatmap && !showCoverage && !showReachability) return;
    const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
    const controller = new AbortController();
    const loadHistorical = async () => {
      try {
        const requests: Array<Promise<void>> = [];
        if (showHeatmap) requests.push(
          fetch(`${base}/api/v1/positions?limit=2000`, { signal: controller.signal })
            .then((response) => response.ok ? response.json() as Promise<ApiNode[]> : Promise.reject())
            .then(setPositionHistory),
        );
        if (showCoverage || showReachability) requests.push(
          fetch(`${base}/api/v1/coverage/measurements?limit=10000&include_neighbors=true`, { signal: controller.signal })
            .then((response) => response.ok ? response.json() as Promise<CoverageMeasurement[]> : Promise.reject())
            .then(setCoverageMeasurements),
        );
        await Promise.all(requests);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) return;
      }
    };
    void loadHistorical();
    const timer = window.setInterval(() => void loadHistorical(), 60_000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [showCoverage, showHeatmap, showReachability]);

  useEffect(() => {
    if (!qualityView) return;
    const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
    const controller = new AbortController();
    const path = qualityView === "gateways" ? "/api/v1/quality/gateways?hours=24"
      : qualityView === "packets" ? "/api/v1/quality/packets?hours=24&limit=500"
      : "/api/v1/quality/warnings?hours=24&limit=500";
    setQualityViewLoading(true);
    fetch(`${base}${path}`, { signal: controller.signal }).then((response) => response.json()).then((data) => {
      if (qualityView === "gateways") setGatewayQuality(data as GatewayQuality[]);
      else if (qualityView === "packets") setLogicalPackets(data as LogicalPacket[]);
      else setQualityWarnings(data as QualityWarning[]);
    }).catch(() => undefined).finally(() => setQualityViewLoading(false));
    return () => controller.abort();
  }, [qualityView]);

  const openQualityView = (view: "gateways" | "packets" | "warnings") => {
    setQualityView(view); setSelectedGateway(null); setSelectedLogicalPacket(null);
    setSelectedNode(null); setSelected(null); setSelectedActivity(null); setDetailOpen(false);
  };

  const inspectGateway = (gateway: GatewayQuality) => {
    const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
    fetch(`${base}/api/v1/quality/gateways/${gateway.observer_node_num}?hours=24`)
      .then((response) => response.json()).then((data) => setSelectedGateway(data as GatewayQuality))
      .catch(() => setSelectedGateway(gateway));
  };

  const inspectLogicalPacket = (packet: LogicalPacket) => {
    const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
    fetch(`${base}/api/v1/quality/packets/${packet.sender}/${packet.packet_id}`)
      .then((response) => response.json()).then((data) => setSelectedLogicalPacket(data as LogicalPacket))
      .catch(() => setSelectedLogicalPacket(packet));
  };

  const followLogicalPacket = (packet: LogicalPacket) => {
    const focus = (detail: LogicalPacket) => {
      setSelectedLogicalPacket(detail);
      setFollowedPacket(detail);
      setShowLinks(true);
    };
    if (packet.observations) { focus(packet); return; }
    const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
    fetch(`${base}/api/v1/quality/packets/${packet.sender}/${packet.packet_id}`)
      .then((response) => {
        if (!response.ok) throw new Error("Packet evidence unavailable");
        return response.json() as Promise<LogicalPacket>;
      }).then(focus).catch(() => undefined);
  };

  useEffect(() => {
    const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
    const controller = new AbortController();
    const load = () => fetch(`${base}/api/v1/quality`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<QualityMetrics> : Promise.reject())
      .then(setQuality)
      .catch(() => undefined);
    const initialTimer = window.setTimeout(() => void load(), 2_500);
    const timer = window.setInterval(() => void load(), 30_000);
    return () => { window.clearTimeout(initialTimer); window.clearInterval(timer); controller.abort(); };
  }, []);

  useEffect(() => {
    if (!selectedNode) {
      setSelectedNodeActivity([]);
      return;
    }
    const base = (process.env.NEXT_PUBLIC_ATLAS_API_URL || window.location.origin).replace(/\/$/, "");
    const controller = new AbortController();
    const load = () => fetch(`${base}/api/v1/nodes/${selectedNode.node_num}/activity?limit=250`, { signal: controller.signal })
        .then((response) => {
          if (!response.ok) throw new Error("Node activity unavailable");
          return response.json() as Promise<ActivityEvent[]>;
        })
        .then(setSelectedNodeActivity)
        .catch((error: unknown) => {
          if (!(error instanceof DOMException && error.name === "AbortError")) setSelectedNodeActivity([]);
        });
    void load();
    fetch(`${base}/api/v1/nodes/${selectedNode.node_num}`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<NodeSummary> : Promise.reject())
      .then(setSelectedNode).catch(() => undefined);
    const timer = window.setInterval(() => void load(), 5_000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [selectedNode?.node_num]);

  useEffect(() => {
    if (!mapReady || !map.current) return;
    const source = map.current.getSource("positioned-node-activity") as GeoJSONSource | undefined;
    const observer = observerNodes[0];
    const distanceKm = (point: ApiNode) => {
      if (!observer) return 0;
      const radians = (value: number) => value * Math.PI / 180;
      const dLat = radians(point.latitude - observer.lat);
      const dLon = radians(point.longitude - observer.lng);
      const a = Math.sin(dLat / 2) ** 2 + Math.cos(radians(observer.lat)) * Math.cos(radians(point.latitude)) * Math.sin(dLon / 2) ** 2;
      return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    };
    source?.setData({
      type: "FeatureCollection",
      features: positionHistory.filter((point) => point.source === "RF_OBSERVED"
        && Date.now() - new Date(point.observed_at).getTime() <= 24 * 60 * 60_000).map((point) => ({
        type: "Feature" as const,
        properties: {
          node_id: `!${(point.node_num >>> 0).toString(16).padStart(8, "0")}`,
          provenance: "RF OBSERVED",
          signal: Math.max(0.12, Math.min(1, ((point.rx_rssi ?? -110) + 120) / 90)),
          recency: Math.max(0.05, 2 ** (-(Date.now() - new Date(point.observed_at).getTime()) / (4 * 60 * 60_000))),
          rssi: point.rx_rssi ?? -110,
          distance_km: distanceKm(point),
        },
        geometry: { type: "Point" as const, coordinates: [point.longitude, point.latitude] },
      })),
    });
    const coverageSource = map.current.getSource("rf-coverage-measurements") as GeoJSONSource | undefined;
    if (coverageSource) {
      coverageSource.setData({
        type: "FeatureCollection",
        features: coverageMeasurements.filter((sample) => sample.confidence === "measured"
          && sample.age_seconds <= 30 * 24 * 60 * 60).map((sample) => {
          const signal = sample.rssi !== null
            ? Math.max(0.08, Math.min(1, (sample.rssi + 120) / 70))
            : Math.max(0.05, Math.min(0.42, ((sample.snr ?? -15) + 20) / 60));
          const recency = Math.max(0.04, 2 ** (-sample.age_seconds / (7 * 24 * 60 * 60)));
          return ({
          type: "Feature" as const,
          properties: {
            signal, recency, weight: signal * recency,
            rssi: sample.rssi ?? -120, snr: sample.snr ?? -20,
            evidence: sample.evidence, distance_km: sample.distance_km ?? 0,
          },
          geometry: { type: "Point" as const, coordinates: [sample.longitude, sample.latitude] },
        }); }),
      });
    }
    const reachabilitySource = map.current.getSource("mesh-reachability") as GeoJSONSource | undefined;
    if (reachabilitySource) {
      const newest = new Map<string, CoverageMeasurement>();
      for (const sample of coverageMeasurements.filter((item) => item.evidence === "NEIGHBORINFO_SNR"
        && item.receiver_latitude !== null && item.receiver_longitude !== null)) {
        const key = [sample.transmitter_node, sample.receiver_node].sort((a, b) => a - b).join(":");
        if (!newest.has(key)) newest.set(key, sample);
      }
      reachabilitySource.setData({
        type: "FeatureCollection",
        features: [...newest.values()].map((sample) => ({
          type: "Feature" as const,
          properties: { evidence: sample.evidence, snr: sample.snr ?? -20, age_seconds: sample.age_seconds },
          geometry: { type: "LineString" as const, coordinates: [
            [sample.longitude, sample.latitude],
            [sample.receiver_longitude!, sample.receiver_latitude!],
          ] },
        })),
      });
    }
  }, [mapReady, observerNodes, positionHistory, coverageMeasurements]);

  useEffect(() => {
    if (!mapReady || !map.current) return;
    const visibility = (visible: boolean) => visible ? "visible" : "none";
    for (const layer of ["rf-coverage-heatmap"]) {
      if (map.current.getLayer(layer)) map.current.setLayoutProperty(layer, "visibility", visibility(showCoverage));
    }
    if (map.current.getLayer("rf-measurement-points")) map.current.setLayoutProperty("rf-measurement-points", "visibility", visibility(showCoverage));
    if (map.current.getLayer("mesh-reachability")) map.current.setLayoutProperty("mesh-reachability", "visibility", visibility(showReachability));
    if (map.current.getLayer("rf-heatmap")) map.current.setLayoutProperty("rf-heatmap", "visibility", visibility(showHeatmap));
    for (const layer of ["terrain-prediction-fill", "terrain-prediction-line"]) {
      if (map.current.getLayer(layer)) map.current.setLayoutProperty(layer, "visibility", visibility(showPrediction));
    }
    for (const layer of ["node-clusters", "node-cluster-count", "node-points-activity-glow", "node-points-wide", "node-points-hit"]) {
      if (map.current.getLayer(layer)) map.current.setLayoutProperty(layer, "visibility", visibility(showNodes));
    }
    for (const layer of ["recent-rf-links-glow", "recent-rf-links", "recent-rf-links-confirmed", "recent-rf-links-trace", "rf-particles-glow", "rf-particles"]) {
      if (map.current.getLayer(layer)) map.current.setLayoutProperty(layer, "visibility", visibility(showLinks));
    }
  }, [mapReady, showCoverage, showHeatmap, showLinks, showNodes, showPrediction, showReachability]);

  useEffect(() => {
    if (!mapContainer.current || map.current) return;
    const cartoKey = process.env.NEXT_PUBLIC_CARTO_API_KEY;
    const cartoQuery = cartoKey ? `?key=${encodeURIComponent(cartoKey)}` : "";
    let instance: MapLibreMap;
    try {
      instance = new MapLibreMap({
        container: mapContainer.current,
        bounds: LZ_REGION_BOUNDS,
        fitBoundsOptions: { padding: 45, maxZoom: 9.2 },
        attributionControl: false,
        style: {
          version: 8,
          glyphs: "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf",
          sources: {
            dark: {
              type: "raster",
              tiles: [
                `https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png${cartoQuery}`,
                `https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png${cartoQuery}`,
              ],
              tileSize: 512,
              attribution: "© OpenStreetMap contributors © CARTO",
            },
          },
          layers: [{ id: "dark", type: "raster", source: "dark", minzoom: 0, maxzoom: 20 }],
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "MapLibre failed to initialize";
      queueMicrotask(() => setMapError(message));
      return;
    }
    instance.on("error", (event) => {
      const message = event.error?.message;
      if (message && !message.includes("tile")) setMapError(message);
    });
    instance.on("load", () => {
      instance.resize();
      instance.addSource("recent-rf-links", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addSource("rf-particles", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addSource("follow-packet-lines", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addSource("follow-packet-nodes", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addSource("positioned-node-activity", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addSource("rf-coverage-measurements", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addSource("terrain-prediction", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addSource("mesh-reachability", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addSource("atlas-nodes", {
        type: "geojson", cluster: true, clusterMaxZoom: 8, clusterRadius: 24,
        clusterProperties: {
          direct_count: ["+", ["case", ["==", ["get", "provenance"], "RF OBSERVED"], 1, 0]],
          remote_count: ["+", ["case", ["==", ["get", "provenance"], "REMOTE GATEWAY RF"], 1, 0]],
          mqtt_count: ["+", ["case", ["==", ["get", "provenance"], "MQTT NETWORK"], 1, 0]],
        },
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addLayer({
        id: "node-clusters", type: "circle", source: "atlas-nodes", maxzoom: 9,
        filter: ["has", "point_count"],
        paint: {
          "circle-color": ["case",
            ["all", [">=", ["get", "remote_count"], ["get", "direct_count"]], [">=", ["get", "remote_count"], ["get", "mqtt_count"]]], "rgba(51,45,10,.94)",
            "rgba(35,19,10,.94)"],
          "circle-radius": ["step", ["get", "point_count"], 13, 10, 16, 30, 20],
          "circle-stroke-color": ["case",
            ["all", [">=", ["get", "remote_count"], ["get", "direct_count"]], [">=", ["get", "remote_count"], ["get", "mqtt_count"]]], "#f5d547",
            "#ff7a1a"],
          "circle-stroke-width": 1.35,
          "circle-opacity": 0.92,
        },
      });
      instance.addLayer({
        id: "node-cluster-count", type: "symbol", source: "atlas-nodes", maxzoom: 9,
        filter: ["has", "point_count"],
        layout: {
          "text-field": ["get", "point_count_abbreviated"],
          "text-font": ["Open Sans Regular"],
          "text-size": 10,
          "text-allow-overlap": true,
          "text-ignore-placement": true,
          "symbol-sort-key": 100,
        },
        paint: { "text-color": "#f1eee9" },
      });
      instance.addLayer({
        id: "node-points-activity-glow", type: "circle", source: "atlas-nodes", maxzoom: 10.5,
        filter: ["all", ["!", ["has", "point_count"]], ["!=", ["get", "activity"], "idle"]],
        paint: {
          "circle-radius": ["match", ["get", "activity"], "active", 9, 6],
          "circle-color": ["get", "color"],
          "circle-opacity": ["match", ["get", "activity"], "active", 0.28, 0.12],
          "circle-blur": 0.75,
        },
      });
      instance.addLayer({
        id: "node-points-wide", type: "circle", source: "atlas-nodes", maxzoom: 10.5,
        filter: ["!", ["has", "point_count"]],
        paint: {
          "circle-radius": ["match", ["get", "activity"], "active", 4.25, "recent", 3.85, 3.5],
          "circle-color": ["get", "color"],
          "circle-opacity": ["match", ["get", "activity"], "active", 1, "recent", 0.95, 0.78],
          "circle-stroke-color": "#071116",
          "circle-stroke-width": ["match", ["get", "activity"], "active", 1.25, 1],
        },
      });
      instance.addLayer({
        id: "node-points-hit", type: "circle", source: "atlas-nodes", maxzoom: 10.5,
        filter: ["!", ["has", "point_count"]],
        paint: { "circle-radius": 14, "circle-color": "rgba(0,0,0,0)", "circle-opacity": 0 },
      });
      instance.addLayer({
        id: "terrain-prediction-fill", type: "fill", source: "terrain-prediction",
        layout: { visibility: "none" },
        paint: {
          "fill-color": ["coalesce", ["get", "fill"], ["get", "color"], "#54e4fb"],
          "fill-opacity": 0.28,
        },
      });
      instance.addLayer({
        id: "terrain-prediction-line", type: "line", source: "terrain-prediction",
        layout: { visibility: "none" },
        paint: { "line-color": ["coalesce", ["get", "stroke"], ["get", "color"], "#54e4fb"], "line-width": 0.7, "line-opacity": 0.42 },
      });
      instance.addLayer({
        id: "rf-coverage-heatmap", type: "heatmap", source: "rf-coverage-measurements", maxzoom: 14,
        layout: { visibility: "none" },
        paint: {
          "heatmap-weight": ["get", "weight"],
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 5, 0.7, 12, 1.35],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 5, 18, 12, 42],
          "heatmap-opacity": 0.58,
          "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, "rgba(0,0,0,0)", 0.08, "rgba(28,78,255,.18)", 0.24, "rgba(0,210,255,.36)", 0.43, "rgba(69,224,111,.48)", 0.64, "rgba(245,213,71,.62)", 0.82, "rgba(255,122,26,.74)", 1, "rgba(255,44,44,.82)"],
        },
      });
      instance.addLayer({
        id: "rf-measurement-points", type: "circle", source: "rf-coverage-measurements",
        layout: { visibility: "none" },
        paint: {
          "circle-radius": 3,
          "circle-color": ["interpolate", ["linear"], ["get", "rssi"], -120, "#ef4444", -100, "#ff7a1a", -85, "#f5d547", -70, "#45e06f", -45, "#54e4fb"],
          "circle-opacity": ["match", ["get", "evidence"], "DIRECT_RF_OBSERVATION", 0.82, 0.35],
          "circle-stroke-color": "#061014", "circle-stroke-width": 0.75,
        },
      });
      instance.addLayer({
        id: "mesh-reachability", type: "line", source: "mesh-reachability",
        layout: { visibility: "none" },
        paint: {
          "line-color": "#a56bff", "line-width": 1.1, "line-opacity": 0.52,
          "line-dasharray": [1.5, 2.5],
        },
      });
      instance.addLayer({
        id: "rf-heatmap", type: "heatmap", source: "positioned-node-activity", maxzoom: 13,
        layout: { visibility: "none" },
        paint: {
          "heatmap-weight": ["*", ["get", "signal"], ["get", "recency"]],
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 5, 0.65, 11, 1.15],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 5, 24, 11, 54],
          "heatmap-opacity": 0.62,
          "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, "rgba(0,0,255,0)", 0.08, "rgba(0,70,255,.28)", 0.2, "rgba(0,210,255,.48)", 0.38, "rgba(0,255,125,.58)", 0.58, "rgba(245,235,40,.7)", 0.78, "rgba(255,122,26,.8)", 1, "rgba(255,25,25,.9)"],
        },
      });
      instance.addLayer({
        id: "recent-rf-links-glow", type: "line", source: "recent-rf-links",
        paint: { "line-color": ["match", ["get", "kind"], "reception", "#ff7a1a", "mqtt", "#a56bff", "traceroute", "#54e4fb", "#45e06f"], "line-width": 4, "line-opacity": 0.1, "line-blur": 4 },
      });
      instance.addLayer({
        id: "recent-rf-links", type: "line", source: "recent-rf-links",
        filter: ["==", ["get", "path_style"], "logical"],
        paint: { "line-color": ["match", ["get", "kind"], "reception", "#ff7a1a", "mqtt", "#a56bff", "#45e06f"], "line-width": 1.25, "line-opacity": 0.62, "line-dasharray": [2, 2] },
      });
      instance.addLayer({
        id: "recent-rf-links-confirmed", type: "line", source: "recent-rf-links",
        filter: ["==", ["get", "path_style"], "confirmed"],
        paint: { "line-color": ["match", ["get", "kind"], "reception", "#ff7a1a", "mqtt", "#a56bff", "#45e06f"], "line-width": 1.5, "line-opacity": 0.76 },
      });
      instance.addLayer({
        id: "recent-rf-links-trace", type: "line", source: "recent-rf-links",
        filter: ["==", ["get", "path_style"], "trace"],
        paint: { "line-color": "#54e4fb", "line-width": 1.4, "line-opacity": 0.78 },
      });
      instance.addLayer({
        id: "rf-particles-glow", type: "circle", source: "rf-particles",
        paint: { "circle-color": ["match", ["get", "kind"], "reception", "#ff7a1a", "mqtt", "#a56bff", "traceroute", "#54e4fb", "#45e06f"], "circle-radius": 5, "circle-opacity": 0.2, "circle-blur": 0.75 },
      });
      instance.addLayer({
        id: "rf-particles", type: "circle", source: "rf-particles",
        paint: { "circle-color": ["match", ["get", "kind"], "reception", "#ffb066", "mqtt", "#d6a8ff", "traceroute", "#8cecff", "#74f39a"], "circle-radius": 2.25, "circle-stroke-width": 0.65, "circle-stroke-color": "#ffffff", "circle-opacity": 0.92 },
      });
      instance.addLayer({
        id: "follow-packet-lines-glow", type: "line", source: "follow-packet-lines",
        paint: { "line-color": "#ff7a1a", "line-width": 8, "line-opacity": 0.18, "line-blur": 5 },
      });
      instance.addLayer({
        id: "follow-packet-lines", type: "line", source: "follow-packet-lines",
        paint: { "line-color": "#ff9b4a", "line-width": 2.2, "line-opacity": 0.9 },
      });
      instance.addLayer({
        id: "follow-packet-nodes", type: "circle", source: "follow-packet-nodes",
        paint: {
          "circle-radius": ["match", ["get", "role"], "sender", 9, "destination", 7, 6],
          "circle-color": ["match", ["get", "role"], "sender", "#45e06f", "destination", "#54e4fb", "#ff7a1a"],
          "circle-opacity": 0.95, "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.4,
        },
      });
      setMapReady(true);
      setMapError(null);
    });
    instance.on("zoomend", () => setMapZoom(instance.getZoom()));
    instance.on("click", "node-clusters", async (event) => {
      const feature = event.features?.[0];
      const clusterId = Number(feature?.properties?.cluster_id);
      if (!feature || feature.geometry.type !== "Point" || !Number.isFinite(clusterId)) return;
      const coordinates = feature.geometry.coordinates as [number, number];
      if (isInvalidZeroPosition(coordinates[0], coordinates[1])) {
        showLocationWarning();
        return;
      }
      const source = instance.getSource("atlas-nodes") as GeoJSONSource | undefined;
      if (!source) return;
      try {
        const expansionZoom = await source.getClusterExpansionZoom(clusterId);
        instance.easeTo({
          center: feature.geometry.coordinates as [number, number],
          zoom: Math.min(expansionZoom, instance.getZoom() + 2.25),
          duration: 950,
          easing: (time) => 1 - (1 - time) ** 3,
          essential: true,
        });
      } catch {
        instance.easeTo({
          center: feature.geometry.coordinates as [number, number],
          zoom: instance.getZoom() + 1.5,
          duration: 950,
          easing: (time) => 1 - (1 - time) ** 3,
          essential: true,
        });
      }
    });
    instance.on("mouseenter", "node-clusters", () => { instance.getCanvas().style.cursor = "zoom-in"; });
    instance.on("mouseleave", "node-clusters", () => { instance.getCanvas().style.cursor = ""; });
    instance.on("click", "node-points-hit", (event) => {
      const id = event.features?.[0]?.properties?.id;
      const node = displayedNodesRef.current.find((item) => item.id === id);
      if (node) {
        const summary = nodeSummariesRef.current.find((item) => `!${(item.node_num >>> 0).toString(16).padStart(8, "0")}` === id);
        setSelected(node); setSelectedNode(summary ?? null); setSelectedActivity(null); setDetailOpen(true);
        if (isInvalidZeroPosition(node.lng, node.lat)) {
          showLocationWarning();
          return;
        }
        instance.flyTo({ center: [node.lng, node.lat], zoom: Math.max(instance.getZoom(), 12), duration: 650 });
      }
    });
    instance.on("mouseenter", "node-points-hit", (event) => {
      instance.getCanvas().style.cursor = "pointer";
      const feature = event.features?.[0];
      if (!feature || feature.geometry.type !== "Point") return;
      hoverPopup.current?.remove();
      hoverPopup.current = new Popup({ closeButton: false, closeOnClick: false, offset: 10, className: "node-hover-popup" })
        .setLngLat(feature.geometry.coordinates as [number, number])
        .setText(String(feature.properties?.label ?? feature.properties?.id ?? "NODE"))
        .addTo(instance);
    });
    instance.on("mouseleave", "node-points-hit", () => {
      instance.getCanvas().style.cursor = "";
      hoverPopup.current?.remove();
      hoverPopup.current = null;
    });
    instance.addControl(new AttributionControl({ compact: true }), "bottom-right");
    map.current = instance;
    requestAnimationFrame(() => instance.resize());
    return () => {
      markers.current.forEach((marker) => marker.remove());
      hoverPopup.current?.remove();
      instance.remove();
      map.current = null;
      setMapReady(false);
    };
  }, []);

  useEffect(() => {
    if (!mapReady || !map.current) return;
    const source = map.current.getSource("atlas-nodes") as GeoJSONSource | undefined;
    const now = Date.now();
    const latestActivity = new Map<string, number>();
    for (const item of activity) {
      if (item.source !== "RF_OBSERVED" && item.source !== "MQTT_NETWORK" && item.source !== "LOCAL_TX") continue;
      const timestamp = new Date(item.observed_at).getTime();
      for (const nodeNum of [item.from_node, item.to_node, item.observer_node_num]) {
        if (nodeNum === undefined) continue;
        const id = `!${(nodeNum >>> 0).toString(16).padStart(8, "0")}`;
        latestActivity.set(id, Math.max(timestamp, latestActivity.get(id) ?? 0));
      }
    }
    source?.setData({
      type: "FeatureCollection",
      features: displayedNodes
        .filter((node) => node.provenance !== "RF OBSERVED" || showRf)
        .filter((node) => node.provenance !== "REMOTE GATEWAY RF" || showRemoteRf)
        .filter((node) => node.provenance !== "MQTT NETWORK" || showMqtt)
        .map((node) => {
        const activityAge = now - (latestActivity.get(node.id) ?? 0);
        const activityLevel = activityAge <= 15_000 ? "active" : activityAge <= 15 * 60_000 ? "recent" : "idle";
        return {
          type: "Feature" as const,
          properties: { id: node.id, label: node.label, color: node.color, activity: activityLevel, provenance: node.provenance },
          geometry: { type: "Point" as const, coordinates: [node.lng, node.lat] },
        };
      }),
    });
  }, [activity, displayedNodes, mapReady, showMqtt, showRemoteRf, showRf]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    markers.current.forEach((marker) => marker.remove());
    markers.current = [];
    if (!showNodes) return;
    const normalizedQuery = searchQuery.trim().toLowerCase();
    displayedNodes
      .filter((node) => node.provenance !== "MQTT NETWORK" || showMqtt)
      .filter((node) => node.provenance !== "REMOTE GATEWAY RF" || showRemoteRf)
      .filter((node) => node.provenance !== "RF OBSERVED" || showRf)
      .filter((node) => !normalizedQuery || `${node.id} ${node.label} ${node.shortName ?? ""}`.toLowerCase().includes(normalizedQuery))
      .forEach((node) => {
        const el = document.createElement("button");
        const activeEvents = activity.filter((item) =>
          (item.source === "RF_OBSERVED" || item.source === "MQTT_NETWORK" || item.source === "LOCAL_TX")
          && Date.now() - new Date(item.observed_at).getTime() < 15_000,
        );
        const isEndpoint = activeEvents.some((item) =>
          [item.from_node, item.to_node, item.observer_node_num].some((nodeNum) => nodeNum !== undefined
            && `!${(nodeNum >>> 0).toString(16).padStart(8, "0")}` === node.id),
        );
        const isRecentlyActive = activity.some((item) =>
          (item.source === "RF_OBSERVED" || item.source === "MQTT_NETWORK" || item.source === "LOCAL_TX")
          && Date.now() - new Date(item.observed_at).getTime() < 15 * 60_000
          && [item.from_node, item.to_node, item.observer_node_num].some((nodeNum) => nodeNum !== undefined
            && `!${(nodeNum >>> 0).toString(16).padStart(8, "0")}` === node.id),
        );
        const isActiveObserver = observerNodes.some((observer) => observer.id === node.id) && activeEvents.length > 0;
        if (mapZoom < 10 && !isEndpoint) return;
        el.className = `mesh-marker ${observerNodes.some((observer) => observer.id === node.id) ? "observer-marker" : ""} ${isRecentlyActive ? "recent-activity" : ""} ${isEndpoint ? "packet-active" : ""}`;
        el.style.zIndex = isEndpoint ? "1000" : isActiveObserver ? "100" : "1";
        el.style.setProperty("--marker-color", node.color);
        el.setAttribute("aria-label", `${node.label}, ${node.role}`);
        const core = document.createElement("span");
        core.className = "marker-core";
        const label = document.createElement("span");
        label.className = "marker-label";
        label.textContent = node.label;
        el.appendChild(core);
        el.appendChild(label);
        el.onclick = () => {
          const summary = nodeSummaries.find((item) => `!${(item.node_num >>> 0).toString(16).padStart(8, "0")}` === node.id);
          setSelected(node); setSelectedNode(summary ?? null); setSelectedActivity(null); setDetailOpen(true);
          if (isInvalidZeroPosition(node.lng, node.lat)) {
            showLocationWarning();
            return;
          }
          instance.flyTo({ center: [node.lng, node.lat], zoom: Math.max(instance.getZoom(), 13), duration: 650 });
        };
        markers.current.push(new Marker({ element: el, anchor: "center" }).setLngLat([node.lng, node.lat]).addTo(instance));
      });
  }, [activity, apiHealthy, displayedNodes, mapReady, mapZoom, nodeSummaries, observerNodes, searchQuery, showMqtt, showNodes, showRemoteRf, showRf]);

  useEffect(() => {
    if (!observerNodes.length) {
      animationLinks.current = [];
      setDrawablePathCount(0);
      return;
    }
    const byId = new Map(displayedNodes.map((node) => [node.id, node]));
    const observerFor = (item: ActivityEvent) => item.observer_node_num === undefined
      ? observerNodes[0]
      : byId.get(`!${(item.observer_node_num >>> 0).toString(16).padStart(8, "0")}`) ?? observerNodes[0];
    const features: Array<{ type: "Feature"; properties: { kind: string; path_style: string; observed_at: string }; geometry: { type: "LineString"; coordinates: number[][] } }> = [];
    const nodeFor = (nodeNum: number) => byId.get(`!${(nodeNum >>> 0).toString(16).padStart(8, "0")}`);
    const addTraceSegments = (nodes: number[], observedAt: string) => {
      for (let index = 0; index < nodes.length - 1; index += 1) {
        const start = nodeFor(nodes[index]);
        const end = nodeFor(nodes[index + 1]);
        if (!start || !end) continue;
        features.push({
          type: "Feature",
          properties: { kind: "traceroute", path_style: "trace", observed_at: observedAt },
          geometry: { type: "LineString", coordinates: [[start.lng, start.lat], [end.lng, end.lat]] },
        });
      }
    };
    const packetEvents = [...animationEvents].sort((left, right) =>
      (right.source === "LOCAL_TX" ? 1 : 0) - (left.source === "LOCAL_TX" ? 1 : 0));
    const animatedPackets = new Set<string>();
    for (const item of packetEvents) {
      if (item.from_node === undefined) continue;
      if (item.source !== "RF_OBSERVED" && item.source !== "MQTT_NETWORK" && item.source !== "LOCAL_TX") continue;
      if (item.source === "MQTT_NETWORK" && !showMqtt) continue;
      if (Date.now() - new Date(item.observed_at).getTime() > 15_000) continue;
      const packetKey = item.packet_id === undefined ? item.event_id : `${item.packet_id}:${item.from_node}:${item.to_node ?? 0}`;
      if (animatedPackets.has(packetKey)) continue;
      animatedPackets.add(packetKey);
      const fromId = `!${(item.from_node >>> 0).toString(16).padStart(8, "0")}`;
      const from = byId.get(fromId);
      const observer = observerFor(item);
      if (!observer) continue;
      if (item.traceroute && item.to_node !== undefined) {
        addTraceSegments([item.to_node, ...item.traceroute.route, item.from_node], item.observed_at);
        if (item.traceroute.route_back.length) {
          addTraceSegments([item.from_node, ...item.traceroute.route_back, item.to_node], item.observed_at);
        }
      }
      if (item.source === "LOCAL_TX") {
        if (!from || item.to_node === undefined || item.to_node === 0xffffffff || item.to_node === 0) continue;
        const toId = `!${(item.to_node >>> 0).toString(16).padStart(8, "0")}`;
        const to = byId.get(toId);
        if (!to) continue;
        const kind = item.portnum === "TEXT_MESSAGE_APP" ? "rf_text" : "addressed";
        features.push({ type: "Feature", properties: { kind, path_style: "logical", observed_at: item.observed_at }, geometry: { type: "LineString", coordinates: [[from.lng, from.lat], [to.lng, to.lat]] } });
        continue;
      }
      const transportKind = item.source === "MQTT_NETWORK" ? "mqtt" : "reception";
      if (!from) {
        features.push({ type: "Feature", properties: { kind: transportKind, path_style: "logical", observed_at: item.observed_at }, geometry: { type: "LineString", coordinates: [[observer.lng, observer.lat], [observer.lng, observer.lat]] } });
        continue;
      }
      const direct = item.source === "RF_OBSERVED"
        && Math.max(0, (item.hop_start ?? 0) - (item.hop_limit ?? 0)) === 0;
      features.push({ type: "Feature", properties: { kind: transportKind, path_style: direct ? "confirmed" : "logical", observed_at: item.observed_at }, geometry: { type: "LineString", coordinates: [[from.lng, from.lat], [observer.lng, observer.lat]] } });
      if (item.to_node !== undefined && item.to_node !== 0xffffffff && item.to_node !== 0) {
        const toId = `!${(item.to_node >>> 0).toString(16).padStart(8, "0")}`;
        const to = byId.get(toId);
        if (to) {
          const addressedKind = item.source === "MQTT_NETWORK"
            ? "mqtt"
            : item.source === "RF_OBSERVED" && item.portnum === "TEXT_MESSAGE_APP" ? "rf_text" : "addressed";
          features.push({ type: "Feature", properties: { kind: addressedKind, path_style: "logical", observed_at: item.observed_at }, geometry: { type: "LineString", coordinates: [[from.lng, from.lat], [to.lng, to.lat]] } });
        }
      }
    }
    animationLinks.current = features.map((feature) => ({
      from: feature.geometry.coordinates[0] as [number, number],
      to: feature.geometry.coordinates[1] as [number, number],
      kind: feature.properties.kind as "reception" | "addressed" | "mqtt" | "rf_text" | "traceroute",
      pathStyle: feature.properties.path_style as "logical" | "confirmed" | "trace",
      observedAt: feature.properties.observed_at,
    }));
    setDrawablePathCount(features.length);
    const source = map.current?.getSource("recent-rf-links") as GeoJSONSource | undefined;
    source?.setData({ type: "FeatureCollection", features });
  }, [animationEvents, displayedNodes, mapReady, observerNodes, showMqtt]);

  useEffect(() => {
    if (!mapReady || !map.current) return;
    const patterns = [[1, 4], [2, 3], [3, 2], [4, 1]];
    let frame = 0;
    const timer = window.setInterval(() => {
      if (map.current?.getLayer("recent-rf-links")) {
        map.current.setPaintProperty("recent-rf-links", "line-dasharray", patterns[frame % patterns.length]);
        frame += 1;
      }
    }, 220);
    return () => window.clearInterval(timer);
  }, [mapReady]);

  useEffect(() => {
    if (!apiHealthy) return;
    let animationFrame = 0;
    let lastActiveCount = -1;
    const lifetimeMs = 15_000;
    const burstStarts = [0, 2_200, 4_400, 6_600, 8_800, 11_000, 13_200];
    const packetOffsets = [0, 140, 280];
    const travelMs = 900;
    const animate = () => {
      const now = Date.now();
      const activeLinks = (showLinks ? animationLinks.current : []).filter((link) => {
        const age = now - new Date(link.observedAt).getTime();
        return age >= 0 && age <= lifetimeMs;
      });
      if (activeLinks.length !== lastActiveCount) {
        lastActiveCount = activeLinks.length;
        setDrawablePathCount(activeLinks.length);
      }
      const lineSource = map.current?.getSource("recent-rf-links") as GeoJSONSource | undefined;
      lineSource?.setData({
        type: "FeatureCollection",
        features: activeLinks.map((link) => ({
          type: "Feature" as const,
          properties: { kind: link.kind, path_style: link.pathStyle, observed_at: link.observedAt },
          geometry: { type: "LineString" as const, coordinates: [link.from, link.to] },
        })),
      });
      const particles = activeLinks.flatMap((link) => {
        const age = now - new Date(link.observedAt).getTime();
        return burstStarts.flatMap((burstStart) => packetOffsets.flatMap((offset) => {
          const travelAge = age - burstStart - offset;
          if (travelAge < 0 || travelAge > travelMs) return [];
          return [{ link, progress: travelAge / travelMs }];
        }));
      });
      const particleSource = map.current?.getSource("rf-particles") as GeoJSONSource | undefined;
      particleSource?.setData({
        type: "FeatureCollection",
        features: particles.map(({ link, progress }) => {
          const start = MercatorCoordinate.fromLngLat(link.from);
          const end = MercatorCoordinate.fromLngLat(link.to);
          const coordinate = new MercatorCoordinate(
            start.x + (end.x - start.x) * progress,
            start.y + (end.y - start.y) * progress,
          ).toLngLat();
          return {
            type: "Feature" as const,
            properties: { kind: link.kind },
            geometry: { type: "Point" as const, coordinates: [coordinate.lng, coordinate.lat] },
          };
        }),
      });
      animationFrame = requestAnimationFrame(animate);
    };
    animationFrame = requestAnimationFrame(animate);
    return () => {
      cancelAnimationFrame(animationFrame);
    };
  }, [apiHealthy, showLinks]);

  useEffect(() => {
    if (!mapReady || !map.current) return;
    const lineSource = map.current.getSource("follow-packet-lines") as GeoJSONSource | undefined;
    const nodeSource = map.current.getSource("follow-packet-nodes") as GeoJSONSource | undefined;
    if (!followedPacket) {
      lineSource?.setData({ type: "FeatureCollection", features: [] });
      nodeSource?.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    const byNumber = new Map(displayedNodes.map((node) => [parseInt(node.id.slice(1), 16) >>> 0, node]));
    const sender = byNumber.get(followedPacket.sender >>> 0);
    const destination = followedPacket.to_node !== null && followedPacket.to_node !== 0xffffffff
      ? byNumber.get(followedPacket.to_node >>> 0) : undefined;
    const verifiedObservers = new Map<number, MeshNode>();
    for (const observation of followedPacket.observations ?? []) {
      if (observation.source !== "RF_OBSERVED" || observation.observer_node_num === undefined) continue;
      const observer = byNumber.get(observation.observer_node_num >>> 0);
      if (observer) verifiedObservers.set(observation.observer_node_num >>> 0, observer);
    }
    const lines = sender ? [...verifiedObservers.values()].map((observer) => ({
      type: "Feature" as const,
      properties: { evidence: "DIRECT_RF_OBSERVATION" },
      geometry: { type: "LineString" as const, coordinates: [[sender.lng, sender.lat], [observer.lng, observer.lat]] },
    })) : [];
    const points: Array<{ node: MeshNode; role: string }> = [];
    if (sender) points.push({ node: sender, role: "sender" });
    if (destination) points.push({ node: destination, role: "destination" });
    for (const observer of verifiedObservers.values()) points.push({ node: observer, role: "observer" });
    lineSource?.setData({ type: "FeatureCollection", features: lines });
    nodeSource?.setData({
      type: "FeatureCollection",
      features: points.map(({ node, role }) => ({
        type: "Feature" as const, properties: { role, label: node.label },
        geometry: { type: "Point" as const, coordinates: [node.lng, node.lat] },
      })),
    });
    if (points.length) {
      const navigablePoints = points.filter(({ node }) => !isInvalidZeroPosition(node.lng, node.lat));
      if (!navigablePoints.length) {
        showLocationWarning();
        return;
      }
      const bounds = new LngLatBounds();
      navigablePoints.forEach(({ node }) => bounds.extend([node.lng, node.lat]));
      if (navigablePoints.length === 1) map.current.flyTo({ center: [navigablePoints[0].node.lng, navigablePoints[0].node.lat], zoom: 13, duration: 800 });
      else map.current.fitBounds(bounds, { padding: { top: 100, right: 430, bottom: 100, left: 310 }, maxZoom: 13, duration: 950 });
    }
  }, [displayedNodes, followedPacket, mapReady]);

  const focusPosition = (longitude: number | null, latitude: number | null, zoom = 13) => {
    if (longitude === null || latitude === null) return;
    if (isInvalidZeroPosition(longitude, latitude)) {
      showLocationWarning();
      return;
    }
    window.requestAnimationFrame(() => {
      const instance = map.current;
      if (!instance) return;
      instance.resize();
      instance.flyTo({
        center: [longitude, latitude],
        zoom: Math.max(instance.getZoom(), zoom),
        duration: 700,
        essential: true,
      });
    });
  };

  const selectSearchResult = () => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return;
    const summary = filteredNodeSummaries[0];
    if (!summary) return;
    const id = `!${(summary.node_num >>> 0).toString(16).padStart(8, "0")}`;
    const match = displayedNodes.find((node) => node.id === id) ?? null;
    setSelected(match);
    setSelectedNode(summary);
    setSelectedActivity(null);
    setDetailOpen(true);
    focusPosition(match?.lng ?? summary.longitude, match?.lat ?? summary.latitude);
  };

  const selectNodeSummary = (summary: NodeSummary) => {
    const id = `!${(summary.node_num >>> 0).toString(16).padStart(8, "0")}`;
    const mapNode = displayedNodes.find((node) => node.id === id) ?? null;
    setSelectedNode(summary);
    setSelected(mapNode);
    setSelectedActivity(null);
    setDetailOpen(true);
    setPanelOpen(false);
    focusPosition(mapNode?.lng ?? summary.longitude, mapNode?.lat ?? summary.latitude);
  };

  const flyHome = () => {
    map.current?.fitBounds(LZ_REGION_BOUNDS, { padding: 55, duration: 900, maxZoom: 9.2 });
  };
  const openLayers = () => {
    setPanelOpen(true);
    if (layersPanel.current) layersPanel.current.open = true;
  };
  const openFilters = () => {
    setPanelOpen(true);
    if (layersPanel.current) layersPanel.current.open = false;
  };
  const adjustUiScale = (change: number) => {
    setUiScale((current) => {
      const next = Math.min(1.4, Math.max(0.9, Math.round((current + change) * 20) / 20));
      window.localStorage.setItem("atlas-ui-scale", String(next));
      window.setTimeout(() => map.current?.resize(), 0);
      return next;
    });
  };
  const importTerrainPrediction = async (file?: File) => {
    if (!file || !map.current) return;
    try {
      const parsed = JSON.parse(await file.text()) as Parameters<GeoJSONSource["setData"]>[0];
      if (parsed.type !== "FeatureCollection") throw new Error("Expected a GeoJSON FeatureCollection");
      const source = map.current.getSource("terrain-prediction") as GeoJSONSource | undefined;
      source?.setData(parsed);
      setPredictionName(file.name);
      setShowPrediction(true);
      setMapError(null);
    } catch (error) {
      setMapError(error instanceof Error ? `Terrain prediction: ${error.message}` : "Invalid terrain prediction GeoJSON");
    }
  };
  const nodeLabel = (nodeNum?: number) => {
    if (nodeNum === undefined) return observerNodes[0]?.label ?? observers[0]?.observer_id ?? "OBSERVER";
    const id = `!${(nodeNum >>> 0).toString(16).padStart(8, "0")}`;
    return liveNodes.find((node) => node.id === id)?.label ?? id.toUpperCase();
  };
  const selectedPositionAgeMinutes = selectedNode?.position_observed_at
    ? Math.max(0, Math.floor((Date.now() - new Date(selectedNode.position_observed_at).getTime()) / 60_000))
    : null;
  const positionedIds = new Set(liveNodes.map((node) => node.id));
  const latestDrawable = activity.find((item) => {
    if (item.from_node === undefined || (item.source !== "RF_OBSERVED" && item.source !== "MQTT_NETWORK" && item.source !== "LOCAL_TX")) return false;
    const id = `!${(item.from_node >>> 0).toString(16).padStart(8, "0")}`;
    return positionedIds.has(id);
  });
  const lastDrawableSeconds = latestDrawable
    ? Math.max(0, Math.floor((Date.now() - new Date(latestDrawable.observed_at).getTime()) / 1000))
    : null;
  const animationStatus = drawablePathCount > 0
    ? `${drawablePathCount} ACTIVE`
    : lastDrawableSeconds === null
      ? "IDLE · NO POSITIONED SENDERS"
      : `IDLE · LAST ${lastDrawableSeconds < 60 ? `${lastDrawableSeconds}s` : `${Math.floor(lastDrawableSeconds / 60)}m`} AGO`;
  const summaryLabel = (node: NodeSummary) => {
    const id = `!${(node.node_num >>> 0).toString(16).padStart(8, "0")}`.toUpperCase();
    return node.short_name && node.long_name
      ? `[${node.short_name}] ${node.long_name}`
      : node.long_name || node.short_name || id;
  };
  const relativeAge = (timestamp: string) => {
    const seconds = Math.max(0, Math.floor((Date.now() - new Date(timestamp).getTime()) / 1000));
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
    return `${Math.floor(seconds / 86400)}d`;
  };

  return (
    <main className="atlas-shell" style={{ zoom: uiScale, width: `${100 / uiScale}vw`, height: `${100 / uiScale}vh` }}>
      <header className="topbar">
        <div className="brand-block">
          <button className="icon-button mobile-only" onClick={() => setPanelOpen(!panelOpen)} aria-label="Open navigation"><Menu size={18} /></button>
          <div className="brand-mark"><Radio size={18} strokeWidth={2.3} /></div>
          <div><div className="brand">ATLAS</div><div className="brand-sub">LIVE RF MAP</div></div>
          <StatusPill live={live} />
          <span className={`demo-pill ${apiHealthy ? "truth-pill" : ""}`}>
            {apiHealthy ? "LIVE VERIFIED DATA" : "DEMO · API OFFLINE"}
          </span>
        </div>
        <div className="top-stats">
          <div><strong>{health?.positioned_nodes ?? 0}</strong><span>POSITIONED</span></div>
          <div><strong>{health?.observations ?? 0}</strong><span>OBSERVATIONS</span></div>
          <div><strong>{health?.observers ?? 0}</strong><span>OBSERVER</span></div>
        </div>
        <div className="top-actions">
          <span className="single-observer"><Antenna size={13} /> {health?.single_observer_mode ? "SINGLE-OBSERVER MODE" : `${health?.observers ?? 0} OBSERVERS`}</span>
          <div className="text-scale-control" aria-label="Interface text size"><button onClick={() => adjustUiScale(-0.05)} disabled={uiScale <= 0.9} aria-label="Decrease interface size"><Minus size={13} /></button><span>{Math.round(uiScale * 100)}%</span><button onClick={() => adjustUiScale(0.05)} disabled={uiScale >= 1.4} aria-label="Increase interface size"><Plus size={13} /></button></div>
          <button className="icon-button" aria-label="Help" onClick={() => setHelpOpen(true)}><CircleHelp size={18} /></button>
          <button className="icon-button" aria-label="Filters and settings" onClick={openFilters}><Settings2 size={18} /></button>
        </div>
      </header>

      <section className="workspace">
        {locationWarning && <div className="location-warning" role="status" aria-live="polite"><Crosshair size={15} /><span>{locationWarning}</span></div>}
        {initialLoading && <div className="initial-loader" role="status" aria-live="polite">
          <span className="loader-pulse"><Radio size={18} /></span>
          <strong>LOADING A MESHTON OF DATA…</strong>
          <small>Synchronizing live nodes and packet activity</small>
        </div>}
        <div ref={mapContainer} className="map-canvas" aria-label="Meshtastic node map" />
        {mapError && <div className="map-error"><strong>MAP RENDERER OFFLINE</strong><span>{mapError}</span></div>}
        {apiHealthy && !followedPacket && <div className={`animation-status ${drawablePathCount ? "active" : ""}`}><Activity size={12} /> RF LIVE · {animationStatus}</div>}
        {followedPacket && <div className="follow-status"><Crosshair size={12} /><span>FOLLOWING {nodeLabel(followedPacket.sender)} · {followedPacket.packet_id}</span><button onClick={() => setFollowedPacket(null)}><X size={12} /></button></div>}
        {quality && <details className="quality-dashboard glass-panel">
          <summary>
            <span role="button" tabIndex={0} onClick={(event) => { event.preventDefault(); openQualityView("packets"); }}><small>UNIQUE</small><strong>{quality.unique_packets}</strong></span>
            <span role="button" tabIndex={0} onClick={(event) => { event.preventDefault(); openQualityView("packets"); }}><small>REPEATS</small><strong>{quality.repeated_observations}</strong></span>
            <span><small>DECRYPT</small><strong>{quality.decrypt_success_percent}%</strong></span>
            <span role="button" tabIndex={0} onClick={(event) => { event.preventDefault(); openQualityView("gateways"); }}><small>GATEWAYS · 1H</small><strong>{quality.active_gateways_1h}</strong></span>
            <span role="button" tabIndex={0} onClick={(event) => { event.preventDefault(); openQualityView("warnings"); }} className={quality.collector_errors ? "quality-warning" : ""}><small>ERRORS</small><strong>{quality.collector_errors}</strong></span>
            <SlidersHorizontal size={13} />
          </summary>
          <div className="quality-detail">
            <div><small>RF UNIQUE</small><strong>{quality.rf_unique_packets}</strong></div>
            <div><small>MQTT UNIQUE</small><strong>{quality.mqtt_unique_packets}</strong></div>
            <div><small>DECODABLE</small><strong>{quality.mqtt_decodable}</strong></div>
            <div><small>ENCRYPTED UNKNOWN</small><strong>{quality.mqtt_encrypted_unknown}</strong></div>
            <div className="packet-type-quality"><small>PACKET TYPES</small><p>{Object.entries(quality.packet_types).slice(0, 8).map(([name, count]) => <span key={name}><b>{name.replace("_APP", "")}</b>{count}</span>)}</p></div>
            <div className="quality-actions"><button onClick={() => openQualityView("gateways")}>GATEWAY QUALITY</button><button onClick={() => openQualityView("packets")}>LOGICAL PACKETS</button><button onClick={() => openQualityView("warnings")}>WARNINGS</button></div>
          </div>
        </details>}
        <div className="map-vignette" />
        <div className="scanline" />

        <aside className={`left-panel glass-panel ${panelOpen ? "panel-open" : ""}`}>
          <div className="panel-heading"><span>NETWORK VIEW</span><button className="close-mobile" onClick={() => setPanelOpen(false)}><X size={16} /></button></div>
          <label className="search-box"><Search size={15} /><input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") selectSearchResult(); }} placeholder="Find node or ID…" /><kbd>↵</kbd></label>
          <div className="filter-label">PROVENANCE · RF WINS DEDUPLICATION</div>
          <button className={`filter-row provenance-row ${showRf ? "active" : ""}`} onClick={() => setShowRf(!showRf)}><span className="filter-icon rf"><Signal size={14} /></span><span><strong>RF observed</strong><small>Direct ATLAS collector reception</small></span><span className="provenance-count"><b>{nodeSummaries.filter((node) => node.rf_observations > 0).length}</b><small>NODES</small><b>{quality?.rf_unique_packets ?? 0}</b><small>PACKETS</small></span>{showRf ? <Eye size={14} /> : <EyeOff size={14} />}</button>
          <button className={`filter-row provenance-row ${showRemoteRf ? "active" : ""}`} onClick={() => setShowRemoteRf(!showRemoteRf)}><span className="filter-icon remote-rf"><Radio size={14} /></span><span><strong>Remote gateway RF</strong><small>Trusted gateway report · 24H map life</small></span><span className="provenance-count"><b>{nodeSummaries.filter((node) => node.remote_rf_observations > 0).length}</b><small>NODES</small></span>{showRemoteRf ? <Eye size={14} /> : <EyeOff size={14} />}</button>
          <button className={`filter-row provenance-row ${showMqtt ? "active" : ""}`} onClick={() => setShowMqtt(!showMqtt)}><span className="filter-icon mqtt"><Zap size={14} /></span><span><strong>MQTT network</strong><small>Broker-forwarded traffic</small></span><span className="provenance-count"><b>{nodeSummaries.filter((node) => node.mqtt_observations > 0).length}</b><small>NODES</small><b>{quality?.mqtt_unique_packets ?? 0}</b><small>PACKETS</small></span>{showMqtt ? <Eye size={14} /> : <EyeOff size={14} />}</button>
          <details ref={layersPanel} className="network-controls">
            <summary>MAP LAYERS <Layers3 size={12} /></summary>
            <button className="layer-row" onClick={() => setShowNodes(!showNodes)}><span>Nodes</span><span className={`mini-toggle ${showNodes ? "on" : ""}`}><i /></span></button>
            <button className="layer-row" onClick={() => setShowPrediction(!showPrediction)} disabled={!predictionName}><span>Predicted RF Coverage</span><span className={`mini-toggle ${showPrediction ? "on" : ""}`}><i /></span></button>
            <button className="layer-row" onClick={() => setShowCoverage(!showCoverage)}><span>Measured RF Evidence</span><span className={`mini-toggle ${showCoverage ? "on" : ""}`}><i /></span></button>
            <button className="layer-row" onClick={() => setShowReachability(!showReachability)}><span>Mesh Reachability</span><span className={`mini-toggle ${showReachability ? "on" : ""}`}><i /></span></button>
            <button className="layer-row" onClick={() => setShowHeatmap(!showHeatmap)}><span>Activity Heatmap</span><span className={`mini-toggle ${showHeatmap ? "on" : ""}`}><i /></span></button>
            <button className="layer-row" onClick={() => setShowLinks(!showLinks)}><span>Live Packet Paths</span><span className={`mini-toggle ${showLinks ? "on" : ""}`}><i /></span></button>
            <button className="layer-row prediction-import" onClick={() => predictionInput.current?.click()}><span>{predictionName ? `MODEL · ${predictionName}` : "IMPORT SITE PLANNER GEOJSON"}</span><Upload size={13} /></button>
            <input ref={predictionInput} type="file" accept=".geojson,.json,application/geo+json,application/json" hidden onChange={(event) => void importTerrainPrediction(event.target.files?.[0])} />
          </details>
          <div className="node-list-summary"><span>{filteredNodeSummaries.length} NODES</span><span>LAST HEARD</span></div>
          <div className="network-node-list">
            {filteredNodeSummaries.map((node) => {
              const id = `!${(node.node_num >>> 0).toString(16).padStart(8, "0")}`;
              return <button key={node.node_num} className={`network-node-row ${selectedNode?.node_num === node.node_num ? "selected" : ""}`} onClick={() => selectNodeSummary(node)}>
                <i className={`node-source ${node.display_provenance === "RF_OBSERVED" ? "rf" : node.display_provenance === "REMOTE_GATEWAY_RF" ? "remote-rf" : node.display_provenance === "MQTT_NETWORK" ? "mqtt" : "other"}`} />
                <span className="network-node-copy"><strong>{summaryLabel(node)}</strong><small>{id.toUpperCase()} · {node.positioned ? "POSITIONED" : "NO POSITION"}</small></span>
                <time>{relativeAge(node.last_heard)}</time>
              </button>;
            })}
          </div>
        </aside>

        <div className="map-tools">
          <button onClick={flyHome} title="Frame Joplin, Springfield, and Fayetteville" aria-label="Return to LZMesh regional view"><Crosshair size={17} /></button>
          <button onClick={openLayers} title="Map layers" aria-label="Open map layers"><Layers3 size={17} /></button>
          <button onClick={openFilters} title="Map filters" aria-label="Open map filters"><SlidersHorizontal size={17} /></button>
          <button onClick={() => map.current?.zoomIn({ duration: 300 })} title="Zoom in" aria-label="Zoom in"><Plus size={17} /></button>
          <button onClick={() => map.current?.zoomOut({ duration: 300 })} title="Zoom out" aria-label="Zoom out"><Minus size={17} /></button>
        </div>

        {helpOpen && <div className="help-backdrop" role="presentation" onClick={() => setHelpOpen(false)}>
          <section className="help-dialog glass-panel" role="dialog" aria-modal="true" aria-labelledby="atlas-help-title" onClick={(event) => event.stopPropagation()}>
            <button className="detail-close" onClick={() => setHelpOpen(false)} aria-label="Close help"><X size={16} /></button>
            <span className="eyebrow">ATLAS OVERVIEW</span>
            <h2 id="atlas-help-title">Live LZMesh radio evidence</h2>
            <p>ATLAS is an evidence map. It separates what an ATLAS collector physically heard, what a trusted remote gateway reports hearing, and what arrived through Meshtastic MQTT. A packet may have several observations; ATLAS correlates them without discarding their individual provenance.</p>
            <h3>PROVENANCE</h3>
            <div className="help-legend">
              <span><i className="legend-swatch rf" /><b>RF observed</b><small>Orange · physically received by a directly connected ATLAS collector such as CHAOS/LZG2.</small></span>
              <span><i className="legend-swatch remote" /><b>Remote gateway RF</b><small>Yellow · a trusted MQTT gateway reports that it received the packet over LoRa. This is RF evidence at that remote gateway, not at CHAOS.</small></span>
              <span><i className="legend-swatch mqtt" /><b>MQTT network</b><small>Purple · transported through the broker without sufficient evidence to claim a local RF reception.</small></span>
              <span><i className="legend-swatch green" /><b>Local transmission</b><small>Green · a connected observer reports that it originated or queued the transmission locally.</small></span>
            </div>
            <h3>MAP DOTS AND CLUSTERS</h3>
            <div className="help-copy"><p>Individual nodes use their evidence color: orange for direct collector RF, yellow for Remote Gateway RF, and purple for MQTT. Cluster circles are orange for ordinary RF/MQTT groups and yellow when Remote Gateway RF is dominant. ATLAS retains each node's latest valid identity and position until newer evidence replaces them, even when live-map age rules hide the node. Packet activity controls brightness; position age remains visible in node evidence.</p></div>
            <h3>LINES AND MOVING PACKETS</h3>
            <div className="help-legend">
              <span><i className="legend-line solid rf" /><b>Solid orange</b><small>Confirmed zero-hop RF reception from the transmitter to the observer.</small></span>
              <span><i className="legend-line dashed rf" /><b>Dashed orange</b><small>RF observation with a logical transmitter-to-observer relationship; it is not proof of every relay hop.</small></span>
              <span><i className="legend-line dashed mqtt" /><b>Dashed purple</b><small>MQTT-carried or MQTT-addressed activity. Purple does not claim direct RF at the local observer.</small></span>
              <span><i className="legend-line dashed green" /><b>Dashed green</b><small>Text or locally originated addressed traffic. It shows known endpoints, not a reconstructed radio route.</small></span>
              <span><i className="legend-line trace" /><b>Thin blue</b><small>Confirmed traceroute segments when the packet supplies explicit route evidence.</small></span>
              <span><i className="legend-packets" /><b>Moving dots</b><small>A short live visualization of packet activity. It expires after about 15 seconds and follows the displayed evidence line.</small></span>
            </div>
            <h3>FOLLOW PACKET</h3>
            <div className="help-legend">
              <span><i className="legend-swatch green" /><b>Green endpoint</b><small>The packet sender.</small></span>
              <span><i className="legend-swatch destination" /><b>Blue endpoint</b><small>The addressed destination, when known and positioned.</small></span>
              <span><i className="legend-swatch observer" /><b>Orange endpoint</b><small>An observer with a verified RF reception of the selected packet.</small></span>
              <span><i className="legend-line solid rf" /><b>Orange connection</b><small>Sender-to-observer reception evidence. ATLAS does not draw an unverified route to the destination.</small></span>
            </div>
            <h3>MAP LAYERS</h3>
            <div className="help-copy"><p><b>Measured RF Evidence</b> uses actual direct reception samples, RSSI, SNR, position, distance, observer, and age. <b>Mesh Reachability</b> uses reported NeighborInfo and is not a measured RF field. <b>Activity Heatmap</b> shows where recently active positioned nodes are concentrated. <b>Predicted RF Coverage</b> is imported model output and remains visually separate from measured evidence.</p></div>
            <div className="help-caution"><b>WHAT ATLAS DOES NOT CLAIM</b><p>A source and destination do not prove the intervening mesh route. MQTT reception does not prove CHAOS heard RF. NeighborInfo is reported reachability, not a direct collector measurement. Inferred or modeled coverage is never presented as measured RF.</p></div>
            <div className="help-tips"><b>CONTROLS</b><p>Crosshair returns to the regional view. Layers opens map overlays. Filters opens the network panel. Click clusters to expand, nodes for evidence, and packet activity for packet details.</p></div>
          </section>
        </div>}

        <div className="map-caption">
          <span className="coordinates">37.0930° N&nbsp;&nbsp; 94.5334° W</span>
          {showCoverage && <span title="Direct zero-hop receptions only"><i className="legend-dot measured-dot" /> MEASURED · {coverageMeasurements.filter((item) => item.confidence === "measured").length}</span>}
          {showPrediction && predictionName && <span><i className="legend-dot prediction-dot" /> PREDICTED · MODEL</span>}
          {showReachability && <span><i className="legend-dot reachability-dot" /> REACHABILITY · REPORTED</span>}
          {showHeatmap && <span><i className="legend-dot activity-dot" /> ACTIVITY · 24H</span>}
        </div>

        {qualityView && <section className="quality-inspector glass-panel">
          <button className="detail-close" onClick={() => setQualityView(null)} aria-label="Close quality inspector"><X size={16} /></button>
          <span className="eyebrow">DATA QUALITY · LAST 24 HOURS</span>
          <div className="quality-tabs"><button className={qualityView === "gateways" ? "active" : ""} onClick={() => openQualityView("gateways")}>GATEWAYS</button><button className={qualityView === "packets" ? "active" : ""} onClick={() => openQualityView("packets")}>PACKETS</button><button className={qualityView === "warnings" ? "active" : ""} onClick={() => openQualityView("warnings")}>WARNINGS</button></div>
          {qualityViewLoading && <div className="panel-loader"><span className="loader-pulse"><Activity size={15} /></span><strong>ANALYZING PACKET EVIDENCE…</strong><small>Large histories may take a moment</small></div>}
          {qualityView === "gateways" && <>
            {selectedGateway && <div className="quality-selection">
              <button onClick={() => setSelectedGateway(null)}><X size={12} /> GATEWAY DETAIL</button>
              <h3>{selectedGateway.short_name && selectedGateway.long_name ? `[${selectedGateway.short_name}] ${selectedGateway.long_name}` : selectedGateway.long_name || selectedGateway.short_name || selectedGateway.node_id}</h3>
              <div className="evidence-grid"><span><small>UNIQUE PACKETS</small><strong>{selectedGateway.unique_packets}</strong></span><span><small>REPEATS</small><strong>{selectedGateway.repeat_observations}</strong></span><span><small>DIRECT RF</small><strong>{selectedGateway.direct_rf_observations}</strong></span><span><small>DECODE</small><strong>{selectedGateway.decode_success_percent}%</strong></span><span><small>RSSI / SNR</small><strong>{selectedGateway.average_rssi ?? "—"} / {selectedGateway.average_snr ?? "—"}</strong></span><span><small>COVERAGE TRUST</small><strong>{selectedGateway.trusted_for_coverage ? "TRUSTED" : "EXCLUDED"}</strong></span></div>
              {selectedGateway.warnings.map((warning) => <p key={warning}>⚠ {warning.replaceAll("_", " ")}</p>)}
            </div>}
            <div className="quality-list">{gatewayQuality.map((gateway) => <button key={gateway.observer_id} onClick={() => inspectGateway(gateway)}><i className={gateway.trusted_for_coverage ? "trusted" : "warning"} /><span><strong>{gateway.short_name && gateway.long_name ? `[${gateway.short_name}] ${gateway.long_name}` : gateway.long_name || gateway.short_name || gateway.node_id}</strong><small>{gateway.unique_packets} unique · {gateway.repeat_observations} repeats · {gateway.direct_rf_observations} direct RF</small></span><em>{gateway.decode_success_percent}%</em></button>)}</div>
          </>}
          {qualityView === "packets" && <>
            {selectedLogicalPacket && <div className="quality-selection">
              <button onClick={() => setSelectedLogicalPacket(null)}><X size={12} /> LOGICAL PACKET</button>
              <h3>{nodeLabel(selectedLogicalPacket.sender)} → {nodeLabel(selectedLogicalPacket.to_node ?? undefined)}</h3>
              <div className="evidence-grid"><span><small>PROVENANCE</small><strong>{selectedLogicalPacket.provenance.replaceAll("_", " ")}</strong></span><span><small>TYPE</small><strong>{selectedLogicalPacket.portnum.replace("_APP", "")}</strong></span><span><small>OBSERVATIONS</small><strong>{selectedLogicalPacket.observation_count}</strong></span><span><small>GATEWAYS</small><strong>{selectedLogicalPacket.gateway_count}</strong></span></div>
              <button className="follow-packet-button" onClick={() => followLogicalPacket(selectedLogicalPacket)}><Crosshair size={12} /> FOLLOW PACKET ON MAP</button>
              <div className="packet-observation-list">{selectedLogicalPacket.observations?.map((item) => <div key={item.event_id}><i className={item.source === "RF_OBSERVED" ? "rf" : "mqtt"} /><span><strong>{item.observer_id}</strong><small>{item.source?.replaceAll("_", " ")} · {item.rx_rssi ?? "—"} dBm · {Math.max(0, (item.hop_start ?? 0) - (item.hop_limit ?? 0))} hops</small></span></div>)}</div>
            </div>}
            <div className="quality-list">{logicalPackets.map((packet) => <button key={`${packet.sender}:${packet.packet_id}`} onClick={() => inspectLogicalPacket(packet)}><i className={packet.provenance === "RF_OBSERVED" ? "trusted" : "mqtt"} /><span><strong>{nodeLabel(packet.sender)} · {packet.portnum.replace("_APP", "")}</strong><small>{packet.observation_count} observations · {packet.repeat_count} repeats · {packet.gateway_count} gateways</small></span><em>{packet.provenance === "RF_OBSERVED" ? "RF" : "MQTT"}</em></button>)}</div>
          </>}
          {qualityView === "warnings" && <div className="quality-list warning-list">{qualityWarnings.map((warning, index) => <button key={`${warning.kind}:${warning.observed_at}:${index}`} onClick={() => { if (warning.scope === "gateway" && warning.observer_node_num) { setQualityView("gateways"); const gateway = gatewayQuality.find((item) => item.observer_node_num === warning.observer_node_num); if (gateway) inspectGateway(gateway); } else if (warning.sender !== undefined && warning.packet_id !== undefined) { setQualityView("packets"); inspectLogicalPacket({ sender: warning.sender, packet_id: warning.packet_id } as LogicalPacket); } }}><i className={warning.severity} /><span><strong>{warning.title}</strong><small>{warning.detail}</small></span><em>{warning.severity.toUpperCase()}</em></button>)}</div>}
        </section>}

        {detailOpen && selectedNode && (
          <section className="node-detail evidence-inspector glass-panel">
            <button className="detail-close" onClick={() => setDetailOpen(false)} aria-label="Close details"><X size={16} /></button>
            <span className="eyebrow">NODE EVIDENCE</span>
            <div className="node-title"><div className="node-avatar"><Antenna size={19} /></div><div><h2>{summaryLabel(selectedNode)}</h2><code>!{(selectedNode.node_num >>> 0).toString(16).padStart(8, "0")}</code></div></div>
            <div className="node-status"><span className="online-dot" /> LAST HEARD {relativeAge(selectedNode.last_heard)} AGO <span>·</span> {selectedNode.positioned ? "POSITIONED" : "NO POSITION"}</div>
            <div className="seen-grid">
              <span><small>LAST RF SEEN</small><strong>{selectedNode.last_rf_seen ? `${relativeAge(selectedNode.last_rf_seen)} AGO` : "NOT OBSERVED"}</strong></span>
              <span><small>LAST ANY SEEN</small><strong>{relativeAge(selectedNode.last_any_seen ?? selectedNode.last_heard)} AGO</strong></span>
            </div>
            <div className="metric-grid">
              <div><small>SENT EVIDENCE</small><strong>{selectedNode.sent_observations}</strong></div><div><small>RECEIVED</small><strong>{selectedNode.received_observations}</strong></div>
              <div><small>RSSI</small><strong>{selectedNode.last_rssi ?? "—"} <em>dBm</em></strong></div><div><small>SNR</small><strong>{selectedNode.last_snr ?? "—"} <em>dB</em></strong></div>
            </div>
            {selectedNode.position_observed_at && (
              <div className={`position-age ${(selectedPositionAgeMinutes ?? 0) > 60 ? "stale" : ""}`}>
                <span><b>POSITION AGE</b>{selectedNode.latitude !== null && selectedNode.longitude !== null && <small>{selectedNode.latitude.toFixed(5)}, {selectedNode.longitude.toFixed(5)}</small>}</span>
                <strong>{relativeAge(selectedNode.position_observed_at)} AGO</strong>
              </div>
            )}
            {(selectedNode.short_name || selectedNode.hardware_model || selectedNode.role || selectedNode.firmware_version || selectedNode.altitude !== null) && (
              <div className="node-metadata">
                {selectedNode.short_name && <span><small>SHORT NAME</small><strong>{selectedNode.short_name}</strong></span>}
                {selectedNode.hardware_model && <span><small>HARDWARE</small><strong>{selectedNode.hardware_model}</strong></span>}
                {selectedNode.role && <span><small>DEVICE ROLE</small><strong>{selectedNode.role.replaceAll("_", " ")}</strong></span>}
                {selectedNode.firmware_version && <span><small>FIRMWARE</small><strong>{selectedNode.firmware_version}</strong></span>}
                {selectedNode.altitude !== null && <span><small>ALTITUDE</small><strong>{selectedNode.altitude} m</strong></span>}
                {Boolean(selectedNode.is_licensed) && <span><small>LICENSED</small><strong>YES</strong></span>}
                {Boolean(selectedNode.has_wifi) && <span><small>CONNECTIVITY</small><strong>WI-FI</strong></span>}
                {Boolean(selectedNode.has_bluetooth) && <span><small>CONNECTIVITY</small><strong>BLUETOOTH</strong></span>}
                {Boolean(selectedNode.has_ethernet) && <span><small>CONNECTIVITY</small><strong>ETHERNET</strong></span>}
                {Boolean(selectedNode.has_pki) && <span><small>SECURITY</small><strong>PKI CAPABLE</strong></span>}
              </div>
            )}
            <div className="provenance-callout"><Signal size={16} /><span><small>DEDUPLICATED PROVENANCE</small><strong>{selectedNode.display_provenance.replaceAll("_", " ")}</strong></span></div>
            {Boolean(selectedNode.recently_heard_by?.length) && <div className="heard-by-section">
              <div className="node-activity-heading"><span>RECENTLY HEARD BY</span><strong>{selectedNode.recently_heard_by?.length}</strong></div>
              {selectedNode.recently_heard_by?.map((observer) => <div className="heard-by-row" key={`${observer.observer_id}:${observer.observer_node_num ?? ""}`}>
                <span><strong>{observer.short_name && observer.long_name ? `[${observer.short_name}] ${observer.long_name}` : observer.long_name || observer.short_name || observer.observer_id}</strong><small>{observer.observation_count} direct RF observations · {relativeAge(observer.last_observed_at)} ago</small></span>
                <em>{observer.average_rssi ?? "—"} dBm<br />{observer.average_snr ?? "—"} dB</em>
              </div>)}
            </div>}
            {selectedActivity && <div className="packet-evidence-card">
              <button onClick={() => setSelectedActivity(null)}><X size={12} /> PACKET EVIDENCE</button>
              <div className="evidence-grid">
                <span><small>DIRECTION</small><strong>{selectedActivity.node_direction?.toUpperCase() ?? "—"}</strong></span>
                <span><small>PACKET TYPE</small><strong>{selectedActivity.portnum?.replace("_APP", "") ?? "UNKNOWN"}</strong></span>
                <span><small>FROM</small><strong>{nodeLabel(selectedActivity.from_node)}</strong></span>
                <span><small>TO</small><strong>{nodeLabel(selectedActivity.to_node)}</strong></span>
                <span><small>OBSERVER</small><strong>{selectedActivity.observer_id ?? "—"}</strong></span>
                <span><small>HOPS</small><strong>{Math.max(0, (selectedActivity.hop_start ?? 0) - (selectedActivity.hop_limit ?? 0))}</strong></span>
                <span><small>RSSI / SNR</small><strong>{selectedActivity.rx_rssi ?? "—"} / {selectedActivity.rx_snr ?? "—"}</strong></span>
                <span><small>CLASSIFICATION</small><strong>{selectedActivity.mqtt_classification?.replaceAll("_", " ") ?? selectedActivity.source?.replaceAll("_", " ") ?? "UNKNOWN"}</strong></span>
                {selectedActivity.node_info?.role && <span><small>ADVERTISED ROLE</small><strong>{selectedActivity.node_info.role.replaceAll("_", " ")}</strong></span>}
                {(selectedActivity.node_info?.hardware_model || selectedActivity.device_metadata?.hardware_model) && <span><small>ADVERTISED HARDWARE</small><strong>{selectedActivity.device_metadata?.hardware_model ?? selectedActivity.node_info?.hardware_model}</strong></span>}
                {selectedActivity.device_metadata?.firmware_version && <span><small>FIRMWARE</small><strong>{selectedActivity.device_metadata.firmware_version}</strong></span>}
              </div>
              {selectedActivity.packet_id !== undefined && selectedActivity.from_node !== undefined && <button className="follow-packet-button" onClick={() => followLogicalPacket({ sender: selectedActivity.from_node!, packet_id: selectedActivity.packet_id! } as LogicalPacket)}><Crosshair size={12} /> FOLLOW PACKET ON MAP</button>}
              {selectedActivity.evidence?.map((item) => <p key={item}>• {item}</p>)}
            </div>}
            <div className="node-activity-heading"><span>PACKET ACTIVITY</span><strong>{selectedNodeActivity.length}</strong></div>
            <div className="node-activity-list">
              {selectedNodeActivity.map((item) => <button key={`${item.event_id}:${item.observer_id ?? ""}`} className={selectedActivity?.event_id === item.event_id ? "selected" : ""} onClick={() => setSelectedActivity(item)}>
                <time>{new Date(item.observed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
                <i className={item.source === "RF_OBSERVED" ? "rf" : item.source === "MQTT_NETWORK" ? "mqtt" : "other"} />
                <span><strong>{item.portnum?.replace("_APP", "") ?? item.event.replaceAll("_", " ").toUpperCase()}</strong><small>{item.node_direction?.toUpperCase()} · {item.source?.replaceAll("_", " ") ?? "UNKNOWN"}</small></span>
              </button>)}
              {!selectedNodeActivity.length && <div className="empty-activity">NO PACKET EVIDENCE AVAILABLE</div>}
            </div>
          </section>
        )}

        <div className="timeline glass-panel">
          <button className="play-button" onClick={playTimeline} title={selectedTimelineBin === null ? "Pause live updates" : "Replay selected minute"}>{live && selectedTimelineBin === null ? <Pause size={14} /> : <Play size={14} />}</button>
          <span className="timeline-label">{selectedTimelineBin === null ? "LAST 15 MINUTES" : `${selectedTimelineEvents.length} PACKETS`}</span>
          <div className="track" aria-label="Packet activity during the last 15 minutes">
            {timeline.bins.map((bin, index) => {
              const total = bin.rf + bin.mqtt + bin.other;
              const height = total === 0 ? 0 : Math.max(3, Math.round((total / timeline.peak) * 18));
              return <button className={`timeline-bin ${selectedTimelineBin === index ? "selected" : ""}`} key={index} title={`${total} packets: ${bin.rf} RF, ${bin.mqtt} MQTT, ${bin.other} other`} onClick={() => { setSelectedTimelineBin(index); setReplayEvents(null); }}>
                <i className="timeline-other" style={{ height: total ? `${(bin.other / total) * height}px` : 0 }} />
                <i className="timeline-mqtt" style={{ height: total ? `${(bin.mqtt / total) * height}px` : 0 }} />
                <i className="timeline-rf" style={{ height: total ? `${(bin.rf / total) * height}px` : 0 }} />
              </button>;
            })}
            <div className="track-fill" />
          </div>
          <time>{timeline.startLabel}</time><time>{timeline.endLabel}</time><button className="now-button" onClick={() => { setSelectedTimelineBin(null); setReplayEvents(null); setLive(true); }}>NOW</button>
        </div>
      </section>
    </main>
  );
}
