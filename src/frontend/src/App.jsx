import { useCallback, useEffect, useRef, useState } from "react";
import {
  Files,
  Search,
  Code2,
  FileCode2,
  FolderOpen,
  Upload,
  ChevronRight,
  ChevronDown,
  Check,
  Copy,
  X,
  AlertCircle,
  Loader2,
  Sparkles,
  GitBranch,
  RefreshCw,

  PanelLeftClose,

  PanelLeftOpen,

  CheckCircle2,

  FileArchive,

  ShieldCheck,

  Network,

} from "lucide-react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

import ImpactAnalyzer from "./ImpactAnalyzer.jsx";

import "./App.css";

SyntaxHighlighter.registerLanguage("python", python);

const API_URL =

  import.meta.env.VITE_API_URL || "http://localhost:8001";

async function apiRequest(path, options = {}) {

  let response;

  try {

    response = await fetch(`${API_URL}${path}`, options);

  } catch {

    throw new Error(

      "Cannot connect to the backend. Make sure FastAPI is running on port 8001."

    );

  }

  const text = await response.text();

  let data = {};

  try {

    data = text ? JSON.parse(text) : {};

  } catch {

    throw new Error("The server returned an invalid response.");

  }

  if (!response.ok) {

    const detail = data.detail;

    const message =

      typeof detail === "string"

        ? detail

        : Array.isArray(detail)

        ? detail.map((item) => item.msg).join(", ")

        : `Request failed (${response.status})`;

    throw new Error(message);

  }

  return data;

}

function getResultKind(result) {
  const kind = String(
    result.symbol_type || result.kind || result.type || ""
  ).toLowerCase();

  if (kind.includes("class")) return "Class";
  if (kind.includes("function")) return "Function";
  return "";
}

function App() {

  const fileInputRef = useRef(null);

  const [file, setFile] = useState(null);

  const [dragging, setDragging] = useState(false);

  const [sidebarOpen, setSidebarOpen] = useState(true);

  const [indexed, setIndexed] = useState(false);

  const [recordCount, setRecordCount] = useState(0);

  const [query, setQuery] = useState("");

  const [results, setResults] = useState([]);

  const [selectedIndex, setSelectedIndex] = useState(0);

  const [hasSearched, setHasSearched] = useState(false);

  const [explanation, setExplanation] = useState("");

  const [loadingUpload, setLoadingUpload] = useState(false);

  const [loadingSearch, setLoadingSearch] = useState(false);

  const [loadingExplain, setLoadingExplain] = useState(false);

  const [error, setError] = useState("");

  const [copied, setCopied] = useState(false);

  const [activeTab, setActiveTab] = useState("explorer");

  const [showRepository, setShowRepository] = useState(true);

  // Function handed from the code viewer to the Change Impact Analyzer.
  const [impactTarget, setImpactTarget] = useState(null);

  const selectedResult = results[selectedIndex] || null;

  useEffect(() => {

    let active = true;

    async function getStatus() {

      try {

        const data = await apiRequest("/api/status");

        if (!active) return;

        setIndexed(Boolean(data.indexed));

        setRecordCount(

          Number(

            data.total_records ??

            data.record_count ??

            data.count ??

            0

          )

        );

      } catch {

        // The frontend can still load when FastAPI is offline.

      }

    }

    getStatus();

    return () => {

      active = false;

    };

  }, []);

  function chooseFile(candidate) {

    if (!candidate) return;

    if (!candidate.name.toLowerCase().endsWith(".zip")) {

      setError("Please select a .zip repository archive.");

      return;

    }

    if (candidate.size > 20 * 1024 * 1024) {

      setError("ZIP file must be 20 MB or smaller.");

      return;

    }

    setFile(candidate);

    setError("");

  }

  async function handleUpload() {

    if (!file || loadingUpload) return;

    setLoadingUpload(true);

    setError("");

    const formData = new FormData();

    formData.append("file", file);

    try {

      const data = await apiRequest("/api/index", {

        method: "POST",

        body: formData,

      });

      setIndexed(true);

      setRecordCount(Number(data.total_records ?? 0));

      setResults([]);

      setHasSearched(false);

      setSelectedIndex(0);

      setExplanation("");

      setActiveTab("explorer");

    } catch (err) {

      setError(err.message);

    } finally {

      setLoadingUpload(false);

    }

  }

  async function handleSearch(event) {

    event?.preventDefault();

    if (!indexed || !query.trim() || loadingSearch) return;

    setLoadingSearch(true);

    setError("");

    setExplanation("");

    setSelectedIndex(0);

    try {

      const data = await apiRequest("/api/search", {

        method: "POST",

        headers: {

          "Content-Type": "application/json",

        },

        body: JSON.stringify({

          query: query.trim(),

          top_k: 5,

        }),

      });

      setResults(

        Array.isArray(data.results) ? data.results : []

      );

      setHasSearched(true);

      setActiveTab("search");

    } catch (err) {

      setResults([]);

      setHasSearched(false);

      setError(err.message);

    } finally {

      setLoadingSearch(false);

    }

  }

  async function handleExplain() {

    if (!selectedResult || loadingExplain) return;

    setLoadingExplain(true);

    setExplanation("");

    setError("");

    try {

      const data = await apiRequest("/api/explain", {

        method: "POST",

        headers: {

          "Content-Type": "application/json",

        },

        body: JSON.stringify({

        query: query.trim(),

        results: results,

        }),

    });

      setExplanation(

        data.explanation || "No explanation was returned."

      );

    } catch (err) {

      setError(err.message);

    } finally {

      setLoadingExplain(false);

    }

  }

  async function handleCopy() {

    if (!selectedResult) return;

    try {

      await navigator.clipboard.writeText(

        selectedResult.code || ""

      );

      setCopied(true);

      window.setTimeout(() => setCopied(false), 1800);

    } catch {

      setError("Unable to copy code to clipboard.");

    }

  }

  function openImpactAnalyzer() {

    if (!selectedResult) return;

    setImpactTarget(selectedResult);

    setActiveTab("impact");

  }

  const clearImpactTarget = useCallback(() => setImpactTarget(null), []);

  function selectResult(index) {

    setSelectedIndex(index);

    setExplanation("");

    setCopied(false);

  }

  const repoName = file

    ? file.name.replace(/\.zip$/i, "")

    : "No repository selected";

  return (

    <div className="app">

      {/* ACTIVITY BAR */}

      <aside className="activity-bar">

        <div className="activity-top">

          <button

            className={`activity-button ${

              activeTab === "explorer" ? "active" : ""

            }`}

            title="Explorer"

            aria-label="Open explorer"

            onClick={() => {

              setActiveTab("explorer");

              setSidebarOpen(true);

            }}

          >

            <Files size={24} />

          </button>

          <button

            className={`activity-button ${

              activeTab === "search" ? "active" : ""

            }`}

            title="Search"

            aria-label="Open search"

            onClick={() => {

              setActiveTab("search");

              setSidebarOpen(true);

            }}

          >

            <Search size={23} />

          </button>

          <button

            className={`activity-button ${

              activeTab === "impact" ? "active" : ""

            }`}

            title="Change impact"

            aria-label="Open change impact analyzer"

            onClick={() => {

              setActiveTab("impact");

              setSidebarOpen(true);

            }}

          >

            <Network size={23} />

          </button>

          <button

            className="activity-button"

            title="Show or hide sidebar"

            aria-label={sidebarOpen ? "Hide sidebar" : "Show sidebar"}

            onClick={() => setSidebarOpen(!sidebarOpen)}

          >

            {sidebarOpen ? (

              <PanelLeftClose size={22} />

            ) : (

              <PanelLeftOpen size={22} />

            )}

          </button>

        </div>

        <div className="activity-bottom">

          <div className="activity-logo" title="RepoLens">

            <Code2 size={22} />

          </div>

        </div>

      </aside>

      {/* SIDE PANEL */}

      {sidebarOpen && (

        <aside className="side-panel">

          <div className="side-panel-header">

            {activeTab === "explorer"
              ? "EXPLORER"
              : activeTab === "impact"
              ? "CHANGE IMPACT"
              : "SEARCH"}

          </div>

          {activeTab === "explorer" ? (

            <>

              <button

                className="tree-heading"

                onClick={() =>

                  setShowRepository(!showRepository)

                }

              >

                {showRepository ? (

                  <ChevronDown size={15} />

                ) : (

                  <ChevronRight size={15} />

                )}

                <span>REPOSITORY</span>

              </button>

              {showRepository && (

                <div className="tree-content">

                  <div className="tree-item">

                    <FolderOpen

                      size={16}

                      className="folder-icon"

                    />

                    <span className="truncate">{repoName}</span>

                  </div>

                  {indexed && (

                    <div className="tree-item nested">

                      <FileCode2

                        size={15}

                        className="file-icon"

                      />

                      <span>

                        {recordCount} indexed records

                      </span>

                    </div>

                  )}

                </div>

              )}

              <div className="side-divider" />

              <div className="side-section-title">

                QUICK START

              </div>

              <p className="side-help">

                Upload a repository ZIP, then search for

                functions, classes, or behavior using natural

                language.

              </p>

            </>

          ) : activeTab === "impact" ? (

            <div className="side-search">

              <p className="side-help">
                Select a function to see its direct callers,
                dependencies and related tests, based on real
                call relationships found in the indexed code.
              </p>

              <p className="side-help">
                Tip: open any search result and press
                &ldquo;Analyze change impact&rdquo;.
              </p>

            </div>

          ) : (

            <div className="side-search">

              <div className="side-section-title">

                SEARCH RESULTS

              </div>

              {results.length > 0 ? (

                <>

                  <p className="side-results-count">

                    {results.length} matches found

                  </p>

                  {results.map((result, index) => (

                    <button

                      key={`${result.file_path}-${result.start_line}-${index}`}

                      className={`side-result ${

                        selectedIndex === index

                          ? "selected"

                          : ""

                      }`}

                      onClick={() => selectResult(index)}

                    >

                      <FileCode2 size={15} />

                      <div className="side-result-text">

                        <strong>

                          {result.symbol || "Code snippet"}

                        </strong>

                        <span>{result.file_path}</span>

                        <small>

                          Ln {result.start_line}–

                          {result.end_line}

                        </small>

                      </div>

                    </button>

                  ))}

                </>

              ) : (

                <p className="side-help">

                  {hasSearched

                    ? "No matching code found."

                    : "Search the repository to see results here."}

                </p>

              )}

            </div>

          )}

        </aside>

      )}

      {/* MAIN WORKSPACE */}

      <div className="workspace">

        <header className="titlebar">

          <div className="titlebar-left">

            <Code2 size={17} />

            <strong>RepoLens</strong>

            <span className="titlebar-divider">/</span>

            <span>
              {activeTab === "impact" ? "Change Impact" : "Code Explorer"}
            </span>

          </div>

          <div className="titlebar-right">

            <span

              className={`connection-dot ${

                indexed ? "connected" : ""

              }`}

            />

            {indexed ? "Index ready" : "No index"}

          </div>

        </header>

        <div className="editor-tabs">

          <div
            className={`editor-tab ${activeTab === "impact" ? "" : "active"}`}
          >

            <Code2 size={15} />

            <span>Explorer</span>

          </div>

          {activeTab === "impact" && (

            <div className="editor-tab active">

              <Network size={15} />

              <span>Change impact</span>

            </div>

          )}

          {selectedResult && (

            <div className="editor-tab file-tab">

              <FileCode2 size={15} />

              <span>

                {selectedResult.file_path

                  ?.split("/")

                  .pop()}

              </span>

            </div>

          )}

        </div>

        {activeTab === "impact" && (

          <main className="editor-content">

            <ImpactAnalyzer
              indexed={indexed}
              initialTarget={impactTarget}
              onTargetConsumed={clearImpactTarget}
            />

          </main>

        )}

        <main className="editor-content" hidden={activeTab === "impact"}>

          <div className="workspace-heading">

            <div className="breadcrumb">

              <span>REPOLENS</span>

              <ChevronRight size={14} />

              <span>WORKSPACE</span>

            </div>

            <h1>Explore your codebase</h1>

            <p>

              Find relevant code, inspect source evidence,

              and understand how your repository works.

            </p>

          </div>

          {/* ERROR */}

          {error && (

            <div className="error-message" role="alert">

              <AlertCircle size={17} />

              <span>{error}</span>

              <button

                onClick={() => setError("")}

                title="Dismiss"

              >

                <X size={16} />

              </button>

            </div>

          )}

          {/* UPLOAD */}

          <section className="workspace-section">

            <div className="section-header">

              <div className="section-heading">

                <FolderOpen size={17} />

                <h2>Repository</h2>

              </div>

              {indexed && (

                <span className="inline-success">

                  <CheckCircle2 size={14} />

                  Indexed

                </span>

              )}

            </div>

            <div className="repository-box">

              <div

                className={`drop-area ${

                  dragging ? "dragging" : ""

                }`}

                onDragOver={(event) => {

                  event.preventDefault();

                  setDragging(true);

                }}

                onDragLeave={(event) => {

                  event.preventDefault();

                  setDragging(false);

                }}

                onDrop={(event) => {

                  event.preventDefault();

                  setDragging(false);

                  chooseFile(

                    event.dataTransfer.files[0]

                  );

                }}

              >

                <FileArchive

                  size={24}

                  className="archive-icon"

                />

                <div className="drop-text">

                  <strong>

                    {file

                      ? file.name

                      : "Select a repository archive"}

                  </strong>

                  <span>

                    {file

                      ? `${(file.size / 1024).toFixed(1)} KB`

                      : "Drag and drop a ZIP file or browse your computer"}

                  </span>

                </div>

                <input

                    ref={fileInputRef}

                    type="file"

                    accept=".zip,application/zip"

                    style={{ display: "none" }}

                    onChange={(event) => {

                      chooseFile(event.target.files[0]);

                      event.target.value = "";

                    }}

                  />

                  <button

                    type="button"

                    className="outline-button browse-label"

                    onClick={() => fileInputRef.current?.click()}

                  >

                    Browse

                  </button>

              </div>

              <div className="repository-actions">

                <span className="muted-small">

                  ZIP archives · Maximum 20 MB

                </span>

                <button

                  className="primary-button"

                  onClick={handleUpload}

                  disabled={!file || loadingUpload}

                >

                  {loadingUpload ? (

                    <Loader2

                      size={15}

                      className="spin"

                    />

                  ) : (

                    <Upload size={15} />

                  )}

                  {loadingUpload

                    ? "Indexing..."

                    : "Index repository"}

                </button>

              </div>

            </div>

            {indexed && (

              <div className="index-summary">

                <CheckCircle2 size={15} />

                <span>

                  Repository ready

                </span>

                <span className="index-count">

                  {recordCount} records

                </span>

              </div>

            )}

          </section>

          {/* SEARCH */}

          <section className="workspace-section">

            <div className="section-header">

              <div className="section-heading">

                <Search size={17} />

                <h2>Search code</h2>

              </div>

            </div>

            <form

              className="main-search"

              onSubmit={handleSearch}

            >

              <Search

                size={18}

                className="search-field-icon"

              />

              <input

                type="text"

                value={query}

                onChange={(event) =>

                  setQuery(event.target.value)

                }

                placeholder="Where is user authentication handled?"

                disabled={!indexed}

              />

              <button

                className="primary-button"

                type="submit"

                disabled={

                  !indexed ||

                  !query.trim() ||

                  loadingSearch

                }

              >

                {loadingSearch ? (

                  <Loader2

                    size={15}

                    className="spin"

                  />

                ) : (

                  <Search size={15} />

                )}

                Search

              </button>

            </form>

            <p className="search-hint">

              {indexed

                ? "Search by function name, keyword, or natural-language question."

                : "Index a repository to enable search."}

            </p>

          </section>

          {/* SEARCH RESULTS AND CODE VIEWER */}

          <section className="workspace-section">

            <div className="section-header">

              <div className="section-heading">

                <FileCode2 size={17} />

                <h2>Code explorer</h2>

              </div>

              <span className="section-meta">

                {results.length} results

              </span>

            </div>

            <div className="code-explorer">

              {/* RESULTS LIST */}

              <div className="results-column">

                <div className="column-header">

                  <span>RESULTS</span>

                  <span>{results.length}</span>

                </div>

                <div className="results-scroll">

                  {results.length > 0 ? (

                    results.map((result, index) => (

                      <button

                        key={`${result.file_path}-${result.start_line}-${index}`}

                        className={`code-result ${

                          selectedIndex === index

                            ? "selected"

                            : ""

                        }`}

                        aria-pressed={selectedIndex === index}

                        onClick={() =>

                          selectResult(index)

                        }

                      >

                        <div className="code-result-title">

                          <FileCode2 size={16} />

                          <strong>

                            {result.symbol ||

                              "Code snippet"}

                          </strong>

                          {getResultKind(result) && (
                            <span
                              className={`result-kind ${getResultKind(result).toLowerCase()}`}
                            >
                              {getResultKind(result)}
                            </span>
                          )}

                        </div>

                        <div className="code-result-path">

                          {result.file_path}

                        </div>

                        <div className="code-result-meta">

                          <span>

                            Ln {result.start_line}–

                            {result.end_line}

                          </span>

                          {typeof result.score ===

                            "number" && (

                            <span className="relevance-score">

                              Relevance{" "}

                              {result.score.toFixed(2)}

                            </span>

                          )}

                        </div>

                      </button>

                    ))

                  ) : (

                    <div className="empty-results">

                      <Search size={23} />

                      <strong>

                        {hasSearched

                          ? "No matches"

                          : "No results yet"}

                      </strong>

                      <span>

                        {hasSearched

                          ? "Try another search query."

                          : "Search your codebase to see matching files and functions."}

                      </span>

                    </div>

                  )}

                </div>

              </div>

              {/* SOURCE VIEWER */}

              <div className="source-column">

                <div className="column-header source-header">

                  <div className="source-filename">

                    <FileCode2 size={15} />

                    <span>

                      {selectedResult

                        ? selectedResult.file_path

                        : "Source preview"}

                    </span>

                  </div>

                  {selectedResult && (

                    <div className="source-actions">

                    <button
                      className="impact-launch"
                      title="See what a change to this code could affect"
                      onClick={openImpactAnalyzer}
                      disabled={getResultKind(selectedResult) === "Class"}
                    >
                      <Network size={13} />
                      Analyze change impact
                    </button>

                    <button

                      className="toolbar-button"

                      title="Copy code"

                      aria-label={copied ? "Code copied" : "Copy code"}

                      onClick={handleCopy}

                    >

                      {copied ? (

                        <Check size={15} />

                      ) : (

                        <Copy size={15} />

                      )}

                    </button>

                    </div>

                  )}

                </div>

                {selectedResult ? (

                  <>

                    <div className="source-info">

                      <span>

                        {selectedResult.language ||

                          "Source"}

                      </span>

                      <span>

                        Lines{" "}

                        {selectedResult.start_line}–

                        {selectedResult.end_line}

                      </span>

                    </div>

                    <div className="code-scroll">

                      <SyntaxHighlighter
                        language="python"
                        style={oneDark}
                        showLineNumbers
                        startingLineNumber={Number(selectedResult.start_line) || 1}
                        className="source-code-highlight"
                        lineNumberStyle={{ color: "#737d8c", minWidth: "3.5em" }}
                        customStyle={{
                          margin: 0,
                          padding: "15px 0",
                          background: "transparent",
                          minWidth: "max-content",
                          fontSize: "12px",
                          lineHeight: 1.8,
                        }}
                      >
                        {String(selectedResult.code || "")}
                      </SyntaxHighlighter>

                    </div>

                    <div className="source-footer">

                      <ShieldCheck size={14} />

                      Retrieved source evidence

                    </div>

                  </>

                ) : (

                  <div className="empty-source">

                    <Code2 size={37} />

                    <strong>

                      No source file selected

                    </strong>

                    <span>

                      Choose a search result to inspect

                      the corresponding code.

                    </span>

                  </div>

                )}

              </div>

            </div>

          </section>

          {/* AI EXPLANATION */}

          <section className="workspace-section">

            <div className="section-header">

              <div className="section-heading">

                <Sparkles size={17} />

                <h2>Code explanation</h2>

                <span className="ai-label">

                  AI-generated

                </span>

              </div>

              <button

                className="outline-button explain-button"

                onClick={handleExplain}

                disabled={

                  !selectedResult ||

                  loadingExplain

                }

              >

                {loadingExplain ? (

                  <Loader2

                    size={15}

                    className="spin"

                  />

                ) : (

                  <Sparkles size={15} />

                )}

                {loadingExplain

                  ? "Generating..."

                  : "Explain search results"}

              </button>

            </div>

            <div className={`explanation-box ${loadingExplain ? "is-loading" : ""}`}>

              {loadingExplain ? (

                <div className="explanation-loading" role="status">

                  <Loader2 size={18} className="spin" />

                  <span>Reviewing the retrieved code and preparing an explanation...</span>

                </div>

              ) : explanation ? (

                <div className="explanation-content">

                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {explanation}
                  </ReactMarkdown>

                </div>

              ) : (

                <div className="explanation-empty">

                  <Sparkles size={18} />

                  <span>

                    Search your repository, then explain the retrieved code to understand
                    how the relevant functions and classes work together.

                  </span>

                </div>

              )}

              <div className="explanation-note">

                <AlertCircle size={14} />

                AI explanations are separate from

                retrieved source evidence.

              </div>

            </div>

          </section>

        </main>

        {/* BOTTOM STATUS BAR */}

        <footer className="status-bar">

          <div className="status-left">

            <GitBranch size={14} />

            <span>RepoLens</span>

            <span className="status-separator">|</span>

            <span>

              {indexed

                ? `${recordCount} records indexed`

                : "No repository indexed"}

            </span>

          </div>

          <div className="status-right">

            <RefreshCw size={12} />

            <span>FastAPI</span>

            <span className="status-separator">|</span>

            <span>Code Intelligence</span>

          </div>

        </footer>

      </div>

    </div>

  );

}

export default App;
