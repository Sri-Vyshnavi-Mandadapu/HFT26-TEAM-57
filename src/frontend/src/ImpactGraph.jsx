import { useMemo, useRef, useState } from "react";
import { Minus, Plus, Maximize2 } from "lucide-react";
import { KIND_META } from "./impactKinds.js";

/*
  Interactive dependency graph (pure SVG, no extra dependency).

  Columns read left to right in call direction:
    indirect callers / tests  ->  callers / tests  ->  selected  ->  dependencies
*/

const KIND_ORDER = [
  "target",
  "caller",
  "test",
  "indirect",
  "indirect_test",
  "dependency",
];

const NODE_W = 176;
const NODE_H = 34;
const COL_GAP = 96;
const ROW_GAP = 14;
const PAD = 28;

function truncate(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function layout(nodes) {
  const columns = [[], [], [], []];

  for (const kind of KIND_ORDER) {
    for (const node of nodes) {
      if (node.kind === kind) {
        columns[KIND_META[kind].column].push(node);
      }
    }
  }

  const tallest = Math.max(...columns.map((c) => c.length), 1);
  const height = PAD * 2 + tallest * (NODE_H + ROW_GAP) - ROW_GAP;
  const width = PAD * 2 + 4 * NODE_W + 3 * COL_GAP;

  const positions = {};

  columns.forEach((column, columnIndex) => {
    const columnHeight = column.length * (NODE_H + ROW_GAP) - ROW_GAP;
    const top = (height - columnHeight) / 2;

    column.forEach((node, row) => {
      positions[node.id] = {
        x: PAD + columnIndex * (NODE_W + COL_GAP),
        y: top + row * (NODE_H + ROW_GAP),
      };
    });
  });

  return { positions, width, height };
}

function edgePath(a, b) {
  const sameColumn = Math.abs(a.x - b.x) < 1;

  if (sameColumn) {
    const x = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const y2 = b.y + NODE_H / 2;
    const bulge = 46;

    return `M ${x} ${y1} C ${x + bulge} ${y1}, ${x + bulge} ${y2}, ${x} ${y2}`;
  }

  const forward = a.x < b.x;
  const x1 = forward ? a.x + NODE_W : a.x;
  const x2 = forward ? b.x : b.x + NODE_W;
  const y1 = a.y + NODE_H / 2;
  const y2 = b.y + NODE_H / 2;
  const mid = (x1 + x2) / 2;

  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}

export default function ImpactGraph({
  graph,
  selectedId,
  onSelect,
  onAnalyze,
  hiddenKinds,
  showLowConfidence,
}) {
  const svgRef = useRef(null);
  const dragRef = useRef(null);

  const [hoverId, setHoverId] = useState(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });

  const nodes = useMemo(
    () =>
      graph.nodes.filter(
        (node) =>
          !hiddenKinds.has(node.kind) &&
          (showLowConfidence || node.confidence !== "low")
      ),
    [graph.nodes, hiddenKinds, showLowConfidence]
  );

  const visibleIds = useMemo(
    () => new Set(nodes.map((node) => node.id)),
    [nodes]
  );

  const edges = useMemo(
    () =>
      graph.edges.filter(
        (edge) =>
          visibleIds.has(edge.source) &&
          visibleIds.has(edge.target) &&
          (showLowConfidence || edge.confidence !== "low")
      ),
    [graph.edges, visibleIds, showLowConfidence]
  );

  const { positions, width, height } = useMemo(() => layout(nodes), [nodes]);

  const activeId = hoverId || selectedId;

  const connected = useMemo(() => {
    if (!activeId) return null;

    const ids = new Set([activeId]);

    for (const edge of edges) {
      if (edge.source === activeId) ids.add(edge.target);
      if (edge.target === activeId) ids.add(edge.source);
    }

    return ids;
  }, [activeId, edges]);

  function zoom(factor) {
    setView((current) => ({
      ...current,
      k: Math.min(2.5, Math.max(0.4, current.k * factor)),
    }));
  }

  function onWheel(event) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    zoom(event.deltaY < 0 ? 1.1 : 0.9);
  }

  function onPointerDown(event) {
    if (event.target.closest("[data-node]")) return;

    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: view.x,
      originY: view.y,
    };

    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event) {
    const drag = dragRef.current;
    if (!drag) return;

    const rect = svgRef.current.getBoundingClientRect();
    const scale = width / rect.width;

    setView((current) => ({
      ...current,
      x: drag.originX + (event.clientX - drag.startX) * scale,
      y: drag.originY + (event.clientY - drag.startY) * scale,
    }));
  }

  function onPointerUp() {
    dragRef.current = null;
  }

  function onNodeKey(event, node) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(node.id);
    }
  }

  const transform = `translate(${view.x} ${view.y}) scale(${view.k})`;

  return (
    <div className="impact-graph">
      <div className="impact-graph-toolbar" role="toolbar" aria-label="Graph zoom">
        <button type="button" onClick={() => zoom(1.2)} title="Zoom in" aria-label="Zoom in">
          <Plus size={14} />
        </button>
        <button type="button" onClick={() => zoom(0.8)} title="Zoom out" aria-label="Zoom out">
          <Minus size={14} />
        </button>
        <button
          type="button"
          onClick={() => setView({ x: 0, y: 0, k: 1 })}
          title="Reset view"
          aria-label="Reset view"
        >
          <Maximize2 size={14} />
        </button>
      </div>

      <svg
        ref={svgRef}
        className="impact-graph-svg"
        viewBox={`0 0 ${width} ${height}`}
        style={{ minHeight: Math.min(560, Math.max(260, height * 0.8)) }}
        role="img"
        aria-label={`Dependency graph with ${nodes.length} functions and ${edges.length} relationships`}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <defs>
          <marker
            id="impact-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#7b8499" />
          </marker>
        </defs>

        <g transform={transform}>
          {["Indirect (2+ hops)", "Direct callers & tests", "Selected", "Dependencies"].map(
            (text, column) => (
              <text
                key={text}
                className="impact-column-label"
                x={PAD + column * (NODE_W + COL_GAP)}
                y={14}
              >
                {text}
              </text>
            )
          )}

          {edges.map((edge) => {
            const a = positions[edge.source];
            const b = positions[edge.target];
            if (!a || !b) return null;

            const dim =
              connected && !(connected.has(edge.source) && connected.has(edge.target)
                && (edge.source === activeId || edge.target === activeId));

            return (
              <path
                key={`${edge.source}->${edge.target}`}
                d={edgePath(a, b)}
                className={`impact-edge ${edge.kind} ${edge.confidence} ${dim ? "dim" : ""}`}
                markerEnd="url(#impact-arrow)"
              />
            );
          })}

          {nodes.map((node) => {
            const pos = positions[node.id];
            const meta = KIND_META[node.kind];
            const dim = connected && !connected.has(node.id);
            const selected = node.id === selectedId;

            return (
              <g
                key={node.id}
                data-node
                className={`impact-node ${dim ? "dim" : ""} ${selected ? "selected" : ""}`}
                transform={`translate(${pos.x} ${pos.y})`}
                tabIndex={0}
                role="button"
                aria-label={`${meta.label}: ${node.symbol}, ${node.file_path} line ${node.start_line}`}
                aria-pressed={selected}
                onMouseEnter={() => setHoverId(node.id)}
                onMouseLeave={() => setHoverId(null)}
                onFocus={() => setHoverId(node.id)}
                onBlur={() => setHoverId(null)}
                onClick={() => onSelect(node.id)}
                onDoubleClick={() => node.kind !== "target" && onAnalyze(node)}
                onKeyDown={(event) => onNodeKey(event, node)}
              >
                <title>{`${node.symbol}\n${node.file_path}:${node.start_line}`}</title>
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={6}
                  fill="#1f2430"
                  stroke={meta.color}
                  strokeWidth={selected || node.kind === "target" ? 2.4 : 1.3}
                />
                <rect width={5} height={NODE_H} rx={2} fill={meta.color} />
                <text x={16} y={14} className="impact-node-label">
                  {truncate(node.label, 22)}
                </text>
                <text x={16} y={27} className="impact-node-sub">
                  {truncate(
                    `${node.file_path.split("/").pop()}:${node.start_line}`,
                    27
                  )}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      <p className="impact-graph-hint">
        Click a node for its evidence. Double-click to analyze it instead.
        Drag to pan; Ctrl/⌘ + scroll to zoom. Dashed lines are references
        or low-confidence links.
      </p>
    </div>
  );
}
