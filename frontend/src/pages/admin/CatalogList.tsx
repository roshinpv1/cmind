import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    BookOpen,
    Building2,
    Star,
    Globe,
    Clock,
    RefreshCw,
    ExternalLink,
    Sparkles,
    GitBranch,
    CheckCircle2,
    Users,
    Trash2,
    Loader2,
} from "lucide-react";

interface CatalogEntry {
    repo_id: string;
    repo_name: string;
    org: string;
    description: string;
    tech_stack: string;
    category: string;
    quality_score: number;
    topics: string[];
    repo_url: string;
    branch: string;
    status?: string;
    created_by?: string;
    source_gap?: string;
    created_at: number;
    updated_at: number;
    contributors?: { uid: string; org: string; contributed_at?: number }[];
}

const STATUS_TABS = [
    { key: "", label: "All", icon: <BookOpen className="w-3.5 h-3.5" /> },
    { key: "active", label: "Active", icon: <CheckCircle2 className="w-3.5 h-3.5" />, color: "text-emerald-600" },
    { key: "proposed", label: "Proposed", icon: <Sparkles className="w-3.5 h-3.5" />, color: "text-amber-600" },
    { key: "qualified", label: "Qualified", icon: <GitBranch className="w-3.5 h-3.5" />, color: "text-blue-600" },
];

function StatusBadge({ status }: { status: string }) {
    const config: Record<string, { bg: string; text: string; dot: string; label: string }> = {
        active: { bg: "bg-emerald-50", text: "text-emerald-700", dot: "bg-emerald-500", label: "Active" },
        proposed: { bg: "bg-amber-50", text: "text-amber-700", dot: "bg-amber-400 animate-pulse", label: "Proposed" },
        qualified: { bg: "bg-blue-50", text: "text-blue-700", dot: "bg-blue-500", label: "Qualified" },
    };
    const c = config[status] || config.active;
    return (
        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-bold ${c.bg} ${c.text}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${c.dot}`} />
            {c.label}
        </span>
    );
}

function formatTimestamp(ts: number): string {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function QualityBadge({ score }: { score: number }) {
    const color =
        score >= 8 ? "bg-green-100 text-green-800" :
            score >= 5 ? "bg-yellow-100 text-yellow-800" :
                score > 0 ? "bg-red-100 text-red-800" :
                    "bg-gray-100 text-gray-500";
    return (
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${color}`}>
            <Star className="w-3 h-3" />
            {score > 0 ? `${score}/10` : "N/A"}
        </span>
    );
}

export default function CatalogList() {
    const [entries, setEntries] = useState<CatalogEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [statusFilter, setStatusFilter] = useState("");

    const fetchCatalogs = (status?: string) => {
        setLoading(true);
        const url = status ? `/api/v1/catalogs/list?status=${status}` : `/api/v1/catalogs/list`;
        fetch(url)
            .then((res) => {
                if (!res.ok) throw new Error("Failed to fetch catalogs");
                return res.json();
            })
            .then((data) => {
                setEntries(data);
                setLoading(false);
            })
            .catch((err) => {
                setError(err.message);
                setLoading(false);
            });
    };

    useEffect(() => { fetchCatalogs(statusFilter || undefined); }, [statusFilter]);

    const deleteCatalog = async (repoId: string, name: string) => {
        if (!window.confirm(`Are you sure you want to delete "${name}"? This cannot be undone.`)) return;
        try {
            const res = await fetch(`/api/v1/catalogs/${encodeURIComponent(repoId)}`, { method: "DELETE" });
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: "Delete failed" }));
                alert(err.detail || "Delete failed");
                return;
            }
            fetchCatalogs(statusFilter || undefined);
        } catch {
            alert("Failed to delete catalog entry");
        }
    };

    const [regeneratingId, setRegeneratingId] = useState<string | null>(null);

    const regenerateCatalog = async (repoId: string) => {
        setRegeneratingId(repoId);
        try {
            const res = await fetch(`/api/v1/catalogs/${encodeURIComponent(repoId)}/regenerate`, {
                method: "POST",
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: "Regeneration failed" }));
                alert(err.detail || "Regeneration failed");
                return;
            }
            fetchCatalogs(statusFilter || undefined);
        } catch {
            alert("Failed to regenerate requirements");
        } finally {
            setRegeneratingId(null);
        }
    };

    const counts = {
        "": entries.length,
        active: entries.filter(e => (e.status || "active") === "active").length,
        proposed: entries.filter(e => e.status === "proposed").length,
        qualified: entries.filter(e => e.status === "qualified").length,
    };

    if (error) return <div className="p-8 text-center text-red-500">Error: {error}</div>;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Component Catalog</h1>
                    <p className="text-sm text-gray-500 mt-1">{entries.length} catalog entries</p>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={() => fetchCatalogs(statusFilter || undefined)}
                        className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
                    >
                        <RefreshCw className="w-4 h-4 mr-1.5" />
                        Refresh
                    </button>
                    <Link
                        to="/admin/catalog/create"
                        className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-primary hover:bg-red-700"
                    >
                        <BookOpen className="w-4 h-4 mr-2" />
                        Generate New
                    </Link>
                </div>
            </div>

            {/* Status Filter Tabs */}
            <div className="flex items-center gap-1 bg-gray-50 p-1 rounded-xl border border-gray-100">
                {STATUS_TABS.map((tab) => {
                    const isActive = statusFilter === tab.key;
                    const count = !statusFilter ? undefined : (counts as any)[tab.key];
                    return (
                        <button
                            key={tab.key}
                            onClick={() => setStatusFilter(tab.key)}
                            className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold transition-all ${isActive
                                ? "bg-white text-gray-900 shadow-sm ring-1 ring-gray-200"
                                : "text-gray-500 hover:text-gray-700 hover:bg-white/50"
                                }`}
                        >
                            <span className={isActive ? (tab.color || "text-gray-700") : ""}>{tab.icon}</span>
                            {tab.label}
                        </button>
                    );
                })}
            </div>

            {loading ? (
                <div className="p-8 text-center text-gray-500">Loading catalogs...</div>
            ) : (
                <>
                    {/* Table */}
                    <div className="bg-white shadow rounded-lg border border-gray-200 overflow-hidden">
                        <div className="overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-200">
                                <thead className="bg-gray-50">
                                    <tr>
                                        <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Repository</th>
                                        <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                                        <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Org</th>
                                        <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Category</th>
                                        <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Tech Stack</th>
                                        <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Quality</th>
                                        <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Updated</th>
                                        <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Actions</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-200">
                                    {entries.map((entry) => (
                                        <tr key={entry.repo_id} className="hover:bg-gray-50 transition-colors">
                                            {/* Repository */}
                                            <td className="px-4 py-3">
                                                <div className="flex items-center gap-2">
                                                    <div className={`h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 ${entry.status === "proposed" ? "bg-amber-100" :
                                                        entry.status === "qualified" ? "bg-blue-100" : "bg-blue-100"
                                                        }`}>
                                                        {entry.status === "proposed" ? (
                                                            <Sparkles className="h-4 w-4 text-amber-600" />
                                                        ) : (
                                                            <BookOpen className="h-4 w-4 text-blue-600" />
                                                        )}
                                                    </div>
                                                    <div className="min-w-0">
                                                        <div className="text-sm font-medium text-gray-900 truncate max-w-[200px]">
                                                            {entry.repo_name}
                                                        </div>
                                                        {entry.source_gap && (
                                                            <div className="text-[10px] text-amber-600 font-medium">
                                                                from gap: {entry.source_gap}
                                                            </div>
                                                        )}
                                                        {!entry.source_gap && (
                                                            <div className="text-xs text-gray-400 font-mono truncate max-w-[200px]">
                                                                {entry.repo_id}
                                                            </div>
                                                        )}
                                                        {entry.contributors && entry.contributors.length > 0 && (
                                                            <div className="flex flex-wrap gap-1 mt-1">
                                                                <Users className="w-3 h-3 text-teal-500 mt-0.5" />
                                                                {entry.contributors.map((c: any, ci: number) => (
                                                                    <span
                                                                        key={ci}
                                                                        className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-teal-50 text-teal-700 border border-teal-100"
                                                                        title={`Contributed at ${c.contributed_at ? new Date(c.contributed_at * 1000).toLocaleDateString() : 'unknown'}`}
                                                                    >
                                                                        {c.uid}{c.org ? ` · ${c.org}` : ""}
                                                                    </span>
                                                                ))}
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                            </td>

                                            {/* Status */}
                                            <td className="px-4 py-3">
                                                <StatusBadge status={entry.status || "active"} />
                                            </td>

                                            {/* Org */}
                                            <td className="px-4 py-3">
                                                {entry.org ? (
                                                    <span className="inline-flex items-center gap-1 text-sm text-gray-700">
                                                        <Building2 className="w-3.5 h-3.5 text-gray-400" />
                                                        {entry.org}
                                                    </span>
                                                ) : (
                                                    <span className="text-sm text-gray-400">—</span>
                                                )}
                                            </td>

                                            {/* Category */}
                                            <td className="px-4 py-3">
                                                {entry.category ? (
                                                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">
                                                        {entry.category}
                                                    </span>
                                                ) : (
                                                    <span className="text-sm text-gray-400">—</span>
                                                )}
                                            </td>

                                            {/* Tech Stack */}
                                            <td className="px-4 py-3">
                                                <div className="text-sm text-gray-700 truncate max-w-[180px]" title={entry.tech_stack}>
                                                    {entry.tech_stack || "—"}
                                                </div>
                                            </td>

                                            {/* Quality */}
                                            <td className="px-4 py-3">
                                                <QualityBadge score={entry.quality_score} />
                                            </td>

                                            {/* Updated */}
                                            <td className="px-4 py-3">
                                                <div className="flex items-center gap-1 text-xs text-gray-500">
                                                    <Clock className="w-3.5 h-3.5" />
                                                    {formatTimestamp(entry.updated_at)}
                                                </div>
                                            </td>

                                            {/* Actions */}
                                            <td className="px-4 py-3 text-right">
                                                <div className="flex items-center justify-end gap-2">
                                                    {entry.repo_url && (
                                                        <a
                                                            href={entry.repo_url}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded transition-colors"
                                                            title="Open repo"
                                                        >
                                                            <ExternalLink className="w-4 h-4" />
                                                        </a>
                                                    )}
                                                    <Link
                                                        to={`/admin/repos/${encodeURIComponent(entry.repo_id)}/edit`}
                                                        className="p-1.5 text-gray-400 hover:text-primary hover:bg-red-50 rounded transition-colors"
                                                        title="Edit metadata"
                                                    >
                                                        <Globe className="w-4 h-4" />
                                                    </Link>
                                                    {(entry.status === "proposed" || entry.status === "qualified") && (
                                                        <>
                                                            <button
                                                                onClick={() => regenerateCatalog(entry.repo_id)}
                                                                disabled={regeneratingId === entry.repo_id}
                                                                className="p-1.5 text-gray-400 hover:text-violet-600 hover:bg-violet-50 rounded transition-colors disabled:opacity-50"
                                                                title="Regenerate requirements"
                                                            >
                                                                {regeneratingId === entry.repo_id
                                                                    ? <Loader2 className="w-4 h-4 animate-spin text-violet-500" />
                                                                    : <Sparkles className="w-4 h-4" />
                                                                }
                                                            </button>
                                                            <button
                                                                onClick={() => deleteCatalog(entry.repo_id, entry.repo_name || entry.repo_id)}
                                                                className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                                                                title="Delete entry"
                                                            >
                                                                <Trash2 className="w-4 h-4" />
                                                            </button>
                                                        </>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>
                                    ))}
                                    {entries.length === 0 && (
                                        <tr>
                                            <td colSpan={8} className="px-4 py-12 text-center text-gray-500">
                                                <BookOpen className="w-8 h-8 mx-auto mb-2 text-gray-300" />
                                                <p>No catalog entries {statusFilter ? `with status "${statusFilter}"` : "generated yet"}.</p>
                                                <p className="text-sm mt-1">
                                                    Go to <Link to="/admin/catalog/create" className="text-primary hover:underline">Generate New</Link> to create one.
                                                </p>
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {/* Footer */}
                    {entries.length > 0 && (
                        <div className="text-xs text-gray-400 text-right">
                            Showing {entries.length} catalog {entries.length === 1 ? "entry" : "entries"}
                            {statusFilter && ` (filtered: ${statusFilter})`}
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
