import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowDownToLine,
  ArrowUpFromLine,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  FileCode2,
  FlaskConical,
  GitBranch,
  Loader2,
  Network,
  Search,
  ShieldAlert,
  ShieldCheck,
  Target,
} from "lucide-react";

import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

import { apiRequest } from "./api.js";
import ImpactGraph from "./ImpactGraph.jsx";
import { KIND_META } from "./impactKinds.js";
import "./ImpactAnalyzer.css";

SyntaxHighlighter.registerLanguage("python", python);

const RISK_COPY = {
  low: { label: "Low risk", Icon: ShieldCheck },
  medium: { label: "Medium risk", Icon: ShieldAlert },
  high: { label: "High risk", Icon: ShieldAlert },
};

const CONFIDENCE_HELP = {
  high: "Resolved exactly (import, self method or same file)",
  medium: "The only function with this name in the repository",
  low: "Ambiguous: several functions share this name",
};

function formatInline(text) {
  // Render `code` spans from backend reasons.
  return text.split(/(`[^`]+`)/g).map((part, index) =>
    part.startsWith("`") && part.endsWith("`") ? (
      <code key={index}>{part.slice(1, -1)}</code>
    ) : (
      <span key={index}>{part}</span>
    )
  );
}

function EvidenceChip({ file_path, line }) {
  const [copied, setCopied] = useState(false);
  const label = `${file_path}:${line}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(label);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable: the text stays selectable */
    }
  }

  return (
    <button
      type="button"
      className="evidence-chip"
      onClick={copy}
      title="Copy path:line"
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
      <span>{label}</span>
    </button>
  );
}

function ResultRow({ item, selected, onFocus, onAnalyze }) {
  const [open, setOpen] = useState(false);

  return (
    <li className={`impact-row ${selected ? "selected" : ""}`}>
      <div className="impact-row-main">
        <button
          type="button"
          className="impact-row-toggle"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          title={open ? "Hide evidence" : "Show evidence"}
        >
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>

        <div className="impact-row-body">
          <div className="impact-row-title">
            <strong>{item.label}</strong>
            {item.type === "function" && item.symbol.includes(".") && (
              <span className="impact-sub">{item.symbol}</span>
            )}
            <span
              className={`confidence-badge ${item.confidence}`}
              title={CONFIDENCE_HELP[item.confidence]}
            >
              {item.confidence}
            </span>
            {item.relationship === "reference" && (
              <span className="kind-badge">reference</span>
            )}
            {item.is_test === true && item.is_test_function === false && (
              <span className="kind-badge">test helper</span>
            )}
          </div>

          <div className="impact-row-path">
            {item.file_path}:{item.start_line}–{item.end_line}
          </div>

          <p className="impact-reason">{formatInline(item.reason)}</p>
        </div>

        <div className="impact-row-actions">
          <button type="button" className="mini-button" onClick={() => onFocus(item.id)}>
            Show in graph
          </button>
          <button type="button" className="mini-button" onClick={() => onAnalyze(item)}>
            Analyze
          </button>
        </div>
      </div>

      {open && (
        <div className="impact-evidence">
          {item.evidence?.length ? (
            item.evidence.map((ev) => (
              <div className="evidence-line" key={`${ev.file_path}:${ev.line}`}>
                <EvidenceChip file_path={ev.file_path} line={ev.line} />
                <code>{ev.text}</code>
              </div>
            ))
          ) : (
            <div className="evidence-line muted">
              Indirect link. Open the evidence of each hop to verify it.
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function ResultList({ title, Icon, items, empty, selectedId, onFocus, onAnalyze, subtitle }) {
  return (
    <section className="impact-card" aria-label={title}>
      <header className="impact-card-header">
        <div className="section-heading">
          <Icon size={17} />
          <h2>{title}</h2>
        </div>
        <span className="section-meta">{items.length}</span>
      </header>

      {subtitle && <p className="impact-card-sub">{subtitle}</p>}

      {items.length ? (
        <ul className="impact-list">
          {items.map((item) => (
            <ResultRow
              key={item.id}
              item={item}
              selected={item.id === selectedId}
              onFocus={onFocus}
              onAnalyze={onAnalyze}
            />
          ))}
        </ul>
      ) : (
        <p className="impact-empty">{empty}</p>
      )}
    </section>
  );
}

export default function ImpactAnalyzer({ indexed, initialTarget, onTargetConsumed }) {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [loadingList, setLoadingList] = useState(false);

  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [focusId, setFocusId] = useState(null);
  const [hiddenKinds, setHiddenKinds] = useState(new Set());
  const [showLow, setShowLow] = useState(true);
  const [showIndirectTests, setShowIndirectTests] = useState(false);
  const [showCode, setShowCode] = useState(false);

  const graphRef = useRef(null);
  const requestRef = useRef(0);

  // ---- function picker (debounced) ----------------------------------------
  useEffect(() => {
    if (!indexed) return undefined;

    let active = true;

    const timer = window.setTimeout(async () => {
      setLoadingList(true);

      try {
        const data = await apiRequest(
          `/api/impact/functions?q=${encodeURIComponent(query.trim())}&limit=30`
        );
        if (active) setOptions(data.functions || []);
      } catch (err) {
        if (active) setError(err.message);
      } finally {
        if (active) setLoadingList(false);
      }
    }, 200);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [query, indexed]);

  // ---- analysis -------------------------------------------------------------
  const analyze = useCallback(async (fn) => {
    const ticket = ++requestRef.current;

    setLoading(true);
    setError("");
    setPickerOpen(false);

    try {
      const data = await apiRequest("/api/impact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_path: fn.file_path,
          symbol: fn.symbol,
          start_line: fn.start_line,
        }),
      });

      if (ticket !== requestRef.current) return;

      setResult(data);
      setFocusId(data.target.id);
      setHiddenKinds(new Set());
      setShowCode(false);
      setQuery(data.target.symbol);
    } catch (err) {
      if (ticket === requestRef.current) setError(err.message);
    } finally {
      if (ticket === requestRef.current) setLoading(false);
    }
  }, []);

  // A search result chosen in the Explorer opens straight into analysis.
  useEffect(() => {
    if (!initialTarget || !indexed) return undefined;

    const timer = window.setTimeout(() => {
      analyze(initialTarget);
      onTargetConsumed?.();
    }, 0);

    return () => window.clearTimeout(timer);
  }, [initialTarget, indexed, analyze, onTargetConsumed]);

  function focusNode(id) {
    setFocusId(id);
    graphRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function toggleKind(kind) {
    setHiddenKinds((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  const focusedNode = useMemo(
    () => result?.graph.nodes.find((node) => node.id === focusId) || null,
    [result, focusId]
  );

  const focusedDetails = useMemo(() => {
    if (!result || !focusId || focusId === result.target.id) return null;

    return [
      ...result.direct_callers,
      ...result.dependencies,
      ...result.related_tests,
      ...result.indirect_callers,
      ...result.indirect_tests,
    ].find((item) => item.id === focusId);
  }, [result, focusId]);

  // ---- render ---------------------------------------------------------------
  if (!indexed) {
    return (
      <div className="impact-empty-state">
        <Network size={38} />
        <strong>Index a repository first</strong>
        <span>
          Upload a ZIP in the Explorer. The analyzer builds its call graph
          from the indexed source code.
        </span>
      </div>
    );
  }

  const summary = result?.summary;
  const risk = summary ? RISK_COPY[summary.risk] : null;
  const counts = summary?.counts;
  const lowCount = result
    ? [...result.direct_callers, ...result.dependencies, ...result.related_tests].filter(
        (i) => i.confidence === "low"
      ).length
    : 0;

  const visible = (items) => (showLow ? items : items.filter((i) => i.confidence !== "low"));

  return (
    <div className="impact">
      <div className="workspace-heading">
        <div className="breadcrumb">
          <span>REPOLENS</span>
          <ChevronRight size={14} />
          <span>CHANGE IMPACT</span>
        </div>
        <h1>Smart Change Impact Analyzer</h1>
        <p>
          Pick a function to see what a change could touch. Every result is
          extracted from the repository&apos;s real source code, with the exact
          file and line as evidence. Nothing is guessed by AI.
        </p>
      </div>

      {error && (
        <div className="error-message" role="alert">
          <AlertCircle size={17} />
          <span>{error}</span>
        </div>
      )}

      {/* PICKER */}
      <section className="workspace-section">
        <div className="section-header">
          <div className="section-heading">
            <Target size={17} />
            <h2>Choose a function</h2>
          </div>
        </div>

        <div className="impact-picker">
          <div className="main-search">
            <Search size={18} className="search-field-icon" />
            <input
              type="text"
              value={query}
              role="combobox"
              aria-expanded={pickerOpen}
              aria-controls="impact-options"
              aria-label="Function name"
              placeholder="Type a function name, e.g. parse_python or Service.handle"
              onFocus={() => setPickerOpen(true)}
              onChange={(event) => {
                setQuery(event.target.value);
                setPickerOpen(true);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && options[0]) {
                  event.preventDefault();
                  analyze(options[0]);
                }
                if (event.key === "Escape") setPickerOpen(false);
              }}
            />
            {(loadingList || loading) && <Loader2 size={16} className="spin" />}
          </div>

          {pickerOpen && (
            <ul id="impact-options" className="impact-options" role="listbox">
              {options.length === 0 && !loadingList && (
                <li className="impact-option-empty">No matching functions.</li>
              )}
              {options.map((fn) => (
                <li key={fn.id} role="option" aria-selected={fn.id === result?.target.id}>
                  <button type="button" onClick={() => analyze(fn)}>
                    <FileCode2 size={15} />
                    <span className="option-text">
                      <strong>{fn.symbol}</strong>
                      <small>
                        {fn.file_path}:{fn.start_line}
                      </small>
                    </span>
                    <span className="option-counts" title="callers / dependencies">
                      {fn.caller_count}↙ {fn.dependency_count}↗
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {!result && !loading && (
        <div className="impact-empty-state">
          <Network size={38} />
          <strong>No function selected</strong>
          <span>
            Choose a function above, or press “Analyze change impact” on any
            search result in the Explorer.
          </span>
        </div>
      )}

      {result && (
        <>
          {/* SELECTED FUNCTION */}
          <section className="impact-card" aria-label="Selected function">
            <header className="impact-card-header">
              <div className="section-heading">
                <Target size={17} />
                <h2>Selected function</h2>
              </div>
              <button
                type="button"
                className="outline-button"
                onClick={() => setShowCode(!showCode)}
              >
                {showCode ? "Hide source" : "Show source"}
              </button>
            </header>

            <div className="selected-fn">
              <strong className="selected-name">{result.target.symbol}</strong>
              <div className="selected-meta">
                <EvidenceChip
                  file_path={result.target.file_path}
                  line={result.target.start_line}
                />
                <span>
                  Lines {result.target.start_line}–{result.target.end_line}
                </span>
                <span className="kind-badge">{result.target.type}</span>
              </div>
            </div>

            {showCode && (
              <div className="impact-code">
                <SyntaxHighlighter
                  language="python"
                  style={oneDark}
                  showLineNumbers
                  startingLineNumber={result.target.start_line}
                  lineNumberStyle={{ color: "#737d8c", minWidth: "3.5em" }}
                  customStyle={{
                    margin: 0,
                    padding: "12px 0",
                    background: "transparent",
                    fontSize: "12px",
                    lineHeight: 1.7,
                  }}
                >
                  {result.target.code}
                </SyntaxHighlighter>
              </div>
            )}
          </section>

          {/* SUMMARY */}
          <section className={`impact-card impact-summary risk-${summary.risk}`} aria-label="Change impact summary">
            <header className="impact-card-header">
              <div className="section-heading">
                <GitBranch size={17} />
                <h2>Change impact summary</h2>
              </div>
              <span className={`risk-badge ${summary.risk}`}>
                <risk.Icon size={14} />
                {risk.label} · {summary.score}/100
              </span>
            </header>

            <p className="summary-headline">{formatInline(summary.headline)}</p>

            <div className="count-tiles">
              {[
                ["Direct callers", counts.direct_callers],
                ["Indirect callers", counts.indirect_callers],
                ["Dependencies", counts.dependencies],
                ["Direct tests", counts.direct_tests],
                ["Indirect tests", counts.indirect_tests],
                ["Files", counts.files],
              ].map(([label, value]) => (
                <div className="count-tile" key={label}>
                  <strong>{value}</strong>
                  <span>{label}</span>
                </div>
              ))}
            </div>

            <ul className="summary-bullets">
              {summary.bullets.map((bullet) => (
                <li key={bullet}>{formatInline(bullet)}</li>
              ))}
            </ul>

            <details className="why-score">
              <summary>How was this score calculated?</summary>
              <p>
                The score is deterministic, not AI-generated. It adds weight for
                direct and indirect callers and for every other file that uses
                the function, and subtracts weight when tests reference it.
              </p>
              <ul>
                {summary.factors.map((factor) => (
                  <li key={factor}>{factor}</li>
                ))}
              </ul>
            </details>
          </section>

          {/* GRAPH */}
          <section className="impact-card" aria-label="Dependency graph" ref={graphRef}>
            <header className="impact-card-header">
              <div className="section-heading">
                <Network size={17} />
                <h2>Dependency graph</h2>
              </div>
              <label className="graph-toggle">
                <input
                  type="checkbox"
                  checked={showLow}
                  onChange={(event) => setShowLow(event.target.checked)}
                />
                Low-confidence links{lowCount ? ` (${lowCount})` : ""}
              </label>
            </header>

            <div className="graph-legend" role="group" aria-label="Filter graph by type">
              {Object.entries(KIND_META).map(([kind, meta]) => (
                <button
                  type="button"
                  key={kind}
                  className={`legend-item ${hiddenKinds.has(kind) ? "off" : ""}`}
                  aria-pressed={!hiddenKinds.has(kind)}
                  disabled={kind === "target"}
                  onClick={() => toggleKind(kind)}
                >
                  <i style={{ background: meta.color }} />
                  {meta.label}
                </button>
              ))}
            </div>

            <ImpactGraph
              graph={result.graph}
              selectedId={focusId}
              onSelect={setFocusId}
              onAnalyze={analyze}
              hiddenKinds={hiddenKinds}
              showLowConfidence={showLow}
            />

            {result.graph.truncated && (
              <p className="impact-note">
                The graph shows the first {result.graph.nodes.length} functions.
                The lists below contain everything.
              </p>
            )}

            {focusedNode && (
              <div className="graph-detail" aria-live="polite">
                <div className="graph-detail-title">
                  <strong>{focusedNode.symbol}</strong>
                  <span className="kind-badge">{KIND_META[focusedNode.kind].label}</span>
                  {focusedNode.confidence && (
                    <span className={`confidence-badge ${focusedNode.confidence}`}>
                      {focusedNode.confidence}
                    </span>
                  )}
                </div>
                <EvidenceChip file_path={focusedNode.file_path} line={focusedNode.start_line} />
                {focusedDetails && (
                  <>
                    <p className="impact-reason">{formatInline(focusedDetails.reason)}</p>
                    {focusedDetails.evidence?.map((ev) => (
                      <div className="evidence-line" key={`${ev.file_path}:${ev.line}`}>
                        <EvidenceChip file_path={ev.file_path} line={ev.line} />
                        <code>{ev.text}</code>
                      </div>
                    ))}
                  </>
                )}
                {focusedNode.kind !== "target" && (
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => analyze(focusedNode)}
                  >
                    <Target size={15} />
                    Analyze this function
                  </button>
                )}
              </div>
            )}
          </section>

          {/* FOUR SECTIONS */}
          <ResultList
            title="Direct callers"
            subtitle="Functions that call or reference the selected function. Re-check these first."
            Icon={ArrowUpFromLine}
            items={visible(result.direct_callers)}
            empty="No callers were found in the indexed code."
            selectedId={focusId}
            onFocus={focusNode}
            onAnalyze={analyze}
          />

          <ResultList
            title="Dependencies"
            subtitle="Repository functions that the selected function calls."
            Icon={ArrowDownToLine}
            items={visible(result.dependencies)}
            empty="It does not call any other function defined in this repository."
            selectedId={focusId}
            onFocus={focusNode}
            onAnalyze={analyze}
          />

          <ResultList
            title="Related tests"
            subtitle="Test code that calls or references the selected function."
            Icon={FlaskConical}
            items={visible(result.related_tests)}
            empty="No test references this function directly."
            selectedId={focusId}
            onFocus={focusNode}
            onAnalyze={analyze}
          />

          {(result.indirect_callers.length > 0 || result.indirect_tests.length > 0) && (
            <section className="impact-card" aria-label="Indirect impact">
              <header className="impact-card-header">
                <div className="section-heading">
                  <GitBranch size={17} />
                  <h2>Indirect impact</h2>
                </div>
                <label className="graph-toggle">
                  <input
                    type="checkbox"
                    checked={showIndirectTests}
                    onChange={(event) => setShowIndirectTests(event.target.checked)}
                  />
                  Include tests ({result.indirect_tests.length})
                </label>
              </header>
              <p className="impact-card-sub">
                Callers of callers, up to three hops away. Each row shows the
                chain that links it to the selected function.
              </p>
              <ul className="impact-list">
                {visible([
                  ...result.indirect_callers,
                  ...(showIndirectTests ? result.indirect_tests : []),
                ]).map((item) => (
                  <ResultRow
                    key={item.id}
                    item={item}
                    selected={item.id === focusId}
                    onFocus={focusNode}
                    onAnalyze={analyze}
                  />
                ))}
              </ul>
            </section>
          )}

          {/* FILES */}
          <section className="impact-card" aria-label="Affected files">
            <header className="impact-card-header">
              <div className="section-heading">
                <FileCode2 size={17} />
                <h2>Affected files</h2>
              </div>
              <span className="section-meta">{result.affected_files.length}</span>
            </header>
            <ul className="file-list">
              {result.affected_files.map((file) => (
                <li key={file.file_path}>
                  <strong>{file.file_path}</strong>
                  <span>{file.roles.join(", ")}</span>
                  <small>{file.functions.join(", ")}</small>
                </li>
              ))}
            </ul>
          </section>

          {result.external_calls.length > 0 && (
            <section className="impact-card" aria-label="External calls">
              <header className="impact-card-header">
                <div className="section-heading">
                  <ArrowDownToLine size={17} />
                  <h2>External library calls</h2>
                </div>
              </header>
              <p className="impact-card-sub">
                Imported libraries the function uses. They are outside this
                repository, so they are not part of the impact lists.
              </p>
              <div className="chip-row">
                {result.external_calls.map((name) => (
                  <code key={name} className="chip">{name}</code>
                ))}
              </div>
            </section>
          )}

          <aside className="impact-limits">
            <AlertCircle size={14} />
            <div>
              <strong>What this analysis cannot see</strong>
              <ul>
                {result.limitations.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          </aside>
        </>
      )}
    </div>
  );
}
