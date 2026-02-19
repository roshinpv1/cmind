import { useState } from "react";
import {
    Search,
    Loader2,
    BookOpen,
    Info,
    Star,
    ChevronDown,
    ChevronUp,
    Layers,
    Cpu,
    Tag,
    ShieldCheck,
    ShieldAlert,
    ExternalLink,
    Sparkles,
    SlidersHorizontal,
    X,
} from "lucide-react";

interface CatalogResult {
    repo_id: string;
    repo_name: string;
    score: number;
    category: string;
    description: string;
    summary_detailed: string;
    architecture: string;
    tech_stack: string;
    topics: string[];
    quality_score: number;
    specification: string;
    pros: string[];
    cons: string[];
    repo_url: string;
    branch: string;
}

function QualityBadge({ score }: { score: number }) {
    const color =
        score >= 80
            ? "text-emerald-700 bg-emerald-50 ring-emerald-200"
            : score >= 60
                ? "text-amber-700 bg-amber-50 ring-amber-200"
                : "text-red-700 bg-red-50 ring-red-200";
    const label = score >= 80 ? "Excellent" : score >= 60 ? "Good" : "Needs Work";

    return (
        <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold ring-1 ${color}`}>
            <Star className="h-3 w-3" />
            {score}/100 — {label}
        </div>
    );
}

function ScoreBar({ score }: { score: number }) {
    const pct = Math.round(score * 100);
    const color =
        pct >= 70 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-400";

    return (
        <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                    className={`h-full rounded-full transition-all duration-700 ease-out ${color}`}
                    style={{ width: `${pct}%` }}
                />
            </div>
            <span className="text-xs font-bold text-gray-500 tabular-nums w-10 text-right">
                {pct}%
            </span>
        </div>
    );
}

function CatalogCard({ item: rawItem }: { item: CatalogResult }) {
    const [expanded, setExpanded] = useState(false);

    // Normalize null/undefined fields to safe defaults
    const item = {
        ...rawItem,
        topics: rawItem.topics ?? [],
        pros: rawItem.pros ?? [],
        cons: rawItem.cons ?? [],
        architecture: rawItem.architecture ?? "",
        description: rawItem.description ?? "",
        summary_detailed: rawItem.summary_detailed ?? "",
        tech_stack: rawItem.tech_stack ?? "",
        specification: rawItem.specification ?? "",
        repo_url: rawItem.repo_url ?? "",
        branch: rawItem.branch ?? "",
        category: rawItem.category ?? "",
    };

    // Parse specification JSON
    let specObj: { key_apis?: string[]; interfaces?: string[]; contracts?: string[] } | null = null;
    try {
        if (item.specification) {
            specObj = JSON.parse(item.specification);
        }
    } catch (e) {
        // Not valid JSON, skip rendering specification section
        specObj = null;
    }

    return (
        <div className="group bg-white rounded-2xl border border-gray-100 shadow-sm hover:shadow-xl hover:border-gray-200 transition-all duration-300 overflow-hidden">
            {/* Header */}
            <div className="px-6 pt-6 pb-4">
                <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-3 mb-2">
                            <h3 className="text-xl font-black text-gray-900 tracking-tight truncate">
                                {item.repo_name}
                            </h3>
                            {item.repo_url && (
                                <a
                                    href={item.repo_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-gray-300 hover:text-primary transition-colors shrink-0"
                                >
                                    <ExternalLink className="h-4 w-4" />
                                </a>
                            )}
                        </div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-xs font-bold bg-indigo-50 text-indigo-700 ring-1 ring-indigo-100">
                                <Layers className="h-3 w-3" />
                                {item.category || "Uncategorized"}
                            </span>
                            {item.branch && (
                                <span className="text-xs text-gray-400 font-mono">
                                    ⎇ {item.branch}
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="shrink-0 text-right space-y-2">
                        <QualityBadge score={item.quality_score} />
                        <ScoreBar score={item.score} />
                    </div>
                </div>
            </div>

            {/* Description */}
            <div className="px-6 pb-4">
                <p className="text-sm text-gray-600 leading-relaxed">{item.description}</p>
            </div>

            {/* Topics */}
            {item.topics.length > 0 && (
                <div className="px-6 pb-4 flex items-center gap-1.5 flex-wrap">
                    <Tag className="h-3.5 w-3.5 text-gray-300 shrink-0" />
                    {item.topics.map((topic) => (
                        <span
                            key={topic}
                            className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-gray-50 text-gray-500 border border-gray-100 hover:bg-gray-100 hover:text-gray-700 transition-colors cursor-default"
                        >
                            {topic}
                        </span>
                    ))}
                </div>
            )}

            {/* Tech Stack Bar */}
            {item.tech_stack && (
                <div className="px-6 pb-4">
                    <div className="flex items-center gap-2 p-3 bg-gray-50 rounded-xl">
                        <Cpu className="h-4 w-4 text-gray-400 shrink-0" />
                        <span className="text-xs text-gray-600 font-medium">{item.tech_stack}</span>
                    </div>
                </div>
            )}

            {/* Expand / Collapse */}
            <div className="border-t border-gray-50">
                <button
                    onClick={() => setExpanded(!expanded)}
                    className="w-full px-6 py-3 flex items-center justify-between text-sm font-bold text-gray-400 hover:text-gray-700 hover:bg-gray-50/50 transition-colors"
                >
                    <span>{expanded ? "Hide details" : "View architecture & analysis"}</span>
                    {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                </button>
            </div>

            {/* Expanded Details */}
            {expanded && (
                <div className="border-t border-gray-100 bg-gray-50/30 animate-in slide-in-from-top-2 duration-300">
                    {/* Detailed Summary */}
                    {item.summary_detailed && (
                        <div className="px-6 py-5 border-b border-gray-100">
                            <h4 className="text-xs font-black uppercase tracking-widest text-gray-400 mb-3">
                                Detailed Analysis
                            </h4>
                            <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">
                                {item.summary_detailed}
                            </p>
                        </div>
                    )}

                    {/* Architecture */}
                    {item.architecture && (
                        <div className="px-6 py-5 border-b border-gray-100">
                            <h4 className="text-xs font-black uppercase tracking-widest text-gray-400 mb-3">
                                Architecture
                            </h4>
                            <div className="text-sm text-gray-700 leading-relaxed space-y-2">
                                {item.architecture.split("\n").map((line, i) => (
                                    <p key={i}>{line}</p>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Specification */}
                    {specObj && (
                        <div className="px-6 py-5 border-b border-gray-100">
                            <h4 className="text-xs font-black uppercase tracking-widest text-gray-400 mb-3">
                                Specification
                            </h4>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                {specObj.key_apis && (Array.isArray(specObj.key_apis) ? specObj.key_apis : [specObj.key_apis]).length > 0 && (
                                    <div>
                                        <span className="text-[10px] font-bold uppercase text-gray-400 tracking-wider">APIs</span>
                                        <ul className="mt-1 space-y-1">
                                            {(Array.isArray(specObj.key_apis) ? specObj.key_apis : [specObj.key_apis]).map((api, i) => (
                                                <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                                    <span className="text-primary mt-0.5">•</span> {String(api)}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                                {specObj.interfaces && (Array.isArray(specObj.interfaces) ? specObj.interfaces : [specObj.interfaces]).length > 0 && (
                                    <div>
                                        <span className="text-[10px] font-bold uppercase text-gray-400 tracking-wider">Interfaces</span>
                                        <ul className="mt-1 space-y-1">
                                            {(Array.isArray(specObj.interfaces) ? specObj.interfaces : [specObj.interfaces]).map((iface, i) => (
                                                <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                                    <span className="text-indigo-500 mt-0.5">•</span> {String(iface)}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                                {specObj.contracts && (Array.isArray(specObj.contracts) ? specObj.contracts : [specObj.contracts]).length > 0 && (
                                    <div>
                                        <span className="text-[10px] font-bold uppercase text-gray-400 tracking-wider">Contracts</span>
                                        <ul className="mt-1 space-y-1">
                                            {(Array.isArray(specObj.contracts) ? specObj.contracts : [specObj.contracts]).map((c, i) => (
                                                <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                                    <span className="text-amber-500 mt-0.5">•</span> {String(c)}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Pros / Cons */}
                    {(item.pros.length > 0 || item.cons.length > 0) && (
                        <div className="px-6 py-5">
                            <div className="grid grid-cols-2 gap-6">
                                {item.pros.length > 0 && (
                                    <div>
                                        <h4 className="text-xs font-black uppercase tracking-widest text-emerald-600 mb-3 flex items-center gap-1.5">
                                            <ShieldCheck className="h-3.5 w-3.5" /> Strengths
                                        </h4>
                                        <ul className="space-y-2">
                                            {item.pros.map((pro, i) => (
                                                <li
                                                    key={i}
                                                    className="text-xs text-gray-600 flex items-start gap-2 p-2 bg-emerald-50/50 rounded-lg"
                                                >
                                                    <span className="text-emerald-500 shrink-0 mt-0.5">✓</span>
                                                    {pro}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                                {item.cons.length > 0 && (
                                    <div>
                                        <h4 className="text-xs font-black uppercase tracking-widest text-red-500 mb-3 flex items-center gap-1.5">
                                            <ShieldAlert className="h-3.5 w-3.5" /> Weaknesses
                                        </h4>
                                        <ul className="space-y-2">
                                            {item.cons.map((con, i) => (
                                                <li
                                                    key={i}
                                                    className="text-xs text-gray-600 flex items-start gap-2 p-2 bg-red-50/50 rounded-lg"
                                                >
                                                    <span className="text-red-400 shrink-0 mt-0.5">✗</span>
                                                    {con}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

export default function AgentCatalogSearch() {
    const [query, setQuery] = useState("");
    const [results, setResults] = useState<CatalogResult[]>([]);
    const [loading, setLoading] = useState(false);
    const [hasSearched, setHasSearched] = useState(false);
    const [showFilters, setShowFilters] = useState(false);

    // Filters
    const [limit, setLimit] = useState(5);
    const [minScore, setMinScore] = useState(0.5);

    const handleSearch = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!query.trim()) return;

        setLoading(true);
        setHasSearched(true);

        try {
            const res = await fetch("/api/v1/catalogs/search", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    query,
                    limit,
                    min_score: minScore,
                }),
            });

            if (res.ok) {
                const data = await res.json();
                setResults(data);
            } else {
                console.error("Search failed:", res.statusText);
                setResults([]);
            }
        } catch (err) {
            console.error("Search error:", err);
            setResults([]);
        } finally {
            setLoading(false);
        }
    };

    const handleSuggestion = (text: string) => {
        setQuery(text);
    };

    return (
        <div className="bg-gray-50 min-h-[calc(100vh-4rem)]">
            <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-10">
                {/* Hero Header */}
                <div className="text-center mb-10 space-y-4">
                    <div className="inline-flex items-center gap-2 px-4 py-1.5 bg-primary/5 text-primary rounded-full text-2xl font-bold uppercase tracking-widest">
                        <Sparkles className="h-3.5 w-3.5" />
                        Discovery Agent
                    </div>

                    <p className="text-gray-500 max-w-lg mx-auto">
                        Search across all indexed marketplaces and repositories by architecture, technology, capability, or natural language.
                    </p>
                </div>

                {/* Search Box */}
                <div className="max-w-3xl mx-auto mb-8 space-y-3">
                    <form onSubmit={handleSearch} className="relative group">
                        <div className="absolute -inset-1 bg-gradient-to-r from-primary/20 via-indigo-500/20 to-primary/20 rounded-2xl blur-lg opacity-0 group-hover:opacity-60 group-focus-within:opacity-80 transition-opacity duration-500" />
                        <div className="relative flex items-center gap-2 bg-white border-2 border-gray-100 rounded-2xl p-2 shadow-xl shadow-gray-200/30 hover:border-gray-200 focus-within:border-primary/40 transition-all">
                            <div className="pl-3">
                                <Search className="h-5 w-5 text-gray-300" />
                            </div>
                            <input
                                type="text"
                                className="flex-1 py-3 px-2 bg-transparent focus:outline-none text-gray-900 placeholder:text-gray-400 font-medium"
                                placeholder="e.g. agent orchestration framework, vector search, REST API..."
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                                disabled={loading}
                            />
                            <button
                                type="button"
                                onClick={() => setShowFilters(!showFilters)}
                                className={`p-2.5 rounded-xl transition-colors ${showFilters
                                    ? "bg-primary/10 text-primary"
                                    : "text-gray-300 hover:text-gray-500 hover:bg-gray-50"
                                    }`}
                            >
                                {showFilters ? <X className="h-4 w-4" /> : <SlidersHorizontal className="h-4 w-4" />}
                            </button>
                            <button
                                type="submit"
                                disabled={loading || !query.trim()}
                                className="bg-gray-900 text-white px-6 py-3 rounded-xl font-bold hover:bg-primary disabled:opacity-20 disabled:hover:bg-gray-900 flex items-center gap-2 transition-all active:scale-95 shadow-lg shadow-black/10"
                            >
                                {loading ? (
                                    <Loader2 className="h-5 w-5 animate-spin" />
                                ) : (
                                    <Search className="h-5 w-5" />
                                )}
                                Search
                            </button>
                        </div>
                    </form>

                    {/* Filters Drawer */}
                    {showFilters && (
                        <div className="flex items-center justify-center gap-8 p-4 bg-white border border-gray-100 rounded-xl shadow-sm animate-in slide-in-from-top-2 duration-200">
                            <div className="flex items-center gap-3">
                                <label className="text-xs font-bold text-gray-500 uppercase tracking-wider">
                                    Results
                                </label>
                                <select
                                    value={limit}
                                    onChange={(e) => setLimit(Number(e.target.value))}
                                    className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:ring-primary focus:border-primary"
                                >
                                    <option value="3">3</option>
                                    <option value="5">5</option>
                                    <option value="10">10</option>
                                    <option value="20">20</option>
                                </select>
                            </div>
                            <div className="h-6 w-px bg-gray-200" />
                            <div className="flex items-center gap-3">
                                <label className="text-xs font-bold text-gray-500 uppercase tracking-wider">
                                    Min Similarity
                                </label>
                                <input
                                    type="range"
                                    min="0"
                                    max="0.9"
                                    step="0.05"
                                    value={minScore}
                                    onChange={(e) => setMinScore(Number(e.target.value))}
                                    className="w-24 accent-primary"
                                />
                                <span className="text-sm font-bold text-gray-700 tabular-nums w-10">
                                    {Math.round(minScore * 100)}%
                                </span>
                            </div>
                        </div>
                    )}

                    {/* Quick Suggestions */}
                    {!hasSearched && (
                        <div className="flex items-center justify-center gap-2 flex-wrap pt-2">
                            <span className="text-xs text-gray-400 mr-1">Try:</span>
                            {[
                                "agent orchestration framework",
                                "vector search implementation",
                                "REST API gateway",
                                "ML pipeline",
                            ].map((suggestion) => (
                                <button
                                    key={suggestion}
                                    onClick={() => handleSuggestion(suggestion)}
                                    className="px-3 py-1.5 text-xs font-medium text-gray-500 bg-white border border-gray-100 rounded-full hover:border-primary/30 hover:text-primary transition-all shadow-sm"
                                >
                                    {suggestion}
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                {/* Results */}
                <div className="space-y-5">
                    {/* Results Summary */}
                    {hasSearched && !loading && (
                        <div className="flex items-center justify-between px-1">
                            <div className="flex items-center gap-2 text-sm text-gray-500">
                                <BookOpen className="h-4 w-4" />
                                <span>
                                    <strong className="text-gray-900">{results.length}</strong>{" "}
                                    {results.length === 1 ? "catalog" : "catalogs"} found
                                </span>
                            </div>
                            {results.length > 0 && (
                                <div className="flex items-center gap-1.5 text-xs text-gray-400">
                                    <Info className="h-3.5 w-3.5" />
                                    Sorted by relevance
                                </div>
                            )}
                        </div>
                    )}

                    {/* Loading State */}
                    {loading && (
                        <div className="flex flex-col items-center justify-center py-20 space-y-4">
                            <div className="relative">
                                <div className="h-12 w-12 rounded-full border-4 border-gray-100" />
                                <div className="absolute inset-0 h-12 w-12 rounded-full border-4 border-primary border-t-transparent animate-spin" />
                            </div>
                            <p className="text-sm text-gray-400 font-medium">
                                Searching across the catalog...
                            </p>
                        </div>
                    )}

                    {/* Result Cards */}
                    {!loading &&
                        results.map((item) => (
                            <CatalogCard key={item.repo_id} item={item} />
                        ))}

                    {/* Empty State */}
                    {hasSearched && !loading && results.length === 0 && (
                        <div className="flex flex-col items-center justify-center py-20 space-y-4 text-center">
                            <div className="p-5 bg-gray-100 rounded-full">
                                <Search className="h-10 w-10 text-gray-300" />
                            </div>
                            <div>
                                <h3 className="text-lg font-bold text-gray-700">No catalogs found</h3>
                                <p className="text-sm text-gray-400 max-w-sm mt-1">
                                    Try a different query or lower the minimum similarity threshold
                                    in the filter settings.
                                </p>
                            </div>
                        </div>
                    )}
                </div>
            </main>
        </div>
    );
}
