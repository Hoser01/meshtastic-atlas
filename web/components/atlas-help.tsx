"use client";

import { Activity, CircleHelp, Crosshair, Layers3, List, Search, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

type HelpSection = "start" | "map" | "how" | "trust" | "fix";

const sections: Array<{ id: HelpSection; label: string }> = [
  { id: "start", label: "START HERE" },
  { id: "map", label: "MAP GUIDE" },
  { id: "how", label: "HOW DO I?" },
  { id: "trust", label: "DATA & TRUST" },
  { id: "fix", label: "TROUBLESHOOT" },
];

const faqs = [
  ["Return to live mode", "Click the status indicator in the title bar or press NOW on the timeline. Either control clears the selected minute and replay packets, then resumes current activity."],
  ["Find a node", "Type its name, short name, or !node ID in Network View. Select the result to open its evidence. If it has a valid retained position, the map moves to it."],
  ["See what one node has been doing", "Select the node on the map or in Network View. Its panel shows identity, position age, recently heard-by evidence, and recent packets."],
  ["Understand a cluster number", "The number is how many positioned nodes are grouped there. Click the cluster to zoom and split it into smaller clusters or individual dots."],
  ["Return to the LZMesh region", "Press the crosshair button. It frames Joplin, Springfield, and Fayetteville. ATLAS will not force you back after you manually move the map."],
  ["Show only direct ATLAS reception", "Open Provenance and leave RF observed enabled. Turn off Remote gateway RF and MQTT network."],
  ["See recent packet movement", "Enable Live Packet Paths under Map Layers. Lines and moving dots appear only when both required endpoints have usable positions, and they expire quickly."],
  ["Follow a packet", "Open a packet from Packet activity, then choose Follow Packet. ATLAS highlights the known sender, destination, observer, and any explicit traceroute segments."],
  ["Make text easier to read", "Use the − and + in the title bar. This changes interface text and panels without changing the map zoom."],
  ["Tell whether a node is live", "Brightness and glow follow recent packet activity. Position age is shown separately in Node Evidence; an old position does not automatically mean the node is active."],
  ["Use the heatmaps", "RF Reachability summarizes observed ability to reach collectors, weighted by hops. Activity Heatmap shows where recent packets concentrate. Neither one is a terrain prediction."],
];

function Plain({ children }: { children: ReactNode }) {
  return <p className="plain-language"><b>PLAIN ENGLISH</b><span>{children}</span></p>;
}

function AnnotatedMap() {
  return <div className="annotated-map" aria-label="Annotated ATLAS screen layout">
      <div className="annotated-top"><b>ATLAS</b><i /><i /><span>● LIVE</span><em>− &nbsp; + &nbsp; ?</em></div>
    <div className="annotated-body">
      <div className="annotated-network"><small>NETWORK VIEW</small><span><Search size={10} /> Find node…</span><hr /><b>Nodes by last heard</b></div>
      <div className="annotated-controls"><Crosshair size={11} /><SlidersHorizontal size={11} /><Layers3 size={11} /></div>
      <div className="annotated-quality">UNIQUE&nbsp;&nbsp; REPEATS&nbsp;&nbsp; DECRYPT&nbsp;&nbsp; GATEWAYS&nbsp;&nbsp; ERRORS</div>
      <div className="annotated-node one" /><div className="annotated-node two" /><div className="annotated-node three" />
      <div className="annotated-feed"><small>PACKET ACTIVITY</small><p /><p /><p /></div>
      <div className="annotated-time">LAST 15 MINUTES <i /><i /><i /><i /></div>
      <b className="guide-pin pin-1">1</b><b className="guide-pin pin-2">2</b><b className="guide-pin pin-3">3</b><b className="guide-pin pin-4">4</b><b className="guide-pin pin-5">5</b><b className="guide-pin pin-6">6</b>
    </div>
    <ol className="annotated-key">
      <li><b>1</b><span><strong>Network View</strong> Find nodes and open their evidence.</span></li>
      <li><b>2</b><span><strong>Map controls</strong> Reset view, filter provenance, choose layers, and zoom.</span></li>
      <li><b>3</b><span><strong>Data quality</strong> Unique packets, repeats, decrypt rate, gateways, and warnings.</span></li>
      <li><b>4</b><span><strong>Map</strong> Nodes, clusters, heatmaps, and short-lived packet paths.</span></li>
      <li><b>5</b><span><strong>Packet activity</strong> The newest decoded events; select one for evidence.</span></li>
      <li><b>6</b><span><strong>Timeline</strong> Fifteen one-minute bins; select a bin to inspect it.</span></li>
    </ol>
  </div>;
}

export function HelpCenter({ onClose, onStartTour, legend }: { onClose: () => void; onStartTour: () => void; legend: ReactNode }) {
  const [section, setSection] = useState<HelpSection>("start");
  return <div className="help-backdrop" role="presentation" onClick={onClose}>
    <section className="help-dialog help-center glass-panel" role="dialog" aria-modal="true" aria-labelledby="atlas-help-title" onClick={(event) => event.stopPropagation()}>
      <button className="detail-close" onClick={onClose} aria-label="Close help"><X size={17} /></button>
      <div className="help-heading"><span className="help-icon"><CircleHelp size={20} /></span><span><small>ATLAS HELP CENTER</small><h2 id="atlas-help-title">What would you like to do?</h2></span></div>
      <nav className="help-nav" aria-label="Help topics">{sections.map((item) => <button key={item.id} className={section === item.id ? "active" : ""} onClick={() => setSection(item.id)}>{item.label}</button>)}</nav>
      <div className="help-body">
        {section === "start" && <>
          <div className="help-hero"><span><b>NEW TO ATLAS?</b><h3>Take the two-minute map tour</h3><p>ATLAS will point to the real controls and explain them one at a time. The tour never starts automatically.</p></span><button onClick={onStartTour}>START GUIDED TOUR <Activity size={14} /></button></div>
          <h3>WHAT ATLAS IS</h3>
          <div className="help-copy"><p>ATLAS is a live evidence map for LZMesh. It combines observations from read-only radio collectors and Meshtastic MQTT, then keeps the provenance of every observation.</p><Plain>It shows where nodes are and what the mesh has recently seen—while telling you how ATLAS learned each fact.</Plain></div>
          <h3>THREE THINGS TO TRY</h3>
          <div className="help-cards"><span><Search size={16} /><b>Find a node</b><small>Search by name or !node ID, then select it.</small></span><span><Layers3 size={16} /><b>Choose a layer</b><small>Turn packet paths or either heatmap on and off.</small></span><span><List size={16} /><b>Inspect activity</b><small>Select a recent packet to see its evidence.</small></span></div>
          <h3>LIVE, REPLAY, AND PAUSED</h3>
          <div className="help-copy"><p><b>LIVE</b> uses a pulsing red dot with green text. <b>REPLAY</b> is purple and appears whenever a timeline minute is selected. <b>PAUSED</b> is yellow. The status indicator is also a button: click it at any time to clear replay and return to live activity.</p><Plain>Red pulse means you are watching now. Purple means you are looking back. Yellow means updates are paused. Click the label to get back to now.</Plain></div>
          <div className="help-caution"><b>REMEMBER</b><p>ATLAS reports evidence, not certainty it does not possess. Missing animation does not mean missing traffic; an endpoint may simply lack a usable position.</p></div>
        </>}
        {section === "map" && <>
          <h3>THE SCREEN AT A GLANCE</h3><AnnotatedMap />
          <h3>COLORS, LINES, AND LAYERS</h3>{legend}
        </>}
        {section === "how" && <><h3>COMMON QUESTIONS</h3><div className="how-list">{faqs.map(([question, answer]) => <details key={question}><summary>{question}</summary><p>{answer}</p></details>)}</div></>}
        {section === "trust" && <>
          <h3>PROVENANCE: WHERE DID THIS COME FROM?</h3>
          <div className="trust-grid">
            <article className="rf"><b>RF OBSERVED</b><p>A radio attached to an ATLAS collector directly received the packet.</p><Plain>An ATLAS station actually heard it over the air.</Plain></article>
            <article className="remote"><b>REMOTE GATEWAY RF</b><p>A trusted MQTT gateway reported that its radio directly received the packet.</p><Plain>Another trusted station says it heard it over the air.</Plain></article>
            <article className="mqtt"><b>MQTT NETWORK</b><p>The packet arrived through the broker without direct-RF proof at an ATLAS collector.</p><Plain>We saw it online, but cannot claim our radio heard it.</Plain></article>
            <article className="local"><b>LOCAL TRANSMISSION</b><p>A connected observer radio originated or queued the packet.</p><Plain>One of our stations was sending it.</Plain></article>
          </div>
          <h3>MEASURED, INFERRED, AND PREDICTED</h3>
          <div className="help-copy"><p><b>Measured</b> means collector evidence such as RSSI and SNR from a direct reception. <b>Inferred reachability</b> uses reported hops to show that a positioned node reached a collector, but does not invent the relay route. <b>Predicted coverage</b> is imported output from a propagation model and remains a separate layer.</p><Plain>Measured is what a radio heard. Inferred is what the packet tells us about reaching the mesh. Predicted is what a computer model expects.</Plain></div>
          <div className="help-caution"><b>ATLAS DOES NOT CLAIM</b><p>A sender and receiver prove every relay between them; MQTT proves local RF reception; NeighborInfo is a collector measurement; or a heatmap is a precise signal boundary.</p></div>
        </>}
        {section === "fix" && <>
          <h3>QUICK FIXES</h3>
          <div className="how-list">
            <details open><summary>The map is blank or slow to appear</summary><p>Give the initial data synchronization a moment, then reload once. Check that map tiles are not blocked by a content blocker or restrictive network.</p></details>
            <details><summary>A node has no marker</summary><p>Open its evidence. It may have no valid position, may be outside the current view, or may have aged out of live-map visibility. ATLAS still retains its identity and last valid position when known.</p></details>
            <details><summary>I see no moving packet dots</summary><p>Enable Live Packet Paths. Animation also needs usable positions for the endpoints. It lasts about 15 seconds and is intentionally not a permanent route display.</p></details>
            <details><summary>A line does not show the real relay path</summary><p>Most packets provide endpoints and hop counts, not every relay identity. Dashed logical lines do not claim a physical route. Explicit traceroute segments use the thin blue style.</p></details>
            <details><summary>A node shows the wrong place</summary><p>Check Position Age in Node Evidence. ATLAS retains the last valid position until the node reports a newer one. A reported 0,0 is rejected as incorrectly configured.</p></details>
            <details><summary>The mobile screen is crowded</summary><p>Close Node Evidence or Network View when finished. The timeline is intentionally hidden on small screens so it does not cover map controls.</p></details>
          </div>
          <p className="help-version">HELP CONTENT · ATLAS WEB 0.3.23</p>
        </>}
      </div>
    </section>
  </div>;
}

type TourStep = { target: string; title: string; text: string; opensNode?: boolean };
const tourSteps: TourStep[] = [
  { target: "[data-tour='topbar']", title: "Status and display", text: "A pulsing red dot means LIVE, purple means REPLAY, and yellow means PAUSED. Click the status indicator to return to live activity. This bar also shows network totals, text size, Help, and the legend." },
  { target: "[data-tour='network']", title: "Find and filter nodes", text: "Search by name or ID. The list has one row per known node, ordered by when it was last heard." },
  { target: "[data-tour='map-tools']", title: "Map controls", text: "Reset the regional view, open provenance or layers, and change map zoom." },
  { target: "[data-tour='quality']", title: "Data quality", text: "Open this strip to inspect unique packets, repeats, decrypt success, gateways, errors, and warnings." },
  { target: "[data-tour='node-evidence']", title: "Node Evidence", text: "ATLAS selected a real positioned node for this example. This panel separates last RF activity from any activity, shows signal readings and position age, keeps known identity and hardware details, lists observers that directly heard it, and provides its recent packet history.", opensNode: true },
  { target: "[data-tour='activity']", title: "Packet activity", text: "Select a recent event to see the packet evidence and follow known endpoints on the map." },
  { target: "[data-tour='timeline']", title: "Recent history", text: "Each bar is one minute. Selecting a bin changes the top indicator to REPLAY. Press NOW—or click that indicator—to clear the selection and return to live activity." },
];

export function GuidedTour({ onClose, onShowNode }: { onClose: () => void; onShowNode: () => void }) {
  const showNode = useRef(onShowNode);
  useEffect(() => { showNode.current = onShowNode; }, [onShowNode]);
  const [visibleSteps, setVisibleSteps] = useState<TourStep[]>([]);
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const step = visibleSteps[index];
  // Resolve responsive targets after mount; hidden mobile controls are skipped.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setVisibleSteps(tourSteps.filter((candidate) => {
    if (candidate.opensNode) return true;
    const element = document.querySelector(candidate.target);
    if (!(element instanceof HTMLElement)) return false;
    const bounds = element.getBoundingClientRect();
    return bounds.width > 0 && bounds.height > 0 && bounds.right > 0 && bounds.left < window.innerWidth;
  })), []);
  useEffect(() => {
    if (!step) return;
    if (step.opensNode) showNode.current();
    const update = () => setRect(document.querySelector(step.target)?.getBoundingClientRect() ?? null);
    const frame = window.setTimeout(update, step.opensNode ? 450 : 0);
    window.addEventListener("resize", update);
    return () => { window.clearTimeout(frame); window.removeEventListener("resize", update); };
  }, [step]);
  if (!step || !rect) return null;
  return <div className="tour-layer" role="dialog" aria-modal="true" aria-label="ATLAS guided tour">
    <div className="tour-highlight" style={{ left: Math.max(5, rect.left - 5), top: Math.max(5, rect.top - 5), width: Math.min(window.innerWidth - 10, rect.width + 10), height: rect.height + 10 }} />
    <div className="tour-card glass-panel"><button className="detail-close" onClick={onClose} aria-label="End tour"><X size={16} /></button><small>GUIDED TOUR · {index + 1} OF {visibleSteps.length}</small><h2>{step.title}</h2><p>{step.text}</p><div><button onClick={onClose}>END TOUR</button><span /><button disabled={index === 0} onClick={() => setIndex(index - 1)}>BACK</button><button className="primary" onClick={() => index + 1 === visibleSteps.length ? onClose() : setIndex(index + 1)}>{index + 1 === visibleSteps.length ? "FINISH" : "NEXT"}</button></div></div>
  </div>;
}
