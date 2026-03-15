import { useState, useEffect, useRef } from "react";
import {
    Send, Loader2, Terminal, FileCode, Search, Compass,
    Package, BarChart3, Wrench, Scale, Sparkles, ChevronRight,
    CheckCircle2, AlertTriangle, Star, Layers, Code2,
    Brain
} from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { authService } from "../../lib/auth";

interface Repo {
    id: string;
    name: string;
    branch: string;
    status: string;
}

interface AgentJob {
    job_id: string;
    status: "pending" | "running" | "completed" | "failed";
    result?: {
        answer: any;
        iterations: number;
        steps_taken: number;
        playbooks_used?: string[];
    };
    logs: string[];
}

/* ─── Playbook Definitions ─────────────────────────────────────── */

interface PlaybookDef {
    id: string;
    label: string;
    icon: React.ReactNode;
    color: string;
    bgColor: string;
    description: string;
    requiresRepo: boolean;
    templates: { label: string; prompt: string }[];
}

const ICON_MAP: Record<string, any> = {
    Brain, Code2, Compass, BarChart3, Wrench, Scale, Layers, Package, Search, Sparkles
};

const COLOR_STYLES: Record<string, { color: string; bgColor: string }> = {
    blue: { color: "text-blue-600", bgColor: "bg-blue-50" },
    teal: { color: "text-teal-600", bgColor: "bg-teal-50" },
    amber: { color: "text-amber-600", bgColor: "bg-amber-50" },
    rose: { color: "text-rose-600", bgColor: "bg-rose-50" },
    emerald: { color: "text-emerald-600", bgColor: "bg-emerald-50" },
    indigo: { color: "text-indigo-600", bgColor: "bg-indigo-50" },
    orange: { color: "text-orange-600", bgColor: "bg-orange-50" },
    gray: { color: "text-gray-600", bgColor: "bg-gray-100" },
    violet: { color: "text-violet-600", bgColor: "bg-violet-50" },
};

function apiToPlaybookDef(item: any): PlaybookDef {
    const IconComp = ICON_MAP[item.icon] || Brain;
    const styles = COLOR_STYLES[item.color] || COLOR_STYLES.violet;
    return {
        id: item.name,
        label: item.name.replaceAll("_", " ").replace(/\b\w/g, (c: string) => c.toUpperCase()),
        icon: <IconComp className="w-4 h-4" />,
        color: styles.color,
        bgColor: styles.bgColor,
        description: item.description || "",
        requiresRepo: item.requires_repo ?? true,
        templates: item.templates || [],
    };
}

// Fallback auto-pilot entry
const AUTO_PILOT: PlaybookDef = {
    id: "auto", label: "Auto-Pilot", icon: <Brain className="w-4 h-4" />,
    color: "text-violet-600", bgColor: "bg-violet-50",
    description: "Let the agent autonomously decide the best strategy",
    requiresRepo: true,
    templates: [
        { label: "Analyze this codebase", prompt: "Analyze the overall architecture and key patterns of this codebase" },
        { label: "Find similar components", prompt: "Search for similar components in the catalog that match this codebase's capabilities" },
    ]
};

/* ─── Structured Result Renderers ──────────────────────────────── */

function CatalogMatchCard({ match }: { match: any }) {
    const conf = match.confidence_score ?? match.score ?? 0;
    const confColor = conf >= 80 ? "text-green-600 bg-green-50" : conf >= 50 ? "text-amber-600 bg-amber-50" : "text-red-500 bg-red-50";
    const entry = match.catalog_entry || {};
    return (
        <div className="border border-gray-100 rounded-xl p-4 bg-white shadow-sm hover:shadow-md transition-shadow">
            <div className="flex items-start justify-between mb-2">
                <div>
                    <h4 className="font-bold text-gray-800 text-sm">{match.component_name || entry.repo_name || "Unknown"}</h4>
                    <p className="text-xs text-gray-500">{match.capability || ""}</p>
                </div>
                <div className={`px-2.5 py-1 rounded-full text-xs font-bold ${confColor}`}>
                    {conf}%
                </div>
            </div>
            <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium mb-2 ${match.match_type === "Full Match" ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-700"
                }`}>
                {match.match_type || "Match"}
            </span>
            {match.reasoning && (
                <p className="text-xs text-gray-600 mt-1 leading-relaxed">{match.reasoning}</p>
            )}
            {entry.tech_stack && (
                <div className="mt-2 flex flex-wrap gap-1">
                    {String(entry.tech_stack).split(",").slice(0, 5).map((t: string, i: number) => (
                        <span key={i} className="px-1.5 py-0.5 bg-gray-100 text-gray-600 text-[10px] rounded">{t.trim()}</span>
                    ))}
                </div>
            )}
            {entry.org && <p className="text-[10px] text-gray-400 mt-1">Org: {entry.org}</p>}
        </div>
    );
}

function GapCard({ gap }: { gap: any }) {
    const name = typeof gap === "string" ? gap : gap.name || gap.description || JSON.stringify(gap);
    const desc = typeof gap === "object" ? gap.description || "" : "";
    const layer = typeof gap === "object" ? gap.architecture_layer || "" : "";
    return (
        <div className="border border-red-100 rounded-lg p-3 bg-red-50/50">
            <div className="flex items-start gap-2">
                <AlertTriangle className="w-3.5 h-3.5 text-red-400 mt-0.5 shrink-0" />
                <div>
                    <p className="text-sm font-medium text-red-800">{name}</p>
                    {desc && <p className="text-xs text-red-600 mt-0.5">{desc}</p>}
                    {layer && <span className="inline-block mt-1 px-1.5 py-0.5 bg-red-100 text-red-600 text-[10px] rounded">{layer}</span>}
                </div>
            </div>
        </div>
    );
}

function StructuredResult({ data }: { data: any }) {
    if (!data || typeof data === "string") {
        return (
            <div className="prose prose-sm max-w-none prose-headings:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:rounded prose-code:text-violet-700 prose-pre:bg-gray-900 prose-pre:text-gray-100">
                <Markdown remarkPlugins={[remarkGfm]}>{data || ""}</Markdown>
            </div>
        );
    }

    // Has report_markdown — render it directly
    if (data.report_markdown) {
        return (
            <div className="prose prose-sm max-w-none prose-headings:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:rounded prose-code:text-violet-700 prose-pre:bg-gray-900 prose-pre:text-gray-100">
                <Markdown remarkPlugins={[remarkGfm]}>{data.report_markdown}</Markdown>
            </div>
        );
    }

    const hasCatalogMatches = Array.isArray(data.catalog_matches) && data.catalog_matches.length > 0;
    const hasGaps = Array.isArray(data.gaps) && data.gaps.length > 0;
    const hasBuildEstimate = data.build_estimate || data.reuse_estimate;

    return (
        <div className="space-y-6">
            {/* Requirement Summary */}
            {data.requirement_summary && (
                <div className="bg-gradient-to-r from-violet-50 to-indigo-50 rounded-xl p-4 border border-violet-100">
                    <h3 className="text-xs font-bold text-violet-600 uppercase tracking-wider mb-1">Requirement</h3>
                    <p className="text-sm text-gray-800">{data.requirement_summary}</p>
                </div>
            )}

            {/* Overall Confidence */}
            {data.overall_confidence_score !== undefined && (
                <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5">
                        <Star className="w-4 h-4 text-amber-500" />
                        <span className="text-sm font-bold text-gray-700">Overall Confidence:</span>
                    </div>
                    <div className="flex-1 bg-gray-100 rounded-full h-2.5">
                        <div
                            className={`h-2.5 rounded-full transition-all ${data.overall_confidence_score >= 70 ? "bg-green-500" :
                                data.overall_confidence_score >= 40 ? "bg-amber-500" : "bg-red-400"
                                }`}
                            style={{ width: `${Math.min(100, data.overall_confidence_score)}%` }}
                        />
                    </div>
                    <span className="text-sm font-bold text-gray-800">{data.overall_confidence_score}%</span>
                </div>
            )}

            {/* Catalog Matches */}
            {hasCatalogMatches && (
                <div>
                    <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
                        Catalog Matches ({data.catalog_matches.length})
                    </h3>
                    <div className="grid grid-cols-1 gap-3">
                        {data.catalog_matches.map((m: any, i: number) => (
                            <CatalogMatchCard key={i} match={m} />
                        ))}
                    </div>
                </div>
            )}

            {/* Architecture Composition */}
            {data.architecture_composition && (
                <div className="bg-indigo-50/50 rounded-xl p-4 border border-indigo-100">
                    <h3 className="text-xs font-bold text-indigo-600 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                        <Layers className="w-3.5 h-3.5" />
                        Architecture Composition
                    </h3>
                    <div className="prose prose-sm max-w-none text-gray-700">
                        <Markdown remarkPlugins={[remarkGfm]}>{data.architecture_composition}</Markdown>
                    </div>
                </div>
            )}

            {/* Build vs Reuse */}
            {hasBuildEstimate && (
                <div className="grid grid-cols-2 gap-3">
                    {data.build_estimate && (
                        <div className="bg-orange-50 p-4 rounded-xl border border-orange-100">
                            <h4 className="text-xs font-bold text-orange-600 uppercase mb-2">🔨 Build From Scratch</h4>
                            <div className="prose prose-sm max-w-none text-gray-700">
                                <Markdown remarkPlugins={[remarkGfm]}>
                                    {typeof data.build_estimate === "string" ? data.build_estimate : JSON.stringify(data.build_estimate, null, 2)}
                                </Markdown>
                            </div>
                        </div>
                    )}
                    {data.reuse_estimate && (
                        <div className="bg-green-50 p-4 rounded-xl border border-green-100">
                            <h4 className="text-xs font-bold text-green-600 uppercase mb-2">♻️ Reuse Existing</h4>
                            <div className="prose prose-sm max-w-none text-gray-700">
                                <Markdown remarkPlugins={[remarkGfm]}>
                                    {typeof data.reuse_estimate === "string" ? data.reuse_estimate : JSON.stringify(data.reuse_estimate, null, 2)}
                                </Markdown>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Gaps */}
            {hasGaps && (
                <div>
                    <h3 className="text-xs font-bold text-red-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                        <AlertTriangle className="w-3.5 h-3.5" />
                        Gaps — Custom Development Needed ({data.gaps.length})
                    </h3>
                    <div className="space-y-2">
                        {data.gaps.map((g: any, i: number) => <GapCard key={i} gap={g} />)}
                    </div>
                </div>
            )}

            {/* Risks */}
            {Array.isArray(data.risks) && data.risks.length > 0 && (
                <div className="bg-amber-50/50 rounded-xl p-4 border border-amber-100">
                    <h3 className="text-xs font-bold text-amber-600 uppercase tracking-wider mb-2">⚠️ Risks</h3>
                    <ul className="space-y-1">
                        {data.risks.map((r: any, i: number) => (
                            <li key={i} className="text-xs text-gray-700 flex items-start gap-1.5">
                                <span className="text-amber-400 mt-0.5">•</span>
                                {typeof r === "string" ? r : JSON.stringify(r)}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {/* Capabilities / Decomposition (compact) */}
            {data.capabilities && (
                <details className="group">
                    <summary className="text-xs font-bold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700">
                        Capabilities & Decomposition
                    </summary>
                    <div className="mt-2 prose prose-sm max-w-none text-gray-600">
                        <Markdown remarkPlugins={[remarkGfm]}>
                            {JSON.stringify(data.capabilities, null, 2) + "\n\n" + (data.decomposition ? JSON.stringify(data.decomposition, null, 2) : "")}
                        </Markdown>
                    </div>
                </details>
            )}

            {/* Fallback for other structured data */}
            {!hasCatalogMatches && !hasGaps && !hasBuildEstimate && !data.architecture_composition && !data.requirement_summary && !data.report_markdown && (
                <div className="prose prose-sm max-w-none">
                    <Markdown remarkPlugins={[remarkGfm]}>
                        {"```json\n" + JSON.stringify(data, null, 2) + "\n```"}
                    </Markdown>
                </div>
            )}
        </div>
    );
}

/* ─── Main Component ───────────────────────────────────────────── */

export default function ChatInterface() {
    const [repos, setRepos] = useState<Repo[]>([]);
    const [selectedRepo, setSelectedRepo] = useState("");
    const [goal, setGoal] = useState("");
    const [selectedPlaybooks, setSelectedPlaybooks] = useState<string[]>(["auto"]);
    const [currentJobId, setCurrentJobId] = useState<string | null>(null);
    const [jobStatus, setJobStatus] = useState<AgentJob | null>(null);
    const [isPolling, setIsPolling] = useState(false);
    const [playbooks, setPlaybooks] = useState<PlaybookDef[]>([AUTO_PILOT]);
    const [isPlaybookDropdownOpen, setIsPlaybookDropdownOpen] = useState(false);
    const logsEndRef = useRef<HTMLDivElement>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);

    const activePlaybook = playbooks.find(p => selectedPlaybooks.includes(p.id)) || playbooks[0];

    // Close dropdown on outside click
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsPlaybookDropdownOpen(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    // Load playbooks from API
    useEffect(() => {
        fetch("/api/v1/playbooks", { headers: { ...authService.getAuthHeader() } })
            .then(r => r.json())
            .then((data: any[]) => {
                const mapped = [AUTO_PILOT, ...data.map(apiToPlaybookDef)];
                setPlaybooks(mapped);
            })
            .catch(err => console.error("Failed to load playbooks", err));
    }, []);

    // Load repositories
    useEffect(() => {
        fetch("/api/v1/repos", { headers: { ...authService.getAuthHeader() } })
            .then(res => res.json())
            .then((data: any[]) => {
                const mapped: Repo[] = data
                    .filter(r => r.status === "indexed" || r.status === "catalog-only")
                    .map(r => ({
                        id: r.repo_id || r.id,
                        name: r.name || r.path?.split('/').pop() || r.repo_id || "Unknown",
                        branch: r.branch || "main",
                        status: r.status || "unknown",
                    }));
                setRepos(mapped);
                // Default to the first indexed repo
                const indexed = mapped.find(r => r.status === "indexed");
                if (indexed) setSelectedRepo(indexed.id);
                else if (mapped.length > 0) setSelectedRepo(mapped[0].id);
            })
            .catch(err => console.error("Failed to load repos", err));
    }, []);

    // Poll status
    useEffect(() => {
        if (!currentJobId || !isPolling) return;
        const interval = setInterval(async () => {
            try {
                const res = await fetch(`/api/v1/agents/autonomous/${currentJobId}/status`, { headers: { ...authService.getAuthHeader() } });
                if (res.ok) {
                    const data = await res.json();
                    setJobStatus(data);
                    if (data.status === "completed" || data.status === "failed") {
                        setIsPolling(false);
                        if (data.status === "completed") {
                            const resultRes = await fetch(`/api/v1/agents/autonomous/${currentJobId}/result`, { headers: { ...authService.getAuthHeader() } });
                            const resultData = await resultRes.json();
                            setJobStatus(prev => prev ? { ...prev, result: resultData } : resultData);
                        }
                    }
                }
            } catch (err) { console.error("Polling error", err); }
        }, 1000);
        return () => clearInterval(interval);
    }, [currentJobId, isPolling]);

    // Scroll logs
    useEffect(() => {
        logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [jobStatus?.logs]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!goal.trim()) return;
        if (activePlaybook.requiresRepo && !selectedRepo) return;

        setJobStatus(null);
        setIsPolling(true);

        try {
            const allowedPlaybooks = selectedPlaybooks.includes("auto") ? null : selectedPlaybooks;

            const res = await fetch("/api/v1/agents/autonomous", {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json",
                    ...authService.getAuthHeader()
                },
                body: JSON.stringify({
                    goal: goal,
                    repo_id: activePlaybook.requiresRepo ? selectedRepo : undefined,
                    max_iterations: 10,
                    allowed_playbooks: allowedPlaybooks
                })
            });

            const data = await res.json();
            setCurrentJobId(data.job_id);
            setJobStatus({ job_id: data.job_id, status: "pending", logs: ["Initializing..."] });
        } catch (err) {
            console.error("Failed to start job", err);
            setIsPolling(false);
        }
    };

    const handleTemplateClick = (template: { label: string; prompt: string }) => {
        setGoal(template.prompt);
    };

    const answerData = jobStatus?.result?.answer;

    return (
        <div className="max-w-7xl mx-auto h-[calc(100vh-5rem)] flex flex-col gap-4 p-4">

            {/* ─── Top Bar: Strategy + Repo ─── */}
            <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-visible z-40 relative">
                {/* Playbook Selection Dropdown */}
                <div className="flex items-center gap-3 px-4 py-3 overflow-visible border-b border-gray-50 bg-gray-50/50" ref={dropdownRef}>
                    <div className="relative">
                        <button
                            onClick={() => setIsPlaybookDropdownOpen(!isPlaybookDropdownOpen)}
                            className="flex items-center gap-2 px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs font-semibold text-gray-700 shadow-sm hover:bg-gray-50 hover:border-violet-300 transition-colors"
                        >
                            <Brain className="w-4 h-4 text-violet-600" />
                            {selectedPlaybooks.includes("auto")
                                ? "Playbooks: Auto-Pilot"
                                : `Playbooks: ${selectedPlaybooks.length} selected`}
                            <ChevronRight className={`w-3.5 h-3.5 text-gray-400 transition-transform ${isPlaybookDropdownOpen ? "rotate-90" : ""}`} />
                        </button>

                        {isPlaybookDropdownOpen && (
                            <div className="absolute top-full left-0 mt-1 w-64 bg-white rounded-xl shadow-lg border border-gray-100 py-1.5 z-50">
                                <div className="px-3 py-1.5 text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">
                                    Available Playbooks
                                </div>
                                <div className="max-h-[300px] overflow-y-auto">
                                    {playbooks.map((pb: PlaybookDef) => {
                                        const isSelected = selectedPlaybooks.includes(pb.id);
                                        return (
                                            <button
                                                key={pb.id}
                                                onClick={() => {
                                                    if (pb.id === "auto") {
                                                        setSelectedPlaybooks(["auto"]);
                                                    } else {
                                                        setSelectedPlaybooks(prev => {
                                                            const withoutAuto = prev.filter(p => p !== "auto");
                                                            if (withoutAuto.includes(pb.id)) {
                                                                const next = withoutAuto.filter(p => p !== pb.id);
                                                                return next.length === 0 ? ["auto"] : next;
                                                            }
                                                            return [...withoutAuto, pb.id];
                                                        });
                                                    }
                                                }}
                                                className={`w-full flex items-center justify-between px-3 py-2 text-left hover:bg-gray-50 transition-colors ${isSelected ? "bg-violet-50/50" : ""}`}
                                            >
                                                <div className="flex items-center gap-2">
                                                    <div className={`p-1 rounded ${isSelected ? pb.bgColor : "bg-gray-100"}`}>
                                                        <div className={isSelected ? pb.color : "text-gray-500"}>
                                                            {pb.icon}
                                                        </div>
                                                    </div>
                                                    <span className={`text-xs ${isSelected ? "font-semibold text-violet-700" : "font-medium text-gray-700"}`}>
                                                        {pb.label}
                                                    </span>
                                                </div>
                                                <div className={`w-4 h-4 rounded-full border flex items-center justify-center transition-colors ${isSelected ? "bg-violet-600 border-violet-600" : "border-gray-300"}`}>
                                                    {isSelected && <CheckCircle2 className="w-3 h-3 text-white" />}
                                                </div>
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                    </div>
                    {/* Visual pills for selected items */}
                    {!selectedPlaybooks.includes("auto") && (
                        <div className="flex items-center gap-1.5 flex-wrap flex-1">
                            {playbooks.filter(p => selectedPlaybooks.includes(p.id)).map(pb => (
                                <span key={pb.id} className={`flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-semibold ${pb.bgColor} ${pb.color}`}>
                                    {pb.icon}
                                    {pb.label}
                                </span>
                            ))}
                        </div>
                    )}
                </div>

                {/* Strategy Description + Repo Selector */}
                <div className="px-5 py-3 flex items-center justify-between gap-4">
                    <div className="flex-1">
                        <p className="text-xs text-gray-500">{activePlaybook.description}</p>
                    </div>
                    {activePlaybook.requiresRepo && (
                        <div className="flex items-center gap-2">
                            <label className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Repository</label>
                            <select
                                value={selectedRepo}
                                onChange={e => setSelectedRepo(e.target.value)}
                                className="text-xs border-gray-200 rounded-lg shadow-sm focus:border-violet-300 focus:ring-violet-300 py-1.5 pr-8"
                            >
                                {repos.map(r => (
                                    <option key={r.id} value={r.id}>
                                        {r.name}{r.branch ? ` (${r.branch})` : ""}
                                        {r.status === "catalog-only" ? " [catalog]" : ""}
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}
                    {!activePlaybook.requiresRepo && (
                        <span className="text-[10px] font-medium text-emerald-600 bg-emerald-50 px-2 py-1 rounded-full">
                            No repository needed
                        </span>
                    )}
                </div>
            </div>

            {/* ─── Main Split View ─── */}
            <div className="flex-1 flex gap-4 min-h-0">

                {/* ─── Left: Input + Activity ─── */}
                <div className="w-[420px] shrink-0 flex flex-col gap-3">

                    {/* Quick Templates */}
                    {!isPolling && !jobStatus?.result && (
                        <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-3">
                            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">Quick Start Templates</p>
                            <div className="space-y-1.5">
                                {activePlaybook.templates.map((t: { label: string; prompt: string }, i: number) => (
                                    <button
                                        key={i}
                                        onClick={() => handleTemplateClick(t)}
                                        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left text-xs text-gray-600 hover:bg-violet-50 hover:text-violet-700 transition-colors group"
                                    >
                                        <ChevronRight className="w-3 h-3 text-gray-300 group-hover:text-violet-500 transition-colors" />
                                        {t.label}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Input Area */}
                    <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-gray-100 shadow-sm p-3">
                        <textarea
                            className="w-full border border-gray-200 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-violet-200 focus:border-violet-300 resize-none"
                            placeholder={activePlaybook.templates[0]?.prompt || "Describe your goal..."}
                            rows={3}
                            value={goal}
                            onChange={e => setGoal(e.target.value)}
                            disabled={isPolling}
                            onKeyDown={e => {
                                if (e.key === "Enter" && !e.shiftKey) {
                                    e.preventDefault();
                                    handleSubmit(e);
                                }
                            }}
                        />
                        <div className="flex items-center justify-between mt-2">
                            <span className="text-[10px] text-gray-400">Shift+Enter for new line</span>
                            <button
                                type="submit"
                                disabled={isPolling || !goal.trim() || (activePlaybook.requiresRepo && !selectedRepo)}
                                className="bg-gradient-to-r from-violet-600 to-indigo-600 text-white px-5 py-2 rounded-lg text-xs font-bold hover:from-violet-700 hover:to-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2 transition-all shadow-sm"
                            >
                                {isPolling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                                {isPolling ? "Running..." : "Execute"}
                            </button>
                        </div>
                    </form>

                    {/* Activity Feed */}
                    {jobStatus && (
                        <div className="flex-1 bg-white rounded-xl border border-gray-100 shadow-sm overflow-y-auto min-h-0">
                            <div className="px-3 py-2 border-b border-gray-50 flex items-center gap-2">
                                <Terminal className="w-3.5 h-3.5 text-gray-400" />
                                <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Activity Log</span>
                                {jobStatus.status === "running" && (
                                    <Loader2 className="w-3 h-3 text-violet-500 animate-spin ml-auto" />
                                )}
                                {jobStatus.status === "completed" && (
                                    <CheckCircle2 className="w-3.5 h-3.5 text-green-500 ml-auto" />
                                )}
                                {jobStatus.status === "failed" && (
                                    <AlertTriangle className="w-3.5 h-3.5 text-red-500 ml-auto" />
                                )}
                            </div>
                            <div className="p-3 space-y-1.5">
                                {jobStatus.logs?.map((log, i) => {
                                    const isAction = log.startsWith("Action");
                                    const isThinking = log.startsWith("Thinking");
                                    return (
                                        <div key={i} className={`text-[11px] px-2.5 py-1.5 rounded-md leading-relaxed ${isAction ? "bg-blue-50 text-blue-700 font-medium"
                                            : isThinking ? "bg-gray-50 text-gray-500 italic"
                                                : "text-gray-600"
                                            }`}>
                                            {log}
                                        </div>
                                    );
                                })}
                                {jobStatus.result && (
                                    <div className="text-[10px] text-gray-400 pt-2 border-t border-gray-100 flex items-center gap-4">
                                        <span>Steps: {jobStatus.result.steps_taken ?? 0}</span>
                                        <span>Iterations: {jobStatus.result.iterations ?? 0}</span>
                                        {jobStatus.result.playbooks_used && (
                                            <span>Playbooks: {jobStatus.result.playbooks_used.join(", ")}</span>
                                        )}
                                    </div>
                                )}
                                <div ref={logsEndRef} />
                            </div>
                        </div>
                    )}
                </div>

                {/* ─── Right: Result Panel ─── */}
                <div className="flex-1 bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden flex flex-col min-h-0">
                    <div className="px-4 py-2.5 border-b border-gray-100 flex items-center gap-2 bg-gray-50/50">
                        <FileCode className="h-4 w-4 text-violet-500" />
                        <span className="text-xs font-bold text-gray-600">Result</span>
                        {answerData && (
                            <span className="ml-auto text-[10px] text-gray-400">
                                {typeof answerData === "string" ? `${answerData.length} chars` : `${Object.keys(answerData).length} fields`}
                            </span>
                        )}
                    </div>
                    <div className="flex-1 overflow-y-auto p-5">
                        {answerData ? (
                            <StructuredResult data={answerData} />
                        ) : (
                            <div className="h-full flex flex-col items-center justify-center text-gray-300">
                                <Sparkles className="h-12 w-12 opacity-20 mb-3" />
                                <p className="text-sm font-medium">Results will appear here</p>
                                <p className="text-xs mt-1">Select a template or type your goal to begin</p>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
