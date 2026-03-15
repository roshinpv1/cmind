import { useState, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
    Coins,
    Lightbulb,
    Target,
    GitBranch,
    AlertTriangle,
    Gauge,
    Store,
    Bot,
    GitFork,
    MessageSquareCode,
    Scale,
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
    org?: string;
    estimated_cost: number;
    business_functionalities: string[];
    reasoning?: string; // Appears during Discovery Agent mode
    status?: string;
    source_gap?: string;
    search_count?: number;
    view_count?: number;
    popularity_points?: number;
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

// Safely convert any value to a renderable string — handles objects from LLM
function safeStr(v: any): string {
    if (typeof v === 'string') return v;
    if (v == null) return '';
    if (typeof v === 'number' || typeof v === 'boolean') return String(v);
    if (typeof v === 'object') {
        return v.component_name || v.name || v.title || v.risk || v.description || v.label || v.value || JSON.stringify(v);
    }
    return String(v);
}

function CatalogCard({ item: rawItem }: { item: CatalogResult }) {
    const [expanded, setExpanded] = useState(false);

    // Normalize null/undefined fields to safe defaults — use safeStr to guard against objects from LLM
    const item = {
        ...rawItem,
        topics: (rawItem.topics ?? []).map(safeStr),
        pros: (rawItem.pros ?? []).map(safeStr),
        cons: (rawItem.cons ?? []).map(safeStr),
        architecture: safeStr(rawItem.architecture ?? ""),
        description: safeStr(rawItem.description ?? ""),
        summary_detailed: safeStr(rawItem.summary_detailed ?? ""),
        tech_stack: Array.isArray(rawItem.tech_stack) ? rawItem.tech_stack.join(", ") : safeStr(rawItem.tech_stack ?? ""),
        specification: typeof rawItem.specification === 'object' ? JSON.stringify(rawItem.specification) : (rawItem.specification ?? ""),
        repo_url: safeStr(rawItem.repo_url ?? ""),
        branch: safeStr(rawItem.branch ?? ""),
        org: safeStr(rawItem.org ?? ""),
        category: safeStr(rawItem.category ?? ""),
        estimated_cost: rawItem.estimated_cost ?? 0,
        business_functionalities: (rawItem.business_functionalities ?? []).map(safeStr),
        reasoning: safeStr(rawItem.reasoning ?? ""),
        search_count: rawItem.search_count ?? 0,
        view_count: rawItem.view_count ?? 0,
        popularity_points: rawItem.popularity_points ?? 0,
    };

    // Parse specification JSON
    let specObj: { key_apis?: string[]; interfaces?: string[]; contracts?: string[] } | null = null;
    let specArray: string[] | null = null;
    try {
        if (item.specification) {
            const parsed = JSON.parse(item.specification);
            if (Array.isArray(parsed)) {
                specArray = parsed;
            } else {
                specObj = parsed;
            }
        }
    } catch (e) {
        // Not valid JSON string. If it's actually an array passed as string, we try an eval fallback or just leave null
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
                            {item.popularity_points > 0 && (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold bg-orange-50 text-orange-700 ring-1 ring-orange-200 shadow-sm shrink-0 ml-2">
                                    <Sparkles className="h-3 w-3" />
                                    {item.popularity_points} Popularity
                                </span>
                            )}
                            {item.search_count > 0 && (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold bg-blue-50 text-blue-700 ring-1 ring-blue-200 shadow-sm shrink-0 ml-1">
                                    <Search className="h-3 w-3" />
                                    {item.search_count} Searches
                                </span>
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
                            {item.org && (
                                <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-xs font-bold bg-teal-50 text-teal-700 ring-1 ring-teal-100">
                                    {item.org}
                                </span>
                            )}
                            {item.estimated_cost > 0 && (
                                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-xs font-bold bg-green-50 text-green-700 ring-1 ring-green-200 shadow-sm ml-2">
                                    <Coins className="h-3.5 w-3.5 text-green-600" />
                                    ${item.estimated_cost.toLocaleString()} Est. Cost
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="shrink-0 text-right space-y-2">
                        <QualityBadge score={item.quality_score} />
                        <ScoreBar score={item.score} />
                        {(rawItem.status === "proposed" || rawItem.status === "qualified") && (
                            <div className="flex flex-col items-end gap-1.5 mt-2">
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-50 text-amber-700 ring-1 ring-amber-200">
                                    <Sparkles className="h-3 w-3" />
                                    Proposed
                                </span>
                                <button
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        const params = new URLSearchParams({
                                            gap_name: item.repo_name,
                                            repo_id: item.repo_id,
                                            contribute: "true",
                                        });
                                        window.location.href = `/admin/catalogs/propose?${params.toString()}`;
                                    }}
                                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[11px] font-bold bg-emerald-600 text-white hover:bg-emerald-700 shadow-sm transition-colors"
                                >
                                    ⚡ Contribute
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Discovery Agent Reasoning Block */}
            {item.reasoning && (
                <div className="px-6 py-4 mx-4 mb-4 bg-primary/5 rounded-xl border border-primary/20">
                    <div className="flex items-center gap-2 mb-2">
                        <Lightbulb className="h-4 w-4 text-primary" />
                        <span className="text-xs font-bold tracking-widest uppercase text-primary">Architectural Reasoning</span>
                    </div>
                    <div className="prose prose-sm prose-compact max-w-none">
                        <Markdown remarkPlugins={[remarkGfm]}>{item.reasoning}</Markdown>
                    </div>
                </div>
            )}

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
                            key={safeStr(topic)}
                            className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-gray-50 text-gray-500 border border-gray-100 hover:bg-gray-100 hover:text-gray-700 transition-colors cursor-default"
                        >
                            {safeStr(topic)}
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
                            <div className="prose prose-sm max-w-none">
                                <Markdown remarkPlugins={[remarkGfm]}>{item.summary_detailed}</Markdown>
                            </div>
                        </div>
                    )}

                    {/* Business Functionalities */}
                    {item.business_functionalities && item.business_functionalities.length > 0 && (
                        <div className="px-6 py-5 border-b border-gray-100">
                            <h4 className="text-xs font-black uppercase tracking-widest text-gray-400 mb-3">
                                Business Functionalities
                            </h4>
                            <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm text-gray-700">
                                {item.business_functionalities.map((func, i) => (
                                    <li key={i} className="flex items-start gap-2">
                                        <span className="text-indigo-500 mt-0.5">•</span>
                                        {safeStr(func)}
                                    </li>
                                ))}
                            </ul>
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
                    {specObj && Object.keys(specObj).length > 0 && (
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
                    {specArray && specArray.length > 0 && (
                        <div className="px-6 py-5 border-b border-gray-100">
                            <h4 className="text-xs font-black uppercase tracking-widest text-gray-400 mb-3">
                                Specification Details
                            </h4>
                            <ul className="grid grid-cols-1 gap-2">
                                {specArray.map((specStr, i) => (
                                    <li key={i} className="text-xs text-gray-700 flex items-start gap-2">
                                        <span className="text-indigo-500 mt-0.5 font-bold">↳</span> {String(specStr)}
                                    </li>
                                ))}
                            </ul>
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
                                                    {safeStr(pro)}
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
                                                    {safeStr(con)}
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

    // Modes
    const [searchMode, setSearchMode] = useState<"catalog" | "discovery">("catalog");

    // Discovery State
    const [discoveryJobId, setDiscoveryJobId] = useState<string | null>(null);
    const [discoveryLogs, setDiscoveryLogs] = useState<string[]>([]);
    const [discoveryResult, setDiscoveryResult] = useState<any>(null);
    const [proposedGapMatches, setProposedGapMatches] = useState<Record<string, any>>({});

    // Build vs Reuse State (separate from discovery)
    const [bvrJobId, setBvrJobId] = useState<string | null>(null);
    const [bvrLoading, setBvrLoading] = useState(false);
    const [bvrLogs, setBvrLogs] = useState<string[]>([]);
    const [bvrResult, setBvrResult] = useState<any>(null);

    // Filters
    const [limit, setLimit] = useState(5);
    const [minScore, setMinScore] = useState(0.5);

    // Discovery Polling Hook
    useEffect(() => {
        let interval: ReturnType<typeof setInterval>;
        if (discoveryJobId && loading) {
            interval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/v1/agents/autonomous/${discoveryJobId}/status`);
                    if (res.ok) {
                        const data = await res.json();
                        setDiscoveryLogs(data.logs || []);

                        if (data.status === "completed" || data.status === "failed") {
                            clearInterval(interval);
                            setLoading(false);

                            // Fetch final result
                            const resultRes = await fetch(`/api/v1/agents/autonomous/${discoveryJobId}/result`);
                            if (resultRes.ok) {
                                const resultData = await resultRes.json();
                                // Parse answer if it's a JSON string
                                if (resultData.answer && typeof resultData.answer === "string") {
                                    try {
                                        resultData.answer = JSON.parse(resultData.answer);
                                    } catch (_) { /* keep as string */ }
                                }
                                console.log("[BvR] Result data:", resultData);
                                setDiscoveryResult(resultData);

                                // Check gaps against proposed catalog entries
                                const gaps = resultData?.answer?.gaps || [];
                                if (gaps.length > 0) {
                                    try {
                                        const matchRes = await fetch("/api/v1/catalogs/match-gaps", {
                                            method: "POST",
                                            headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({ gaps }),
                                        });
                                        if (matchRes.ok) {
                                            const matchData = await matchRes.json();
                                            setProposedGapMatches(matchData);
                                        }
                                    } catch (e) {
                                        console.warn("Failed to match gaps to proposed entries", e);
                                    }
                                }
                            }
                        }
                    }
                } catch (e) {
                    console.error("Polling error", e);
                }
            }, 2000);
        }
        return () => clearInterval(interval);
    }, [discoveryJobId, loading]);

    // Build vs Reuse Polling Hook
    useEffect(() => {
        let interval: ReturnType<typeof setInterval>;
        if (bvrJobId && bvrLoading) {
            interval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/v1/agents/autonomous/${bvrJobId}/status`);
                    if (res.ok) {
                        const data = await res.json();
                        setBvrLogs(data.logs || []);
                        if (data.status === "completed" || data.status === "failed") {
                            clearInterval(interval);
                            setBvrLoading(false);
                            const resultRes = await fetch(`/api/v1/agents/autonomous/${bvrJobId}/result`);
                            if (resultRes.ok) {
                                const resultData = await resultRes.json();
                                if (resultData.answer && typeof resultData.answer === "string") {
                                    try { resultData.answer = JSON.parse(resultData.answer); } catch (_) { }
                                }
                                console.log("[BvR] Result data:", resultData);
                                setBvrResult(resultData);
                            }
                        }
                    }
                } catch (e) { console.error("BvR polling error", e); }
            }, 2000);
        }
        return () => clearInterval(interval);
    }, [bvrJobId, bvrLoading]);

    // Trigger Build vs Reuse analysis
    const handleBuildVsReuse = async () => {
        if (!query.trim()) return;
        setBvrLoading(true);
        setBvrJobId(null);
        setBvrLogs([]);
        setBvrResult(null);
        try {
            const res = await fetch("/api/v1/agents/autonomous", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    goal: query,
                    max_iterations: 15,
                    allowed_playbooks: ["evaluate_build_vs_reuse"]
                }),
            });
            if (res.ok) {
                const data = await res.json();
                setBvrJobId(data.job_id);
            } else {
                console.error("BvR trigger failed");
                setBvrLoading(false);
            }
        } catch (err) {
            console.error("BvR error:", err);
            setBvrLoading(false);
        }
    };

    const handleTrendingSearch = async (sortBy: "popularity_points" | "search_count") => {
        setLoading(true);
        setHasSearched(true);
        setResults([]);
        setDiscoveryJobId(null);
        setDiscoveryLogs([]);
        setDiscoveryResult(null);
        setBvrJobId(null);
        setBvrLoading(false);
        setBvrLogs([]);
        setBvrResult(null);
        setSearchMode("catalog");
        setQuery(sortBy === "popularity_points" ? "🔥 Trending Components" : "⭐ Most Popular Components");

        try {
            const res = await fetch(`/api/v1/catalogs/trending?sort_by=${sortBy}&limit=${limit}`);
            if (res.ok) {
                const data = await res.json();
                setResults(data);
            } else {
                console.error("Trending fetch failed:", res.statusText);
                setResults([]);
            }
        } catch (err) {
            console.error("Trending fetch error:", err);
            setResults([]);
        } finally {
            setLoading(false);
        }
    };

    const handleSearch = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!query.trim()) return;

        setLoading(true);
        setHasSearched(true);
        setResults([]);
        setDiscoveryJobId(null);
        setDiscoveryLogs([]);
        setDiscoveryResult(null);
        setBvrJobId(null);
        setBvrLoading(false);
        setBvrLogs([]);
        setBvrResult(null);

        if (searchMode === "discovery") {
            try {
                const playbook = "design_solution";
                const res = await fetch("/api/v1/agents/autonomous", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        goal: query,
                        max_iterations: 15,
                        allowed_playbooks: [playbook]
                    }),
                });

                if (res.ok) {
                    const data = await res.json();
                    setDiscoveryJobId(data.job_id);
                } else {
                    console.error("Request trigger failed");
                    setLoading(false);
                }
            } catch (err) {
                console.error("Request error:", err);
                setLoading(false);
            }
            return;
        }

        // Standard Catalog Search
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

                    <p className="text-gray-700 text-lg max-w-xlg mx-auto">
                        Search across all indexed marketplaces and repositories by architecture, technology, capability, or natural language.
                    </p>

                    {/* Feature Icons */}
                    <div className="flex items-center justify-center gap-3 pt-3 flex-wrap">
                        {[
                            { icon: Store, label: "IDP", color: "text-violet-600 bg-violet-50 ring-violet-200/60" },
                            { icon: Bot, label: "Tachyon Agent Marketplace", color: "text-blue-600 bg-blue-50 ring-blue-200/60" },
                            { icon: GitFork, label: "Enterprise Git", color: "text-orange-600 bg-orange-50 ring-orange-200/60" },
                            { icon: MessageSquareCode, label: "Prompt Library", color: "text-emerald-600 bg-emerald-50 ring-emerald-200/60" },
                        ].map(({ icon: Icon, label, color }) => (
                            <div
                                key={label}
                                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold ring-1 ${color} hover:shadow-sm transition-all cursor-default`}
                            >
                                <Icon className="h-3.5 w-3.5" />
                                {label}
                            </div>
                        ))}
                    </div>
                </div>

                {/* Mode Toggle */}
                <div className="flex justify-center mb-8">
                    <div className="inline-flex items-center p-1 bg-gray-100 rounded-xl space-x-1">
                        <button
                            onClick={() => setSearchMode("catalog")}
                            className={`px-6 py-2.5 text-sm font-bold rounded-lg transition-all ${searchMode === "catalog"
                                ? "bg-white text-gray-900 shadow-sm ring-1 ring-gray-200"
                                : "text-gray-500 hover:text-gray-700 hover:bg-gray-50/50"
                                }`}
                        >
                            <span className="flex items-center gap-2">
                                <Search className="h-4 w-4" /> Catalog Search
                            </span>
                        </button>
                        <button
                            onClick={() => setSearchMode("discovery")}
                            className={`px-6 py-2.5 text-sm font-bold rounded-lg transition-all ${searchMode === "discovery"
                                ? "bg-white text-gray-900 shadow-sm ring-1 ring-gray-200"
                                : "text-gray-500 hover:text-gray-700 hover:bg-gray-50/50"
                                }`}
                        >
                            <span className="flex items-center gap-2">
                                <Lightbulb className="h-4 w-4" /> Intelligent Discovery
                            </span>
                        </button>

                    </div>
                </div>

                {/* Search Box */}
                <div className="max-w-3xl mx-auto mb-8 space-y-3">
                    <form onSubmit={handleSearch} className="relative group">
                        <div className="absolute -inset-1 bg-gradient-to-r from-primary/20 via-indigo-500/20 to-primary/20 rounded-2xl blur-lg opacity-0 group-hover:opacity-60 group-focus-within:opacity-80 transition-opacity duration-500" />
                        <div className="relative flex items-center gap-2 bg-white border-2 border-gray-100 rounded-2xl p-2 shadow-xl shadow-gray-200/30 hover:border-gray-200 focus-within:border-primary/40 transition-all">
                            <div className="pl-3">
                                {searchMode === "discovery" ? (
                                    <Lightbulb className="h-5 w-5 text-indigo-400" />
                                ) : (
                                    <Search className="h-5 w-5 text-gray-300" />
                                )}
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

                    {/* Quick Suggestions & Trending */}
                    {!hasSearched && (
                        <div className="flex flex-col items-center justify-center pt-6 space-y-4">
                            <div className="flex items-center gap-4">
                                <button
                                    onClick={() => handleTrendingSearch("popularity_points")}
                                    className="group flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-orange-50 to-red-50 border border-orange-200 rounded-xl hover:border-orange-400 hover:shadow-md transition-all shrink-0"
                                >
                                    <div className="p-1.5 bg-white rounded-lg group-hover:scale-110 transition-transform shadow-sm">
                                        <Sparkles className="h-4 w-4 text-orange-500" />
                                    </div>
                                    <div className="text-left">
                                        <div className="text-xs font-black uppercase tracking-wider text-orange-600">Trending Now</div>
                                        <div className="text-[10px] text-orange-400 font-medium leading-tight">Fastest growing</div>
                                    </div>
                                </button>
                                <button
                                    onClick={() => handleTrendingSearch("search_count")}
                                    className="group flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-xl hover:border-blue-400 hover:shadow-md transition-all shrink-0"
                                >
                                    <div className="p-1.5 bg-white rounded-lg group-hover:scale-110 transition-transform shadow-sm">
                                        <Star className="h-4 w-4 text-blue-500" />
                                    </div>
                                    <div className="text-left">
                                        <div className="text-xs font-black uppercase tracking-wider text-blue-600">Most Popular</div>
                                        <div className="text-[10px] text-blue-400 font-medium leading-tight">Most frequently searched</div>
                                    </div>
                                </button>
                            </div>

                            <div className="flex items-center justify-center gap-2 flex-wrap pt-2">
                                <span className="text-xs text-gray-400 mr-1">Or try:</span>
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
                        </div>
                    )}
                </div>

                {/* Results */}
                <div className="space-y-5">
                    {searchMode === "catalog" && (
                        <>
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
                        </>
                    )}

                    {searchMode === "discovery" && (
                        <>
                            {/* Discovery Loading & Logs */}
                            {loading && (
                                <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
                                    <div className="p-4 bg-gray-50/50 border-b border-gray-100 flex items-center justify-between">
                                        <div className="flex items-center gap-2">
                                            <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                            <h3 className="text-sm font-bold text-gray-700">Synthesizing Architecture...</h3>
                                        </div>
                                    </div>
                                    <div className="p-4 space-y-2 max-h-60 overflow-y-auto text-xs">
                                        {discoveryLogs.map((log, i) => (
                                            <div key={i} className="text-gray-500 flex gap-2">
                                                <span className="text-gray-300 shrink-0">[{new Date().toLocaleTimeString()}]</span>
                                                <div className="prose prose-sm prose-compact max-w-none flex-1">
                                                    <Markdown remarkPlugins={[remarkGfm]}>{log}</Markdown>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Discovery Final Result (Structured JSON) */}
                            {!loading && discoveryResult && discoveryResult.answer && discoveryResult.answer.catalog_matches && (
                                <div className="space-y-6">
                                    {/* ─── Requirement Summary + Confidence ─── */}
                                    <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
                                        <div className="flex items-start justify-between gap-6">
                                            <div className="flex-1">
                                                <div className="flex items-center gap-2 mb-2">
                                                    <Target className="h-5 w-5 text-primary" />
                                                    <h2 className="text-lg font-black text-gray-900">Requirement Analysis</h2>
                                                </div>
                                                <p className="text-sm text-gray-600 leading-relaxed">
                                                    {safeStr(discoveryResult.answer.requirement_summary || discoveryResult.goal || "—")}
                                                </p>
                                            </div>
                                            {discoveryResult.answer.overall_confidence_score > 0 && (
                                                <div className="shrink-0 flex flex-col items-center gap-1 px-4 py-3 bg-gray-50 rounded-xl">
                                                    <Gauge className="h-5 w-5 text-gray-400" />
                                                    <span className="text-2xl font-black text-gray-900 tabular-nums">
                                                        {discoveryResult.answer.overall_confidence_score}%
                                                    </span>
                                                    <span className="text-[10px] font-bold uppercase tracking-widest text-gray-400">Confidence</span>
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    {/* ─── Capabilities & Decomposition Grid ─── */}
                                    {(() => {
                                        const caps = discoveryResult.answer.capabilities;
                                        const decomp = discoveryResult.answer.decomposition;
                                        // Normalize: handle both string[] and object[] from LLM
                                        const toStringList = (arr: any): string[] => {
                                            if (!arr) return [];
                                            const items = Array.isArray(arr) ? arr : (arr?.items || arr?.functional || arr?.core_modules || Object.values(arr || {}).flat());
                                            return (items || []).map((item: any) => {
                                                if (typeof item === 'string') return item;
                                                if (typeof item === 'object' && item !== null) return item.component_name || item.name || item.description || JSON.stringify(item);
                                                return String(item);
                                            });
                                        };
                                        const capList = toStringList(caps);
                                        const decompList = toStringList(decomp);
                                        const hasCaps = capList && capList.length > 0;
                                        const hasDecomp = decompList && decompList.length > 0;

                                        if (!hasCaps && !hasDecomp) return null;

                                        return (
                                            <div className={`grid gap-4 ${hasCaps && hasDecomp ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1'}`}>
                                                {hasCaps && (
                                                    <div className="bg-white p-5 rounded-2xl shadow-sm border border-gray-100">
                                                        <div className="flex items-center gap-2 mb-4">
                                                            <Sparkles className="h-4 w-4 text-indigo-500" />
                                                            <h3 className="text-xs font-bold text-gray-500 uppercase tracking-widest">Identified Capabilities</h3>
                                                        </div>
                                                        <ul className="space-y-2">
                                                            {capList.map((cap: string, i: number) => (
                                                                <li key={i} className="text-sm text-gray-700 flex items-start gap-2">
                                                                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-indigo-400 shrink-0" />
                                                                    {cap}
                                                                </li>
                                                            ))}
                                                        </ul>
                                                    </div>
                                                )}
                                                {hasDecomp && (
                                                    <div className="bg-white p-5 rounded-2xl shadow-sm border border-gray-100">
                                                        <div className="flex items-center gap-2 mb-4">
                                                            <GitBranch className="h-4 w-4 text-emerald-500" />
                                                            <h3 className="text-xs font-bold text-gray-500 uppercase tracking-widest">Module Decomposition</h3>
                                                        </div>
                                                        <ul className="space-y-2">
                                                            {decompList.map((mod: string, i: number) => (
                                                                <li key={i} className="text-sm text-gray-700 flex items-start gap-2">
                                                                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-emerald-400 shrink-0" />
                                                                    {mod}
                                                                </li>
                                                            ))}
                                                        </ul>
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })()}

                                    {/* ─── Architecture Composition ─── */}
                                    {discoveryResult.answer.architecture_composition && (
                                        <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
                                            <div className="flex items-center gap-2 mb-3">
                                                <Layers className="h-5 w-5 text-gray-400" />
                                                <h2 className="text-lg font-black text-gray-900">Architecture Composition</h2>
                                            </div>
                                            <div className="prose prose-sm max-w-none">
                                                <Markdown remarkPlugins={[remarkGfm]}>{
                                                    typeof discoveryResult.answer.architecture_composition === 'string'
                                                        ? discoveryResult.answer.architecture_composition
                                                        : JSON.stringify(discoveryResult.answer.architecture_composition, null, 2)
                                                }</Markdown>
                                            </div>
                                        </div>
                                    )}

                                    {/* ─── Layered Architecture View ─── */}
                                    {(() => {
                                        const LAYERS = ["Presentation", "Business Logic", "Data & Storage", "Infrastructure"] as const;
                                        const layerStyles: Record<string, { bg: string; border: string; label: string; icon: string }> = {
                                            "Presentation": { bg: "bg-violet-50/60", border: "border-violet-200", label: "text-violet-700", icon: "🖥️" },
                                            "Business Logic": { bg: "bg-blue-50/60", border: "border-blue-200", label: "text-blue-700", icon: "⚙️" },
                                            "Data & Storage": { bg: "bg-amber-50/60", border: "border-amber-200", label: "text-amber-700", icon: "🗄️" },
                                            "Infrastructure": { bg: "bg-slate-50/60", border: "border-slate-200", label: "text-slate-700", icon: "🏗️" },
                                        };

                                        // Classify catalog matches into layers
                                        const matches = discoveryResult.answer.catalog_matches || [];
                                        const layerMap: Record<string, { matches: any[]; gaps: any[] }> = {};
                                        LAYERS.forEach(l => { layerMap[l] = { matches: [], gaps: [] }; });

                                        for (const m of matches) {
                                            const layer = m.architecture_layer || "Business Logic";
                                            const key = LAYERS.includes(layer) ? layer : "Business Logic";
                                            layerMap[key].matches.push(m);
                                        }

                                        // Classify gaps (handle both string[] and object[] formats)
                                        const rawGaps = discoveryResult.answer.gaps || [];
                                        for (const g of rawGaps) {
                                            if (typeof g === "string") {
                                                layerMap["Business Logic"].gaps.push({ name: g, description: "" });
                                            } else {
                                                const layer = g.architecture_layer || "Business Logic";
                                                const key = LAYERS.includes(layer) ? layer : "Business Logic";
                                                layerMap[key].gaps.push(g);
                                            }
                                        }

                                        // Only show layers that have content
                                        const activeLayers = LAYERS.filter(l => layerMap[l].matches.length > 0 || layerMap[l].gaps.length > 0);
                                        if (activeLayers.length === 0) return null;

                                        return (
                                            <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
                                                <div className="px-6 pt-5 pb-3 flex items-center gap-2">
                                                    <Layers className="h-5 w-5 text-primary" />
                                                    <h2 className="text-lg font-black text-gray-900">Architecture Layers</h2>
                                                </div>
                                                <div className="px-4 pb-5 space-y-1">
                                                    {activeLayers.map((layer) => {
                                                        const style = layerStyles[layer];
                                                        const { matches: lMatches, gaps: lGaps } = layerMap[layer];
                                                        return (
                                                            <div key={layer} className={`${style.bg} border ${style.border} rounded-xl px-4 py-3`}>
                                                                <div className={`text-[10px] font-black uppercase tracking-[0.15em] ${style.label} mb-2 flex items-center gap-1.5`}>
                                                                    <span>{style.icon}</span> {layer}
                                                                </div>
                                                                <div className="flex flex-wrap gap-2">
                                                                    {lMatches.map((m: any, i: number) => {
                                                                        const name = m.component_name || m.catalog_entry?.repo_name || "Component";
                                                                        const score = m.confidence_score || 0;
                                                                        const matchType = m.match_type || "";
                                                                        const compOrg = m.catalog_entry?.org || "";
                                                                        const opacity = score >= 70 ? "opacity-100" : score >= 40 ? "opacity-85" : "opacity-70";
                                                                        return (
                                                                            <div
                                                                                key={i}
                                                                                className={`group relative flex items-center gap-2 px-3 py-2 bg-white rounded-lg border border-gray-200 shadow-sm hover:shadow-md transition-all ${opacity}`}
                                                                                title={m.reasoning || ""}
                                                                            >
                                                                                <div className={`h-2 w-2 rounded-full shrink-0 ${score >= 70 ? "bg-emerald-500" : score >= 40 ? "bg-amber-400" : "bg-gray-300"}`} />
                                                                                <span className="text-xs font-bold text-gray-800">{name}</span>
                                                                                {compOrg && (
                                                                                    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-teal-50 text-teal-600">
                                                                                        {compOrg}
                                                                                    </span>
                                                                                )}
                                                                                {matchType && (
                                                                                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full ${matchType === "Full Match" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                                                                                        {matchType === "Full Match" ? "Full" : "Partial"}
                                                                                    </span>
                                                                                )}
                                                                                <span className="text-[10px] font-mono text-gray-400">{score}%</span>
                                                                            </div>
                                                                        );
                                                                    })}
                                                                    {lGaps.map((g: any, i: number) => {
                                                                        const gapKey = safeStr(g.name || g);
                                                                        const proposedMatch = proposedGapMatches[gapKey];
                                                                        const isProposed = !!proposedMatch;
                                                                        return (
                                                                            <div
                                                                                key={`gap-${i}`}
                                                                                className={`flex items-center gap-2 px-3 py-2 bg-white/50 rounded-lg border-2 border-dashed cursor-pointer transition-all group ${isProposed
                                                                                    ? "border-violet-300 hover:border-violet-500 hover:bg-violet-50/50"
                                                                                    : "border-amber-300 hover:border-amber-500 hover:bg-amber-50/50"
                                                                                    }`}
                                                                                title={isProposed
                                                                                    ? `Proposed by ${proposedMatch.created_by || "someone"} — click to contribute`
                                                                                    : (g.description || "Click to create a proposal for this gap")
                                                                                }
                                                                                onClick={() => {
                                                                                    if (isProposed) {
                                                                                        // Navigate to promote/contribute page for existing proposal
                                                                                        const params = new URLSearchParams({
                                                                                            gap_name: gapKey,
                                                                                            repo_id: proposedMatch.repo_id,
                                                                                            contribute: "true",
                                                                                        });
                                                                                        window.location.href = `/admin/catalogs/propose?${params.toString()}`;
                                                                                    } else {
                                                                                        const params = new URLSearchParams({
                                                                                            gap_name: gapKey,
                                                                                            gap_description: safeStr(g.description || ""),
                                                                                            architecture_layer: safeStr(g.architecture_layer || layer),
                                                                                            user_query: query,
                                                                                        });
                                                                                        if (g.build_cost_usd) params.set("build_cost_usd", String(g.build_cost_usd));
                                                                                        if (g.dev_weeks) params.set("dev_weeks", String(g.dev_weeks));
                                                                                        window.location.href = `/admin/catalogs/propose?${params.toString()}`;
                                                                                    }
                                                                                }}
                                                                            >
                                                                                <div className={`h-2 w-2 rounded-full shrink-0 ${isProposed ? "bg-violet-500" : "bg-amber-400 animate-pulse"}`} />
                                                                                <span className="text-xs font-bold text-gray-500">{gapKey}</span>
                                                                                {isProposed ? (
                                                                                    <>
                                                                                        <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-violet-100 text-violet-700">Proposed</span>
                                                                                        <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 opacity-0 group-hover:opacity-100 transition-opacity">
                                                                                            ⚡ Contribute
                                                                                        </span>
                                                                                    </>
                                                                                ) : (
                                                                                    <>
                                                                                        <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-600">Gap</span>
                                                                                        <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-violet-50 text-violet-600 opacity-0 group-hover:opacity-100 transition-opacity">
                                                                                            + Propose
                                                                                        </span>
                                                                                    </>
                                                                                )}
                                                                            </div>
                                                                        );
                                                                    })}
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            </div>
                                        );
                                    })()}

                                    {/* Catalog Match Cards — deduplicated by component */}
                                    <div className="space-y-4">
                                        <h3 className="text-sm font-bold text-gray-500 uppercase tracking-widest pl-2">Selected Components</h3>
                                        {(() => {
                                            // Deduplicate: group matches by component_name (or repo_url fallback)
                                            const grouped = new Map<string, { entry: any; reasonings: string[]; maxScore: number }>();
                                            for (const match of discoveryResult.answer.catalog_matches) {
                                                const entry = match.catalog_entry || {};
                                                const key = entry.repo_url || match.component_name || `unknown-${Math.random()}`;
                                                if (grouped.has(key)) {
                                                    const g = grouped.get(key)!;
                                                    if (match.reasoning) g.reasonings.push(match.reasoning);
                                                    g.maxScore = Math.max(g.maxScore, match.confidence_score || 0);
                                                } else {
                                                    grouped.set(key, {
                                                        entry,
                                                        reasonings: match.reasoning ? [match.reasoning] : [],
                                                        maxScore: match.confidence_score || 0,
                                                    });
                                                }
                                            }
                                            return Array.from(grouped.entries()).map(([_key, { entry, reasonings, maxScore }], idx) => {
                                                const combinedReasoning = reasonings.length > 1
                                                    ? reasonings.map((r, i) => `${i + 1}. ${r}`).join("\n")
                                                    : reasonings[0] || "";
                                                const resultObj: CatalogResult = {
                                                    repo_id: `discovery-${idx}`,
                                                    repo_name: entry.repo_name || "",
                                                    repo_url: entry.repo_url || "",
                                                    score: maxScore / 100,
                                                    category: entry.category || "",
                                                    description: entry.description || "",
                                                    summary_detailed: entry.description || "",
                                                    architecture: entry.architecture || "",
                                                    tech_stack: Array.isArray(entry.tech_stack) ? entry.tech_stack.join(", ") : (entry.tech_stack || ""),
                                                    specification: "",
                                                    estimated_cost: entry.estimated_cost || 0,
                                                    business_functionalities: entry.business_functionalities || [],
                                                    reasoning: combinedReasoning,
                                                    topics: entry.topics || [],
                                                    quality_score: entry.quality_score || 0,
                                                    pros: entry.pros || [],
                                                    cons: entry.cons || [],
                                                    branch: entry.branch || "main",
                                                    org: entry.org || "",
                                                };
                                                return <CatalogCard key={idx} item={resultObj} />;
                                            });
                                        })()}
                                    </div>

                                    {/* Gaps & Custom Build Needs */}
                                    {discoveryResult.answer.gaps && discoveryResult.answer.gaps.length > 0 && (
                                        <div className="bg-amber-50 rounded-2xl border border-amber-200 p-6">
                                            <div className="flex items-center gap-2 mb-4">
                                                <Info className="h-5 w-5 text-amber-600" />
                                                <h3 className="text-sm font-bold text-amber-900 uppercase tracking-wider">Identified Gaps (Custom Build Required)</h3>
                                            </div>
                                            <ul className="space-y-2">
                                                {discoveryResult.answer.gaps.map((gap: any, i: number) => {
                                                    const gapName = typeof gap === "string" ? gap : (gap.component_name || gap.name || "Unknown");
                                                    const gapDesc = typeof gap === "object" && gap.description ? ` — ${gap.description}` : "";
                                                    const gapLayer = typeof gap === "object" && gap.architecture_layer ? ` [${gap.architecture_layer}]` : "";
                                                    return (
                                                        <li key={i} className="text-sm text-amber-800 flex items-start gap-2">
                                                            <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-amber-400 shrink-0" />
                                                            <span><strong>{gapName}</strong>{gapDesc}{gapLayer && <span className="text-amber-500 text-xs">{gapLayer}</span>}</span>
                                                        </li>
                                                    );
                                                })}
                                            </ul>
                                        </div>
                                    )}

                                    {/* Risks */}
                                    {discoveryResult.answer.risks && discoveryResult.answer.risks.length > 0 && (
                                        <div className="bg-red-50/60 rounded-2xl border border-red-200 p-6">
                                            <div className="flex items-center gap-2 mb-4">
                                                <AlertTriangle className="h-5 w-5 text-red-500" />
                                                <h3 className="text-sm font-bold text-red-800 uppercase tracking-wider">Risks & Considerations</h3>
                                            </div>
                                            <ul className="space-y-2">
                                                {discoveryResult.answer.risks.map((riskItem: any, i: number) => {
                                                    const riskText = typeof riskItem === 'string' ? riskItem : (riskItem.risk || riskItem.name || riskItem.description || JSON.stringify(riskItem));
                                                    const mitigation = typeof riskItem === 'object' && riskItem.mitigation ? riskItem.mitigation : null;
                                                    return (
                                                        <li key={i} className="text-sm text-red-700 flex items-start gap-2">
                                                            <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-red-400 shrink-0" />
                                                            <span>
                                                                {riskText}
                                                                {mitigation && <span className="text-red-500/70 text-xs ml-1">→ {mitigation}</span>}
                                                            </span>
                                                        </li>
                                                    );
                                                })}
                                            </ul>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* ═══ Build vs Reuse Section (inline within Discovery) ═══ */}
                            {!loading && discoveryResult && discoveryResult.answer && discoveryResult.answer.catalog_matches && (
                                <div className="space-y-6">
                                    {/* Trigger Button */}
                                    {!bvrResult && !bvrLoading && (
                                        <button
                                            onClick={handleBuildVsReuse}
                                            className="w-full flex items-center justify-center gap-3 py-4 px-6 bg-gradient-to-r from-indigo-50 to-emerald-50 rounded-2xl border-2 border-dashed border-indigo-200 hover:border-indigo-400 hover:from-indigo-100 hover:to-emerald-100 transition-all group"
                                        >
                                            <Scale className="h-5 w-5 text-indigo-500 group-hover:scale-110 transition-transform" />
                                            <span className="text-sm font-bold text-indigo-700">Run Build vs Reuse Analysis</span>
                                            <span className="text-xs text-indigo-400">Compare build cost vs reusing catalog components</span>
                                        </button>
                                    )}

                                    {/* BvR Loading */}
                                    {bvrLoading && (
                                        <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
                                            <div className="p-4 bg-gray-50/50 border-b border-gray-100 flex items-center gap-2">
                                                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                                                <h3 className="text-sm font-bold text-gray-700">Analyzing Build vs Reuse...</h3>
                                            </div>
                                            <div className="p-4 space-y-2 max-h-60 overflow-y-auto font-mono text-xs">
                                                {bvrLogs.map((log: any, i: number) => (
                                                    <div key={i} className="text-gray-500 flex gap-2">
                                                        <span className="text-gray-300">[{new Date().toLocaleTimeString()}]</span>
                                                        {log}
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {/* BvR Result */}
                                    {bvrResult && bvrResult.answer && bvrResult.answer.comparison && (() => {
                                        const build = bvrResult.answer.build_estimate || {};
                                        const reuse = bvrResult.answer.reuse_estimate || {};
                                        const comp = bvrResult.answer.comparison || {};
                                        const isBuildCheaper = (comp.build_total_usd || 0) < (comp.reuse_total_usd || 0);
                                        const recColor = comp.recommendation === "BUILD"
                                            ? "from-blue-500 to-indigo-600"
                                            : comp.recommendation === "REUSE"
                                                ? "from-emerald-500 to-teal-600"
                                                : "from-amber-500 to-orange-600";
                                        return (
                                            <div className="space-y-4">
                                                {/* Header */}
                                                <div className="bg-white p-5 rounded-2xl shadow-sm border border-gray-100">
                                                    <div className="flex items-center gap-2 mb-1">
                                                        <Scale className="h-5 w-5 text-primary" />
                                                        <h2 className="text-lg font-black text-gray-900">Build vs Reuse Analysis</h2>
                                                    </div>
                                                    <p className="text-sm text-gray-600">{bvrResult.answer.requirement_summary || query}</p>
                                                </div>

                                                {/* Side-by-Side Cards */}
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                    {/* BUILD Card */}
                                                    <div className={`bg-white rounded-2xl shadow-sm border ${!isBuildCheaper ? "border-gray-200" : "border-blue-300 ring-2 ring-blue-100"} p-6`}>
                                                        <div className="flex items-center justify-between mb-4">
                                                            <h3 className="text-sm font-black uppercase tracking-widest text-blue-600">🔨 Build from Scratch</h3>
                                                            {isBuildCheaper && <span className="text-[9px] font-bold px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">CHEAPER</span>}
                                                        </div>
                                                        <div className="text-3xl font-black text-gray-900 mb-4">${(comp.build_total_usd || build.total_cost_usd || 0).toLocaleString()}</div>
                                                        <div className="space-y-3 text-sm">
                                                            <div className="flex justify-between"><span className="text-gray-500">Timeline</span><span className="font-bold text-gray-800">{comp.build_timeline_weeks || build.timeline_weeks || "?"} weeks</span></div>
                                                            <div className="flex justify-between"><span className="text-gray-500">Team Size</span><span className="font-bold text-gray-800">{build.team_size || "?"} developers</span></div>
                                                            <div className="flex justify-between"><span className="text-gray-500">Dev-Months</span><span className="font-bold text-gray-800">{build.dev_months || "?"}</span></div>
                                                            <div className="flex justify-between"><span className="text-gray-500">Complexity</span><span className={`font-bold ${build.complexity === "high" || build.complexity === "extreme" ? "text-red-600" : build.complexity === "medium" ? "text-amber-600" : "text-emerald-600"}`}>{(build.complexity || "medium").charAt(0).toUpperCase() + (build.complexity || "medium").slice(1)}</span></div>
                                                        </div>
                                                        {build.required_skills?.length > 0 && (
                                                            <div className="mt-4 pt-4 border-t border-gray-100">
                                                                <div className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-2">Required Skills</div>
                                                                <div className="flex flex-wrap gap-1">{build.required_skills.map((s: string, i: number) => <span key={i} className="px-2 py-0.5 text-[10px] font-semibold bg-gray-50 text-gray-600 rounded-full border border-gray-100">{s}</span>)}</div>
                                                            </div>
                                                        )}
                                                        {build.key_risks?.length > 0 && (
                                                            <div className="mt-3 pt-3 border-t border-gray-100">
                                                                <div className="text-[10px] font-bold uppercase tracking-widest text-red-400 mb-2">Risks</div>
                                                                <ul className="space-y-1">{build.key_risks.map((r: string, i: number) => <li key={i} className="text-xs text-gray-500 flex items-start gap-1.5"><AlertTriangle className="h-3 w-3 text-red-400 shrink-0 mt-0.5" />{r}</li>)}</ul>
                                                            </div>
                                                        )}
                                                    </div>

                                                    {/* REUSE Card */}
                                                    <div className={`bg-white rounded-2xl shadow-sm border ${isBuildCheaper ? "border-gray-200" : "border-emerald-300 ring-2 ring-emerald-100"} p-6`}>
                                                        <div className="flex items-center justify-between mb-4">
                                                            <h3 className="text-sm font-black uppercase tracking-widest text-emerald-600">♻️ Reuse Existing</h3>
                                                            {!isBuildCheaper && <span className="text-[9px] font-bold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700">CHEAPER</span>}
                                                        </div>
                                                        <div className="text-3xl font-black text-gray-900 mb-4">${(comp.reuse_total_usd || reuse.total_integration_cost_usd || 0).toLocaleString()}</div>
                                                        <div className="space-y-3 text-sm">
                                                            <div className="flex justify-between"><span className="text-gray-500">Timeline</span><span className="font-bold text-gray-800">{comp.reuse_timeline_weeks || reuse.total_timeline_weeks || "?"} weeks</span></div>
                                                            <div className="flex justify-between"><span className="text-gray-500">Components</span><span className="font-bold text-gray-800">{(reuse.components || []).length} matched</span></div>
                                                            <div className="flex justify-between"><span className="text-gray-500">Gaps</span><span className="font-bold text-amber-600">{(reuse.gaps || []).length} to build</span></div>
                                                            <div className="flex justify-between"><span className="text-gray-500">Annual Maintenance</span><span className="font-bold text-gray-800">${(reuse.annual_maintenance_total_usd || 0).toLocaleString()}/yr</span></div>
                                                        </div>
                                                    </div>
                                                </div>

                                                {/* Recommendation Banner */}
                                                <div className={`bg-gradient-to-r ${recColor} rounded-2xl p-6 text-white shadow-lg`}>
                                                    <div className="flex items-center justify-between mb-3">
                                                        <div className="flex items-center gap-3">
                                                            <div className="p-2 bg-white/20 rounded-xl"><Scale className="h-6 w-6" /></div>
                                                            <div>
                                                                <div className="text-xs font-bold uppercase tracking-widest opacity-80">Recommendation</div>
                                                                <div className="text-2xl font-black">{comp.recommendation || "HYBRID"}</div>
                                                            </div>
                                                        </div>
                                                        <div className="text-right">
                                                            <div className="text-3xl font-black">{comp.confidence_score || 0}%</div>
                                                            <div className="text-xs opacity-80">Confidence</div>
                                                        </div>
                                                    </div>
                                                    <div className="text-sm leading-relaxed opacity-90 prose prose-sm prose-compact max-w-none prose-invert">
                                                        <Markdown remarkPlugins={[remarkGfm]}>{comp.reasoning || ""}</Markdown>
                                                    </div>
                                                    {(comp.savings_pct > 0 || comp.time_saved_weeks > 0) && (
                                                        <div className="flex gap-4 mt-4 pt-4 border-t border-white/20">
                                                            {comp.savings_pct > 0 && <div><div className="text-2xl font-black">{comp.savings_pct}%</div><div className="text-xs opacity-80">Cost Saved</div></div>}
                                                            {comp.savings_usd > 0 && <div><div className="text-2xl font-black">${comp.savings_usd.toLocaleString()}</div><div className="text-xs opacity-80">$ Saved</div></div>}
                                                            {comp.time_saved_weeks > 0 && <div><div className="text-2xl font-black">{comp.time_saved_weeks}w</div><div className="text-xs opacity-80">Time Saved</div></div>}
                                                        </div>
                                                    )}
                                                </div>

                                                {/* Reusable Components Breakdown */}
                                                {bvrResult.answer.reuse_estimate?.components?.length > 0 && (
                                                    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
                                                        <div className="px-6 pt-5 pb-3"><h3 className="text-sm font-bold text-gray-500 uppercase tracking-widest">Reusable Components</h3></div>
                                                        <div className="divide-y divide-gray-50">
                                                            {bvrResult.answer.reuse_estimate.components.map((c: any, i: number) => (
                                                                <div key={i} className="px-6 py-4 hover:bg-gray-50/50 transition-colors">
                                                                    <div className="flex items-center justify-between mb-2">
                                                                        <div className="flex items-center gap-3">
                                                                            <div className={`h-2.5 w-2.5 rounded-full ${c.confidence_score >= 70 ? "bg-emerald-500" : c.confidence_score >= 40 ? "bg-amber-400" : "bg-gray-300"}`} />
                                                                            <span className="font-bold text-gray-900 text-sm">{c.name}</span>
                                                                            <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full ${c.match_quality === "Full Match" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>{c.match_quality || "Partial"}</span>
                                                                            <span className="text-[10px] text-gray-400 font-mono">{c.confidence_score || 0}%</span>
                                                                        </div>
                                                                        <div className="text-xs text-gray-400">{c.architecture_layer || ""}</div>
                                                                    </div>
                                                                    <div className="text-xs text-gray-500 mb-2 prose prose-sm prose-compact max-w-none">
                                                                        <Markdown remarkPlugins={[remarkGfm]}>{c.reasoning || ""}</Markdown>
                                                                    </div>
                                                                    <div className="flex gap-4 text-xs">
                                                                        <span className="text-gray-400">Integration: <strong className="text-gray-600">{c.integration_effort_days || 0}d</strong></span>
                                                                        <span className="text-gray-400">Customization: <strong className="text-gray-600">{c.customization_effort_days || 0}d</strong></span>
                                                                        <span className="text-gray-400">Maintenance: <strong className="text-gray-600">${(c.annual_maintenance_usd || 0).toLocaleString()}/yr</strong></span>
                                                                    </div>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    </div>
                                                )}

                                                {/* Gaps */}
                                                {bvrResult.answer.reuse_estimate?.gaps?.length > 0 && (
                                                    <div className="bg-amber-50 rounded-2xl border border-amber-200 p-6">
                                                        <div className="flex items-center gap-2 mb-4">
                                                            <AlertTriangle className="h-5 w-5 text-amber-600" />
                                                            <h3 className="text-sm font-bold text-amber-900 uppercase tracking-wider">Gaps — Custom Build Required</h3>
                                                        </div>
                                                        <div className="space-y-3">
                                                            {bvrResult.answer.reuse_estimate.gaps.map((g: any, i: number) => (
                                                                <div key={i} className="flex items-center justify-between bg-white/60 rounded-lg px-4 py-3 border border-amber-200/50">
                                                                    <div>
                                                                        <div className="text-sm font-bold text-amber-900">{g.name}</div>
                                                                        <div className="text-xs text-amber-700">{g.description || ""}</div>
                                                                    </div>
                                                                    <div className="text-right shrink-0">
                                                                        <div className="text-sm font-black text-gray-800">${(g.build_cost_usd || 0).toLocaleString()}</div>
                                                                        <div className="text-[10px] text-gray-400">{g.dev_weeks || 0} weeks</div>
                                                                    </div>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })()}

                                    {/* BvR Error State */}
                                    {!bvrLoading && bvrResult && (!bvrResult.answer || !bvrResult.answer.comparison) && (
                                        <div className="bg-amber-50 rounded-2xl border border-amber-200 p-4 text-center">
                                            <p className="text-sm text-amber-700">Build vs Reuse analysis could not generate a comparison. Try again.</p>
                                            <button onClick={handleBuildVsReuse} className="mt-2 text-xs font-bold text-amber-800 underline">Retry</button>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Fallback: answer is a plain string (not structured JSON) */}
                            {!loading && discoveryResult && discoveryResult.answer && typeof discoveryResult.answer === "string" && (
                                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 space-y-4">
                                    <div className="flex items-center gap-2 mb-2">
                                        <Bot className="h-5 w-5 text-primary" />
                                        <h3 className="text-lg font-black text-gray-900">Discovery Analysis</h3>
                                    </div>
                                    <div className="prose prose-sm max-w-none">
                                        <Markdown remarkPlugins={[remarkGfm]}>{discoveryResult.answer}</Markdown>
                                    </div>
                                    <div className="flex items-center gap-3 pt-3 border-t border-gray-100 text-xs text-gray-400">
                                        <span>Playbooks: {discoveryResult.playbooks_used?.join(", ") || "—"}</span>
                                        <span>·</span>
                                        <span>{discoveryResult.iterations || 0} iterations</span>
                                    </div>
                                </div>
                            )}

                            {/* Empty / Error State — only when truly no answer */}
                            {!loading && hasSearched && (!discoveryResult || !discoveryResult.answer) && (
                                <div className="flex flex-col items-center justify-center py-20 text-center">
                                    <h3 className="text-lg font-bold text-gray-700">Discovery Completed</h3>
                                    <p className="text-sm text-gray-500 mt-2">Could not synthesize architecture or find matches.</p>
                                </div>
                            )}
                        </>
                    )}


                </div>
            </main>
        </div>
    );
}
