import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
    Save,
    ArrowLeft,
    GitBranch,
    Building2,
    User,
    FileText,
    Hash,
    Globe,
    Calendar,
    Loader2,
    CheckCircle,
    AlertCircle
} from "lucide-react";
import { authService } from "../../lib/auth";

interface RepoDetail {
    repo_id: string;
    name: string;
    branch: string | null;
    path: string;
    repo_url: string | null;
    org: string | null;
    status: string;
    total_files: number;
    last_indexed: string;
    first_author: string | null;
    total_commits: number | null;
    last_pr_title: string | null;
    last_pr_user: string | null;
    last_pr_merged_at: string | null;
    embedding_model: string | null;
    embedding_version: number | null;
    last_commit_hash: string | null;
}

interface FormData {
    org: string;
    repo_url: string;
    branch: string;
    first_author: string;
    total_commits: string;
    last_pr_title: string;
    last_pr_user: string;
    last_pr_merged_at: string;
}

export default function RepoEdit() {
    const { repoId } = useParams<{ repoId: string }>();
    const navigate = useNavigate();
    const [repo, setRepo] = useState<RepoDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");
    const [success, setSuccess] = useState("");
    const [form, setForm] = useState<FormData>({
        org: "",
        repo_url: "",
        branch: "",
        first_author: "",
        total_commits: "",
        last_pr_title: "",
        last_pr_user: "",
        last_pr_merged_at: "",
    });

    useEffect(() => {
        if (!repoId) return;
        fetch(`/api/v1/repos/${encodeURIComponent(repoId)}`, { headers: { ...authService.getAuthHeader() } })
            .then((res) => {
                if (!res.ok) throw new Error("Repository not found");
                return res.json();
            })
            .then((data: RepoDetail) => {
                setRepo(data);
                setForm({
                    org: data.org || "",
                    repo_url: data.repo_url || "",
                    branch: data.branch || "",
                    first_author: data.first_author || "",
                    total_commits: data.total_commits?.toString() || "",
                    last_pr_title: data.last_pr_title || "",
                    last_pr_user: data.last_pr_user || "",
                    last_pr_merged_at: data.last_pr_merged_at || "",
                });
                setLoading(false);
            })
            .catch((err) => {
                setError(err.message);
                setLoading(false);
            });
    }, [repoId]);

    const handleChange = (field: keyof FormData, value: string) => {
        setForm((prev) => ({ ...prev, [field]: value }));
        setSuccess("");
    };

    const handleSave = async () => {
        if (!repoId) return;
        setSaving(true);
        setError("");
        setSuccess("");

        // Build update payload — only send non-empty changed fields
        const payload: Record<string, string | number> = {};
        if (form.org && form.org !== (repo?.org || "")) payload.org = form.org;
        if (form.repo_url && form.repo_url !== (repo?.repo_url || "")) payload.repo_url = form.repo_url;
        if (form.branch && form.branch !== (repo?.branch || "")) payload.branch = form.branch;
        if (form.first_author && form.first_author !== (repo?.first_author || "")) payload.first_author = form.first_author;
        if (form.total_commits && form.total_commits !== (repo?.total_commits?.toString() || ""))
            payload.total_commits = parseInt(form.total_commits);
        if (form.last_pr_title && form.last_pr_title !== (repo?.last_pr_title || "")) payload.last_pr_title = form.last_pr_title;
        if (form.last_pr_user && form.last_pr_user !== (repo?.last_pr_user || "")) payload.last_pr_user = form.last_pr_user;
        if (form.last_pr_merged_at && form.last_pr_merged_at !== (repo?.last_pr_merged_at || ""))
            payload.last_pr_merged_at = form.last_pr_merged_at;

        if (Object.keys(payload).length === 0) {
            setError("No changes to save");
            setSaving(false);
            return;
        }

        try {
            const res = await fetch(`/api/v1/repos/${encodeURIComponent(repoId)}`, {
                method: "PUT",
                headers: { 
                    "Content-Type": "application/json",
                    ...authService.getAuthHeader()
                },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || "Update failed");
            }
            const result = await res.json();
            setSuccess(`Updated: ${result.fields_updated.join(", ")}`);
            // Refresh repo data
            const refreshRes = await fetch(`/api/v1/repos/${encodeURIComponent(repoId)}`, { headers: { ...authService.getAuthHeader() } });
            if (refreshRes.ok) {
                const freshData = await refreshRes.json();
                setRepo(freshData);
            }
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Update failed");
        } finally {
            setSaving(false);
        }
    };

    if (loading) return <div className="p-8 text-center">Loading repository...</div>;
    if (!repo) return <div className="p-8 text-center text-red-500">Repository not found</div>;

    return (
        <div className="space-y-6 max-w-4xl">
            {/* Header */}
            <div className="flex items-center gap-4">
                <button
                    onClick={() => navigate("/admin/repos")}
                    className="p-2 hover:bg-gray-100 rounded-md transition-colors"
                >
                    <ArrowLeft className="w-5 h-5" />
                </button>
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Edit Repository</h1>
                    <p className="text-sm text-gray-500 mt-1">{repo.name}</p>
                </div>
            </div>

            {/* Status messages */}
            {error && (
                <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    {error}
                </div>
            )}
            {success && (
                <div className="flex items-center gap-2 p-3 bg-green-50 border border-green-200 rounded-lg text-green-700 text-sm">
                    <CheckCircle className="w-4 h-4 flex-shrink-0" />
                    {success}
                </div>
            )}

            {/* Read-only info card */}
            <div className="bg-white shadow rounded-lg border border-gray-200 p-6">
                <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">System Information</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                    <div className="flex items-center gap-2">
                        <Hash className="w-4 h-4 text-gray-400" />
                        <span className="font-medium text-gray-500">Repo ID:</span>
                        <span className="text-gray-900 font-mono text-xs truncate">{repo.repo_id}</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <FileText className="w-4 h-4 text-gray-400" />
                        <span className="font-medium text-gray-500">Files:</span>
                        <span className="text-gray-900">{repo.total_files.toLocaleString()}</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <Calendar className="w-4 h-4 text-gray-400" />
                        <span className="font-medium text-gray-500">Last Indexed:</span>
                        <span className="text-gray-900">{new Date(repo.last_indexed).toLocaleString()}</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <Hash className="w-4 h-4 text-gray-400" />
                        <span className="font-medium text-gray-500">Commit:</span>
                        <span className="text-gray-900 font-mono text-xs">{repo.last_commit_hash?.slice(0, 8) || "—"}</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <FileText className="w-4 h-4 text-gray-400" />
                        <span className="font-medium text-gray-500">Embedding:</span>
                        <span className="text-gray-900">{repo.embedding_model} (v{repo.embedding_version})</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <Globe className="w-4 h-4 text-gray-400" />
                        <span className="font-medium text-gray-500">Path:</span>
                        <span className="text-gray-900 text-xs truncate">{repo.path}</span>
                    </div>
                </div>
            </div>

            {/* Editable fields */}
            <div className="bg-white shadow rounded-lg border border-gray-200 p-6">
                <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-6">Editable Metadata</h2>

                <div className="space-y-5">
                    {/* Core fields */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                        <div>
                            <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                                <Building2 className="w-4 h-4" />
                                Organization
                            </label>
                            <input
                                type="text"
                                value={form.org}
                                onChange={(e) => handleChange("org", e.target.value)}
                                placeholder="e.g. Engineering, Platform"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-sm"
                            />
                        </div>
                        <div>
                            <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                                <Globe className="w-4 h-4" />
                                Repository URL
                            </label>
                            <input
                                type="url"
                                value={form.repo_url}
                                onChange={(e) => handleChange("repo_url", e.target.value)}
                                placeholder="https://github.com/org/repo"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-sm"
                            />
                        </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                        <div>
                            <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                                <GitBranch className="w-4 h-4" />
                                Branch
                            </label>
                            <input
                                type="text"
                                value={form.branch}
                                onChange={(e) => handleChange("branch", e.target.value)}
                                placeholder="main"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-sm"
                            />
                        </div>
                        <div>
                            <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                                <User className="w-4 h-4" />
                                First Author
                            </label>
                            <input
                                type="text"
                                value={form.first_author}
                                onChange={(e) => handleChange("first_author", e.target.value)}
                                placeholder="Author name"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-sm"
                            />
                        </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                        <div>
                            <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                                <Hash className="w-4 h-4" />
                                Total Commits
                            </label>
                            <input
                                type="number"
                                value={form.total_commits}
                                onChange={(e) => handleChange("total_commits", e.target.value)}
                                placeholder="0"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-sm"
                            />
                        </div>
                        <div>
                            <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                                <User className="w-4 h-4" />
                                Last PR User
                            </label>
                            <input
                                type="text"
                                value={form.last_pr_user}
                                onChange={(e) => handleChange("last_pr_user", e.target.value)}
                                placeholder="PR author"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-sm"
                            />
                        </div>
                        <div>
                            <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                                <Calendar className="w-4 h-4" />
                                Last PR Merged
                            </label>
                            <input
                                type="text"
                                value={form.last_pr_merged_at}
                                onChange={(e) => handleChange("last_pr_merged_at", e.target.value)}
                                placeholder="2024-01-15T10:30:00Z"
                                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-sm"
                            />
                        </div>
                    </div>

                    <div>
                        <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1">
                            <FileText className="w-4 h-4" />
                            Last PR Title
                        </label>
                        <input
                            type="text"
                            value={form.last_pr_title}
                            onChange={(e) => handleChange("last_pr_title", e.target.value)}
                            placeholder="Fix: resolve authentication issue in middleware"
                            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary text-sm"
                        />
                    </div>
                </div>
            </div>

            {/* Save button */}
            <div className="flex justify-end gap-3">
                <button
                    onClick={() => navigate("/admin/repos")}
                    className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md shadow-sm hover:bg-gray-50"
                >
                    Cancel
                </button>
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-primary border border-transparent rounded-md shadow-sm hover:bg-red-700 disabled:opacity-50"
                >
                    {saving ? (
                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    ) : (
                        <Save className="w-4 h-4 mr-2" />
                    )}
                    {saving ? "Saving..." : "Save Changes"}
                </button>
            </div>
        </div>
    );
}
