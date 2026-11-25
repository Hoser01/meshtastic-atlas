export type TracerouteEvidence = {
  from_node?: number;
  to_node?: number;
  request_id?: number;
  want_response?: boolean;
  traceroute?: {
    route: number[];
    route_back: number[];
  };
};

export type TraceroutePath = {
  nodes: number[];
  complete: boolean;
};

const validNode = (value: number | undefined): value is number =>
  value !== undefined && value !== 0 && value !== 0xffffffff;

/**
 * Convert RouteDiscovery evidence into paths without inventing unobserved legs.
 *
 * A request can be uploaded by several MQTT gateways while it is still flooding.
 * Its `route` contains only the relays accumulated by that particular copy, so it
 * must not be joined to the intended destination. A reply (`request_id > 0`) is
 * completed evidence and can safely join the recorded forward and return routes
 * to their endpoints.
 */
export function traceroutePaths(item: TracerouteEvidence): TraceroutePath[] {
  if (!item.traceroute || !validNode(item.from_node)) return [];

  const { route, route_back } = item.traceroute;
  const completedReply = (item.request_id ?? 0) > 0;
  if (!completedReply) {
    const partial = [item.from_node, ...route].filter(validNode);
    return partial.length > 1 ? [{ nodes: partial, complete: false }] : [];
  }

  if (!validNode(item.to_node)) return [];
  const paths: TraceroutePath[] = [
    { nodes: [item.to_node, ...route, item.from_node].filter(validNode), complete: true },
  ];
  if (route_back.length) {
    paths.push({ nodes: [item.from_node, ...route_back, item.to_node].filter(validNode), complete: true });
  }
  return paths.filter((path) => path.nodes.length > 1);
}
