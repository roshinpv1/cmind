import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    BookOpen,
    Building2,
    Star,
    Tag,
    Globe,
    Clock,
    RefreshCw,
    ExternalLink
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
    created_at: number;
    updated_at: number;
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

    const fetchCatalogs = () => {
        setLoading(true);
        fetch("/api/v1/catalogs/list")
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

    useEffect(() => { fetchCatalogs(); }, []);

    if (loading) return <div className="p-8 text-center text-gray-500">Loading catalogs...</div>;
    if (error) return <div className="p-8 text-center text-red-500">Error: {error}</div>;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Generated Catalogs</h1>
                    <p className="text-sm text-gray-500 mt-1">{entries.length} catalog entries</p>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={fetchCatalogs}
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

            {/* Table */}
            <div className="bg-white shadow rounded-lg border border-gray-200 overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Repository</th>
                                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Org</th>
                                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Category</th>
                                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Tech Stack</th>
                                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Quality</th>
                                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Topics</th>
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
                                            <div className="h-8 w-8 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
                                                <BookOpen className="h-4 w-4 text-blue-600" />
                                            </div>
                                            <div className="min-w-0">
                                                <div className="text-sm font-medium text-gray-900 truncate max-w-[200px]">
                                                    {entry.repo_name}
                                                </div>
                                                <div className="text-xs text-gray-400 font-mono truncate max-w-[200px]">
                                                    {entry.repo_id}
                                                </div>
                                            </div>
                                        </div>
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

                                    {/* Topics */}
                                    <td className="px-4 py-3">
                                        <div className="flex flex-wrap gap-1 max-w-[180px]">
                                            {(entry.topics || []).slice(0, 3).map((t, i) => (
                                                <span
                                                    key={i}
                                                    className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-gray-100 text-gray-600"
                                                >
                                                    <Tag className="w-2.5 h-2.5 mr-0.5" />
                                                    {t}
                                                </span>
                                            ))}
                                            {(entry.topics || []).length > 3 && (
                                                <span className="text-xs text-gray-400">+{entry.topics.length - 3}</span>
                                            )}
                                        </div>
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
                                        </div>
                                    </td>
                                </tr>
                            ))}
                            {entries.length === 0 && (
                                <tr>
                                    <td colSpan={8} className="px-4 py-12 text-center text-gray-500">
                                        <BookOpen className="w-8 h-8 mx-auto mb-2 text-gray-300" />
                                        <p>No catalog entries generated yet.</p>
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

            {/* Description preview below table */}
            {entries.length > 0 && (
                <div className="text-xs text-gray-400 text-right">
                    Showing {entries.length} catalog {entries.length === 1 ? "entry" : "entries"}
                </div>
            )}
        </div>
    );
}
