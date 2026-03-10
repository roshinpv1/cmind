import { useState, useEffect } from "react";
import {
    Search, ArrowRight, Brain, Code2, Compass,
    BarChart3, Wrench, Scale, Layers, Package, Sparkles,
    Plus, Copy, ChevronDown,
    BookOpen, Zap
} from "lucide-react";

interface PlaybookItem {
    id: string;
    name: string;
    version: string;
    description: string;
    category: string;
    complexity: string;
    author: string;
    is_builtin: boolean;
    is_published: boolean;
    icon: string;
    color: string;
    tags: string[];
    downloads: number;
    rating: number;
    templates: { label: string; prompt: string }[];
    anti_patterns: string[];
    quality_rubric: any[];
    evaluation_rules: string[];
}

const ICON_MAP: Record<string, any> = {
    Brain, Code2, Compass, BarChart3, Wrench, Scale, Layers, Package, Search, Sparkles, BookOpen, Zap
};

const COLOR_MAP: Record<string, { text: string; bg: string; border: string; gradient: string }> = {
    blue: { text: "text-blue-600", bg: "bg-blue-50", border: "border-blue-200", gradient: "from-blue-500 to-blue-600" },
    teal: { text: "text-teal-600", bg: "bg-teal-50", border: "border-teal-200", gradient: "from-teal-500 to-teal-600" },
    amber: { text: "text-amber-600", bg: "bg-amber-50", border: "border-amber-200", gradient: "from-amber-500 to-amber-600" },
    rose: { text: "text-rose-600", bg: "bg-rose-50", border: "border-rose-200", gradient: "from-rose-500 to-rose-600" },
    emerald: { text: "text-emerald-600", bg: "bg-emerald-50", border: "border-emerald-200", gradient: "from-emerald-500 to-emerald-600" },
    indigo: { text: "text-indigo-600", bg: "bg-indigo-50", border: "border-indigo-200", gradient: "from-indigo-500 to-indigo-600" },
    orange: { text: "text-orange-600", bg: "bg-orange-50", border: "border-orange-200", gradient: "from-orange-500 to-orange-600" },
    gray: { text: "text-gray-600", bg: "bg-gray-50", border: "border-gray-200", gradient: "from-gray-500 to-gray-600" },
    violet: { text: "text-violet-600", bg: "bg-violet-50", border: "border-violet-200", gradient: "from-violet-500 to-violet-600" },
};

const CATEGORIES = ["All", "analysis", "generation", "evaluation", "exploration"];

export default function PlaybookStore() {
    const [playbooks, setPlaybooks] = useState<PlaybookItem[]>([]);
    const [search, setSearch] = useState("");
    const [category, setCategory] = useState("All");
    const [expandedId, setExpandedId] = useState<string | null>(null);

    useEffect(() => {
        fetch("/api/v1/playbooks")
            .then(r => r.json())
            .then(data => setPlaybooks(data))
            .catch(err => console.error("Failed to load playbooks", err));
    }, []);

    const filtered = playbooks.filter(pb => {
        const matchSearch = !search || pb.name.toLowerCase().includes(search.toLowerCase()) ||
            pb.description.toLowerCase().includes(search.toLowerCase());
        const matchCat = category === "All" || pb.category === category;
        return matchSearch && matchCat;
    });

    const handleClone = async (id: string) => {
        try {
            const res = await fetch(`/api/v1/playbooks/${id}/clone`, { method: "POST" });
            if (res.ok) {
                const cloned = await res.json();
                setPlaybooks(prev => [...prev, cloned]);
                window.location.href = `/admin/playbook-composer/${cloned.id}`;
            }
        } catch (err) {
            console.error("Clone failed", err);
        }
    };

    const getIcon = (iconName: string) => {
        const IconComp = ICON_MAP[iconName] || Brain;
        return <IconComp className="w-5 h-5" />;
    };

    const getColors = (color: string) => COLOR_MAP[color] || COLOR_MAP.violet;

    return (
        <div className="max-w-7xl mx-auto">
            {/* Header */}
            <div className="mb-8">
                <div className="flex items-center justify-between mb-2">
                    <div>
                        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-3">
                            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center">
                                <BookOpen className="w-5 h-5 text-white" />
                            </div>
                            PlaybookStore
                        </h1>
                        <p className="text-sm text-gray-500 mt-1">Discover, install, and manage AI analysis playbooks</p>
                    </div>
                    <a
                        href="/admin/playbook-composer"
                        className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-violet-600 to-indigo-600 text-white rounded-xl text-sm font-semibold hover:from-violet-700 hover:to-indigo-700 transition-all shadow-lg shadow-violet-200"
                    >
                        <Plus className="w-4 h-4" />
                        Create Playbook
                    </a>
                </div>
            </div>

            {/* Search & Filters */}
            <div className="flex items-center gap-4 mb-6">
                <div className="flex-1 relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                    <input
                        type="text"
                        placeholder="Search playbooks..."
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        className="w-full pl-10 pr-4 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-200 focus:border-violet-300 bg-white"
                    />
                </div>
                <div className="flex items-center gap-1 bg-white border border-gray-200 rounded-xl p-1">
                    {CATEGORIES.map(cat => (
                        <button
                            key={cat}
                            onClick={() => setCategory(cat)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${category === cat
                                ? "bg-violet-100 text-violet-700 shadow-sm"
                                : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                                }`}
                        >
                            {cat === "All" ? "All" : cat.charAt(0).toUpperCase() + cat.slice(1)}
                        </button>
                    ))}
                </div>
            </div>

            {/* Stats Bar */}
            <div className="flex items-center gap-6 mb-6 text-xs text-gray-500">
                <span className="flex items-center gap-1.5">
                    <Package className="w-3.5 h-3.5" />
                    {filtered.length} playbooks
                </span>
                <span className="flex items-center gap-1.5">
                    <Zap className="w-3.5 h-3.5 text-amber-500" />
                    {playbooks.filter(p => p.is_builtin).length} built-in
                </span>
                <span className="flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-violet-500" />
                    {playbooks.filter(p => !p.is_builtin).length} custom
                </span>
            </div>

            {/* Playbook Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {filtered.map(pb => {
                    const colors = getColors(pb.color);
                    const isExpanded = expandedId === pb.id;
                    return (
                        <div
                            key={pb.id}
                            className={`bg-white rounded-2xl border ${isExpanded ? colors.border : "border-gray-100"} shadow-sm hover:shadow-md transition-all overflow-hidden group`}
                        >
                            {/* Card Header */}
                            <div className="p-5">
                                <div className="flex items-start justify-between mb-3">
                                    <div className="flex items-center gap-3">
                                        <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${colors.gradient} flex items-center justify-center text-white shadow-sm`}>
                                            {getIcon(pb.icon)}
                                        </div>
                                        <div>
                                            <h3 className="font-bold text-gray-900 text-sm">{pb.name.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase())}</h3>
                                            <div className="flex items-center gap-2 mt-0.5">
                                                <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-md ${colors.bg} ${colors.text}`}>
                                                    {pb.category}
                                                </span>
                                                <span className="text-[10px] text-gray-400">v{pb.version}</span>
                                                {pb.is_builtin && (
                                                    <span className="text-[10px] font-medium text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded-md">Built-in</span>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <p className="text-xs text-gray-600 leading-relaxed line-clamp-2 mb-3">{pb.description}</p>

                                {/* Tags */}
                                {pb.tags.length > 0 && (
                                    <div className="flex flex-wrap gap-1 mb-3">
                                        {pb.tags.slice(0, 4).map((tag, i) => (
                                            <span key={i} className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded">
                                                {tag}
                                            </span>
                                        ))}
                                    </div>
                                )}

                                {/* Quality Indicators */}
                                <div className="flex items-center gap-3 text-[10px] text-gray-400">
                                    {pb.anti_patterns.length > 0 && (
                                        <span className="flex items-center gap-0.5">🚫 {pb.anti_patterns.length} guards</span>
                                    )}
                                    {pb.quality_rubric.length > 0 && (
                                        <span className="flex items-center gap-0.5">📊 {pb.quality_rubric.length} criteria</span>
                                    )}
                                    {pb.evaluation_rules.length > 0 && (
                                        <span className="flex items-center gap-0.5">✅ {pb.evaluation_rules.length} checks</span>
                                    )}
                                    {pb.templates.length > 0 && (
                                        <span className="flex items-center gap-0.5">⚡ {pb.templates.length} templates</span>
                                    )}
                                </div>
                            </div>

                            {/* Expandable Details */}
                            <div className="border-t border-gray-50">
                                <button
                                    onClick={() => setExpandedId(isExpanded ? null : pb.id)}
                                    className="w-full px-5 py-2 flex items-center justify-between text-[10px] font-semibold text-gray-400 hover:bg-gray-50 transition-colors"
                                >
                                    <span>{isExpanded ? "Hide details" : "Show details"}</span>
                                    <ChevronDown className={`w-3 h-3 transition-transform ${isExpanded ? "rotate-180" : ""}`} />
                                </button>
                                {isExpanded && (
                                    <div className="px-5 pb-4 space-y-3">
                                        {/* Anti-patterns preview */}
                                        {pb.anti_patterns.length > 0 && (
                                            <div>
                                                <h4 className="text-[10px] font-bold text-gray-500 uppercase mb-1">Guards</h4>
                                                <div className="space-y-0.5">
                                                    {pb.anti_patterns.slice(0, 3).map((ap, i) => (
                                                        <p key={i} className="text-[10px] text-red-600 truncate">❌ {ap}</p>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                        {/* Templates */}
                                        {pb.templates.length > 0 && (
                                            <div>
                                                <h4 className="text-[10px] font-bold text-gray-500 uppercase mb-1">Quick Start</h4>
                                                <div className="space-y-0.5">
                                                    {pb.templates.map((t, i) => (
                                                        <p key={i} className="text-[10px] text-gray-600">⚡ {t.label}</p>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>

                            {/* Card Actions */}
                            <div className="px-5 py-3 border-t border-gray-50 bg-gray-50/50 flex items-center gap-2">
                                <button
                                    onClick={() => handleClone(pb.id)}
                                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:text-violet-600 hover:bg-violet-50 rounded-lg transition-all"
                                >
                                    <Copy className="w-3 h-3" />
                                    Clone & Edit
                                </button>
                                <a
                                    href={`/reasoning-lab?playbook=${pb.name}`}
                                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:text-emerald-600 hover:bg-emerald-50 rounded-lg transition-all ml-auto"
                                >
                                    <ArrowRight className="w-3 h-3" />
                                    Use
                                </a>
                            </div>
                        </div>
                    );
                })}
            </div>

            {filtered.length === 0 && (
                <div className="text-center py-20">
                    <Search className="w-12 h-12 text-gray-200 mx-auto mb-3" />
                    <p className="text-sm text-gray-400 font-medium">No playbooks found</p>
                    <p className="text-xs text-gray-300 mt-1">Try adjusting your search or filters</p>
                </div>
            )}
        </div>
    );
}
