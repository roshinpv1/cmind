import { useEffect, useState } from "react";
import {
    BookOpen,
    GitFork,
    Sparkles,
    Users,
    TrendingUp,
    Award,
    ArrowUpRight,
    Database,
    Layers,
    BarChart3,
    Target,
    Crown,
    Medal,
    Star,
    Activity,
    RefreshCw,
    Zap,
    Search,
    FolderOpen,
} from "lucide-react";
import { Link } from "react-router-dom";
import { authService } from "../../lib/auth";

// ─── Types ──────────────────────────────────────────────────────────────────
interface DashboardStats {
    totalRepos: number;
    activeComponents: number;
    proposedComponents: number;
    qualifiedComponents: number;
    totalContributors: number;
    totalSearches: number;
    reusabilityScore: number;
}

interface ContributorEntry {
    uid: string;
    org: string;
    contributions: number;
    rank: number;
}

interface ReuserEntry {
    uid: string;
    org: string;
    reusedCount: number;
    rank: number;
}

interface RecentActivity {
    action: string;
    target: string;
    user: string;
    time: string;
    type: "propose" | "contribute" | "index" | "search";
}

interface RankedItem {
    name: string;
    tag: string;
    value: number;
    rank: number;
}

// ─── Metric Card ────────────────────────────────────────────────────────────
function MetricCard({
    label,
    value,
    change,
    icon,
    color,
    bgGradient,
}: {
    label: string;
    value: number | string;
    change?: string;
    icon: React.ReactNode;
    color: string;
    bgGradient: string;
}) {
    return (
        <div className={`relative overflow-hidden rounded-2xl border border-gray-100 shadow-sm hover:shadow-lg transition-all duration-300 bg-white group`}>
            <div className={`absolute inset-0 opacity-5 ${bgGradient}`} />
            <div className="relative p-5">
                <div className="flex items-center justify-between mb-3">
                    <div className={`p-2.5 rounded-xl ${color} bg-opacity-10`}>
                        {icon}
                    </div>
                    {change && (
                        <span className="inline-flex items-center gap-0.5 text-xs font-bold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">
                            <ArrowUpRight className="h-3 w-3" />
                            {change}
                        </span>
                    )}
                </div>
                <p className="text-3xl font-black text-gray-900 tracking-tight">{value}</p>
                <p className="text-xs font-semibold text-gray-400 mt-1 uppercase tracking-wider">{label}</p>
            </div>
        </div>
    );
}

// ─── Leaderboard Row ────────────────────────────────────────────────────────
function LeaderboardRow({
    rank,
    name,
    org,
    value,
    valueLabel,
    maxValue,
}: {
    rank: number;
    name: string;
    org: string;
    value: number;
    valueLabel: string;
    maxValue: number;
}) {
    const rankIcon =
        rank === 1 ? <Crown className="h-4 w-4 text-amber-500" /> :
            rank === 2 ? <Medal className="h-4 w-4 text-gray-400" /> :
                rank === 3 ? <Medal className="h-4 w-4 text-orange-400" /> :
                    <span className="text-xs font-bold text-gray-400 w-4 text-center">{rank}</span>;

    const pct = Math.round((value / maxValue) * 100);

    return (
        <div className="flex items-center gap-3 py-2.5 px-3 rounded-xl hover:bg-gray-50 transition-colors group">
            <div className="w-6 flex items-center justify-center shrink-0">
                {rankIcon}
            </div>
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-gray-800 truncate">{name}</span>
                    {org && (
                        <span className="text-[10px] font-semibold text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">
                            {org}
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2 mt-1">
                    <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                        <div
                            className="h-full rounded-full bg-gradient-to-r from-violet-500 to-indigo-500 transition-all duration-700"
                            style={{ width: `${pct}%` }}
                        />
                    </div>
                    <span className="text-[10px] font-bold text-gray-500 tabular-nums w-10 text-right shrink-0">
                        {value} {valueLabel}
                    </span>
                </div>
            </div>
        </div>
    );
}

// ─── Dashboard Component ────────────────────────────────────────────────────
export default function Dashboard() {
    const [stats, setStats] = useState<DashboardStats | null>(null);
    const [contributors, setContributors] = useState<ContributorEntry[]>([]);
    const [reusers, setReusers] = useState<ReuserEntry[]>([]);
    const [activities, setActivities] = useState<RecentActivity[]>([]);
    const [topComponents, setTopComponents] = useState<RankedItem[]>([]);
    const [topSearched, setTopSearched] = useState<RankedItem[]>([]);
    const [topCategories, setTopCategories] = useState<RankedItem[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadDashboard();
    }, []);

    async function loadDashboard() {
        setLoading(true);
        try {
            // Fetch real data from APIs
            const [catalogRes, repoRes] = await Promise.all([
                fetch("/api/v1/catalogs/list", { headers: { ...authService.getAuthHeader() } }),
                fetch("/api/v1/repos", { headers: { ...authService.getAuthHeader() } }),
            ]);

            const catalogs = catalogRes.ok ? await catalogRes.json() : [];
            const repos = repoRes.ok ? await repoRes.json() : [];

            const active = catalogs.filter((c: any) => (c.status || "active") === "active").length;
            const proposed = catalogs.filter((c: any) => c.status === "proposed").length;
            const qualified = catalogs.filter((c: any) => c.status === "qualified").length;

            // Extract real contributors from catalog metadata
            const allContribs: Record<string, { uid: string; org: string; count: number }> = {};
            for (const cat of catalogs) {
                if (cat.contributors && Array.isArray(cat.contributors)) {
                    for (const c of cat.contributors) {
                        const key = c.uid;
                        if (!allContribs[key]) {
                            allContribs[key] = { uid: c.uid, org: c.org || "", count: 0 };
                        }
                        allContribs[key].count++;
                    }
                }
            }

            const realContributors = Object.values(allContribs)
                .sort((a, b) => b.count - a.count)
                .map((c, i) => ({ uid: c.uid, org: c.org, contributions: c.count, rank: i + 1 }));

            // Merge real + dummy contributors to fill the leaderboard
            const dummyContributors: ContributorEntry[] = [
                { uid: "sarah.chen", org: "Platform", contributions: 12, rank: 1 },
                { uid: "mike.johnson", org: "CT", contributions: 9, rank: 2 },
                { uid: "priya.sharma", org: "DTI", contributions: 7, rank: 3 },
                { uid: "alex.kumar", org: "CTO", contributions: 5, rank: 4 },
                { uid: "lisa.wang", org: "Engineering", contributions: 4, rank: 5 },
            ];

            // Use real contributors if available, fill remaining with dummy
            const mergedContributors = realContributors.length > 0
                ? [...realContributors, ...dummyContributors.filter(d => !realContributors.some(r => r.uid === d.uid))].slice(0, 5)
                    .map((c, i) => ({ ...c, rank: i + 1 }))
                : dummyContributors;

            // Dummy reusers (no API for this yet)
            const dummyReusers: ReuserEntry[] = [
                { uid: "david.lee", org: "Product", reusedCount: 18, rank: 1 },
                { uid: "emma.wilson", org: "Frontend", reusedCount: 14, rank: 2 },
                { uid: "raj.patel", org: "Backend", reusedCount: 11, rank: 3 },
                { uid: "nina.garcia", org: "DevOps", reusedCount: 8, rank: 4 },
                { uid: "tom.harris", org: "QA", reusedCount: 6, rank: 5 },
            ];

            // Dummy recent activities
            const dummyActivities: RecentActivity[] = [
                { action: "Proposed", target: "Event Logging Service", user: "sarah.chen", time: "2 min ago", type: "propose" },
                { action: "Contributed to", target: "API Gateway Module", user: "mike.johnson", time: "15 min ago", type: "contribute" },
                { action: "Indexed", target: "payment-service", user: "System", time: "1 hour ago", type: "index" },
                { action: "Searched", target: "Authentication middleware", user: "priya.sharma", time: "2 hours ago", type: "search" },
                { action: "Proposed", target: "Notification Hub", user: "alex.kumar", time: "3 hours ago", type: "propose" },
                { action: "Contributed to", target: "Cache Layer", user: "lisa.wang", time: "5 hours ago", type: "contribute" },
            ];

            setStats({
                totalRepos: repos.length || 0,
                activeComponents: active,
                proposedComponents: proposed,
                qualifiedComponents: qualified,
                totalContributors: mergedContributors.length + 23, // real + dummy
                totalSearches: 847, // dummy
                reusabilityScore: 73, // dummy
            });

            setContributors(mergedContributors);
            setReusers(dummyReusers);
            setActivities(dummyActivities);

            // ── Top Used Components (by quality score, from real data) ──
            const topUsed: RankedItem[] = catalogs
                .filter((c: any) => c.quality_score > 0)
                .sort((a: any, b: any) => (b.quality_score || 0) - (a.quality_score || 0))
                .slice(0, 5)
                .map((c: any, i: number) => ({
                    name: c.repo_name || c.repo_id,
                    tag: c.category || "General",
                    value: Math.round(c.quality_score || 0),
                    rank: i + 1,
                }));
            // Fill with dummy if < 5
            const dummyComponents: RankedItem[] = [
                { name: "API Gateway", tag: "Infrastructure", value: 92, rank: 1 },
                { name: "Auth Service", tag: "Security", value: 88, rank: 2 },
                { name: "Event Bus", tag: "Messaging", value: 85, rank: 3 },
                { name: "Config Manager", tag: "Platform", value: 79, rank: 4 },
                { name: "Cache Proxy", tag: "Performance", value: 74, rank: 5 },
            ];
            const mergedComponents = topUsed.length >= 5
                ? topUsed
                : [...topUsed, ...dummyComponents.slice(topUsed.length)].map((c, i) => ({ ...c, rank: i + 1 }));
            setTopComponents(mergedComponents);

            // ── Top Search-Appeared Components (dummy search hit data) ──
            const searchAppeared: RankedItem[] = catalogs
                .slice(0, 5)
                .map((c: any, i: number) => ({
                    name: c.repo_name || c.repo_id,
                    tag: c.category || "General",
                    value: [47, 38, 31, 24, 19][i] || 10, // dummy hit counts
                    rank: i + 1,
                }));
            setTopSearched(searchAppeared.length >= 5 ? searchAppeared : [
                { name: "Auth Middleware", tag: "Security", value: 47, rank: 1 },
                { name: "Payment Gateway", tag: "Fintech", value: 38, rank: 2 },
                { name: "Notification Hub", tag: "Messaging", value: 31, rank: 3 },
                { name: "User Service", tag: "Core", value: 24, rank: 4 },
                { name: "Data Pipeline", tag: "Analytics", value: 19, rank: 5 },
            ]);

            // ── Top Searched Categories (from real catalog categories + dummy counts) ──
            const catCounts: Record<string, number> = {};
            for (const cat of catalogs) {
                const category = cat.category || "Uncategorized";
                catCounts[category] = (catCounts[category] || 0) + 1;
            }
            const realCategories = Object.entries(catCounts)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([name, count], i) => ({
                    name,
                    tag: `${count} component${count > 1 ? "s" : ""}`,
                    value: [156, 124, 98, 67, 45][i] || 30, // dummy search counts
                    rank: i + 1,
                }));
            setTopCategories(realCategories.length >= 3 ? realCategories : [
                { name: "Infrastructure", tag: "8 components", value: 156, rank: 1 },
                { name: "Security", tag: "5 components", value: 124, rank: 2 },
                { name: "Messaging", tag: "4 components", value: 98, rank: 3 },
                { name: "Analytics", tag: "3 components", value: 67, rank: 4 },
                { name: "DevOps", tag: "3 components", value: 45, rank: 5 },
            ]);
        } catch (err) {
            console.error("Dashboard load error:", err);
            // Fallback: show all dummy data
            setStats({
                totalRepos: 0,
                activeComponents: 0,
                proposedComponents: 0,
                qualifiedComponents: 0,
                totalContributors: 28,
                totalSearches: 847,
                reusabilityScore: 73,
            });
        } finally {
            setLoading(false);
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[60vh]">
                <div className="flex flex-col items-center gap-3">
                    <div className="relative h-12 w-12">
                        <div className="absolute inset-0 rounded-full border-4 border-gray-100" />
                        <div className="absolute inset-0 rounded-full border-4 border-primary border-t-transparent animate-spin" />
                    </div>
                    <p className="text-sm text-gray-400 font-medium">Loading dashboard...</p>
                </div>
            </div>
        );
    }

    const s = stats!;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-black text-gray-900 flex items-center gap-2">
                        <BarChart3 className="h-6 w-6 text-primary" />
                        Discovery Dashboard
                    </h1>
                    <p className="text-sm text-gray-500 mt-0.5">
                        Component reuse intelligence at a glance
                    </p>
                </div>
                <button
                    onClick={loadDashboard}
                    className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-bold text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
                >
                    <RefreshCw className="h-3.5 w-3.5" />
                    Refresh
                </button>
            </div>

            {/* Metric Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <MetricCard
                    label="Repositories"
                    value={s.totalRepos}
                    change="+3 this week"
                    icon={<Database className="h-5 w-5 text-blue-600" />}
                    color="text-blue-600"
                    bgGradient="bg-gradient-to-br from-blue-500 to-cyan-500"
                />
                <MetricCard
                    label="Active Components"
                    value={s.activeComponents}
                    change="+5 this month"
                    icon={<BookOpen className="h-5 w-5 text-emerald-600" />}
                    color="text-emerald-600"
                    bgGradient="bg-gradient-to-br from-emerald-500 to-teal-500"
                />
                <MetricCard
                    label="Proposed"
                    value={s.proposedComponents}
                    icon={<Sparkles className="h-5 w-5 text-amber-600" />}
                    color="text-amber-600"
                    bgGradient="bg-gradient-to-br from-amber-500 to-orange-500"
                />
                <MetricCard
                    label="Qualified"
                    value={s.qualifiedComponents}
                    icon={<GitFork className="h-5 w-5 text-violet-600" />}
                    color="text-violet-600"
                    bgGradient="bg-gradient-to-br from-violet-500 to-purple-500"
                />
            </div>

            {/* Secondary Metrics */}
            <div className="grid grid-cols-3 gap-4">
                <MetricCard
                    label="Contributors"
                    value={s.totalContributors}
                    change="+8 this month"
                    icon={<Users className="h-5 w-5 text-teal-600" />}
                    color="text-teal-600"
                    bgGradient="bg-gradient-to-br from-teal-500 to-cyan-500"
                />
                <MetricCard
                    label="Catalog Searches"
                    value={s.totalSearches}
                    change="+142 this week"
                    icon={<Target className="h-5 w-5 text-indigo-600" />}
                    color="text-indigo-600"
                    bgGradient="bg-gradient-to-br from-indigo-500 to-blue-500"
                />
                <MetricCard
                    label="Reusability Score"
                    value={`${s.reusabilityScore}%`}
                    change="+4%"
                    icon={<TrendingUp className="h-5 w-5 text-rose-600" />}
                    color="text-rose-600"
                    bgGradient="bg-gradient-to-br from-rose-500 to-pink-500"
                />
            </div>

            {/* Leaderboards + Activity */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Top Contributors */}
                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                    <div className="px-5 pt-5 pb-3 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <Award className="h-5 w-5 text-amber-500" />
                            <h3 className="text-sm font-black text-gray-900 uppercase tracking-wider">Top Contributors</h3>
                        </div>
                        <span className="text-[10px] font-bold text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full">
                            This Month
                        </span>
                    </div>
                    <div className="px-2 pb-4 space-y-0.5">
                        {contributors.map((c) => (
                            <LeaderboardRow
                                key={c.uid}
                                rank={c.rank}
                                name={c.uid}
                                org={c.org}
                                value={c.contributions}
                                valueLabel=""
                                maxValue={Math.max(...contributors.map(x => x.contributions))}
                            />
                        ))}
                    </div>
                </div>

                {/* Top Reusers */}
                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                    <div className="px-5 pt-5 pb-3 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <Star className="h-5 w-5 text-indigo-500" />
                            <h3 className="text-sm font-black text-gray-900 uppercase tracking-wider">Top Reusers</h3>
                        </div>
                        <span className="text-[10px] font-bold text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full">
                            This Month
                        </span>
                    </div>
                    <div className="px-2 pb-4 space-y-0.5">
                        {reusers.map((r) => (
                            <LeaderboardRow
                                key={r.uid}
                                rank={r.rank}
                                name={r.uid}
                                org={r.org}
                                value={r.reusedCount}
                                valueLabel=""
                                maxValue={Math.max(...reusers.map(x => x.reusedCount))}
                            />
                        ))}
                    </div>
                </div>

                {/* Recent Activity */}
                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                    <div className="px-5 pt-5 pb-3 flex items-center gap-2">
                        <Activity className="h-5 w-5 text-emerald-500" />
                        <h3 className="text-sm font-black text-gray-900 uppercase tracking-wider">Recent Activity</h3>
                    </div>
                    <div className="px-4 pb-4 space-y-1">
                        {activities.map((a, i) => {
                            const typeColors = {
                                propose: "bg-amber-100 text-amber-700",
                                contribute: "bg-emerald-100 text-emerald-700",
                                index: "bg-blue-100 text-blue-700",
                                search: "bg-violet-100 text-violet-700",
                            };
                            return (
                                <div key={i} className="flex items-start gap-3 py-2.5 px-2 rounded-xl hover:bg-gray-50 transition-colors">
                                    <div className={`mt-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase shrink-0 ${typeColors[a.type]}`}>
                                        {a.type}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <p className="text-xs text-gray-700">
                                            <span className="font-bold">{a.user}</span>{" "}
                                            <span className="text-gray-400">{a.action}</span>{" "}
                                            <span className="font-semibold">{a.target}</span>
                                        </p>
                                        <p className="text-[10px] text-gray-400 mt-0.5">{a.time}</p>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>

            {/* Component & Category Leaderboards */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Top Used Components */}
                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                    <div className="px-5 pt-5 pb-3 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <Zap className="h-5 w-5 text-emerald-500" />
                            <h3 className="text-sm font-black text-gray-900 uppercase tracking-wider">Top Components</h3>
                        </div>
                        <span className="text-[10px] font-bold text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full">
                            By Quality
                        </span>
                    </div>
                    <div className="px-2 pb-4 space-y-0.5">
                        {topComponents.map((c) => (
                            <LeaderboardRow
                                key={c.name}
                                rank={c.rank}
                                name={c.name}
                                org={c.tag}
                                value={c.value}
                                valueLabel="pts"
                                maxValue={Math.max(...topComponents.map(x => x.value), 1)}
                            />
                        ))}
                    </div>
                </div>

                {/* Top Search-Appeared Components */}
                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                    <div className="px-5 pt-5 pb-3 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <Search className="h-5 w-5 text-blue-500" />
                            <h3 className="text-sm font-black text-gray-900 uppercase tracking-wider">Most Discovered</h3>
                        </div>
                        <span className="text-[10px] font-bold text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full">
                            Search Hits
                        </span>
                    </div>
                    <div className="px-2 pb-4 space-y-0.5">
                        {topSearched.map((c) => (
                            <LeaderboardRow
                                key={c.name}
                                rank={c.rank}
                                name={c.name}
                                org={c.tag}
                                value={c.value}
                                valueLabel="hits"
                                maxValue={Math.max(...topSearched.map(x => x.value), 1)}
                            />
                        ))}
                    </div>
                </div>

                {/* Top Searched Categories */}
                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                    <div className="px-5 pt-5 pb-3 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <FolderOpen className="h-5 w-5 text-violet-500" />
                            <h3 className="text-sm font-black text-gray-900 uppercase tracking-wider">Hot Categories</h3>
                        </div>
                        <span className="text-[10px] font-bold text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full">
                            Searches
                        </span>
                    </div>
                    <div className="px-2 pb-4 space-y-0.5">
                        {topCategories.map((c) => (
                            <LeaderboardRow
                                key={c.name}
                                rank={c.rank}
                                name={c.name}
                                org={c.tag}
                                value={c.value}
                                valueLabel=""
                                maxValue={Math.max(...topCategories.map(x => x.value), 1)}
                            />
                        ))}
                    </div>
                </div>
            </div>

            {/* Quick Links */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Link
                    to="/catalog-search"
                    className="flex items-center gap-3 p-4 bg-white rounded-xl border border-gray-100 hover:border-primary/30 hover:shadow-md transition-all group"
                >
                    <Layers className="h-5 w-5 text-primary group-hover:scale-110 transition-transform" />
                    <span className="text-sm font-bold text-gray-700 group-hover:text-primary">Catalog Search</span>
                </Link>
                <Link
                    to="/admin/catalogs"
                    className="flex items-center gap-3 p-4 bg-white rounded-xl border border-gray-100 hover:border-emerald-300 hover:shadow-md transition-all group"
                >
                    <BookOpen className="h-5 w-5 text-emerald-600 group-hover:scale-110 transition-transform" />
                    <span className="text-sm font-bold text-gray-700 group-hover:text-emerald-600">View Catalogs</span>
                </Link>
                <Link
                    to="/admin/repos"
                    className="flex items-center gap-3 p-4 bg-white rounded-xl border border-gray-100 hover:border-blue-300 hover:shadow-md transition-all group"
                >
                    <Database className="h-5 w-5 text-blue-600 group-hover:scale-110 transition-transform" />
                    <span className="text-sm font-bold text-gray-700 group-hover:text-blue-600">Repositories</span>
                </Link>
                <Link
                    to="/reasoning-lab"
                    className="flex items-center gap-3 p-4 bg-white rounded-xl border border-gray-100 hover:border-violet-300 hover:shadow-md transition-all group"
                >
                    <GitFork className="h-5 w-5 text-violet-600 group-hover:scale-110 transition-transform" />
                    <span className="text-sm font-bold text-gray-700 group-hover:text-violet-600">Reasoning Lab</span>
                </Link>
            </div>
        </div>
    );
}
