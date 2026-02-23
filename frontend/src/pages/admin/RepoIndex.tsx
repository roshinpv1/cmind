import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitGraph, Loader2 } from "lucide-react";

export default function RepoIndex() {
    const [url, setUrl] = useState("");
    const [branch, setBranch] = useState("main"); // specific branch if needed
    const [org, setOrg] = useState(""); // organization owning this component
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const navigate = useNavigate();

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError("");

        // Assuming POST /api/v1/index accepts { url, branch }?
        // Checking server.py implementation later. Usually it's index_repo(url).

        try {
            const res = await fetch("/api/v1/index", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                // Adjust payload based on actual API
                body: JSON.stringify({ repo_url: url, branch: branch || "main", org: org || undefined })
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || "Indexing failed");
            }

            const data = await res.json();
            // Usually returns job_id. We can redirect to list or show success.
            navigate("/admin/repos");
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="max-w-2xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6">Index Repository</h1>

            <div className="bg-white shadow sm:rounded-lg p-6 border border-gray-200">
                <form onSubmit={handleSubmit} className="space-y-6">
                    {error && (
                        <div className="bg-red-50 border-l-4 border-red-400 p-4 text-red-700">
                            {error}
                        </div>
                    )}

                    <div>
                        <label htmlFor="url" className="block text-sm font-medium text-gray-700">
                            Repository URL
                        </label>
                        <div className="mt-1 relative rounded-md shadow-sm">
                            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                <GitGraph className="h-5 w-5 text-gray-400" />
                            </div>
                            <input
                                type="text"
                                name="url"
                                id="url"
                                required
                                className="focus:ring-primary focus:border-primary block w-full pl-10 sm:text-sm border-gray-300 rounded-md py-2 border"
                                placeholder="https://github.com/owner/repo"
                                value={url}
                                onChange={(e) => setUrl(e.target.value)}
                            />
                        </div>
                        <p className="mt-2 text-sm text-gray-500">
                            Supports public and private repositories (if token configured).
                        </p>
                    </div>

                    <div>
                        <label htmlFor="branch" className="block text-sm font-medium text-gray-700">
                            Branch (Optional)
                        </label>
                        <div className="mt-1 relative rounded-md shadow-sm">
                            <input
                                type="text"
                                name="branch"
                                id="branch"
                                className="focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md py-2 border pl-3"
                                placeholder="main"
                                value={branch}
                                onChange={(e) => setBranch(e.target.value)}
                            />
                        </div>
                    </div>

                    <div>
                        <label htmlFor="org" className="block text-sm font-medium text-gray-700">
                            Organization (Optional)
                        </label>
                        <div className="mt-1 relative rounded-md shadow-sm">
                            <input
                                type="text"
                                name="org"
                                id="org"
                                className="focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md py-2 border pl-3"
                                placeholder="e.g. Platform Engineering"
                                value={org}
                                onChange={(e) => setOrg(e.target.value)}
                            />
                        </div>
                        <p className="mt-2 text-sm text-gray-500">
                            Team or org that owns this component. Shown in catalog and discovery results.
                        </p>
                    </div>

                    <div className="flex justify-end">
                        <button
                            type="submit"
                            disabled={loading}
                            className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-primary hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50"
                        >
                            {loading && <Loader2 className="animate-spin -ml-1 mr-2 h-4 w-4" />}
                            Start Indexing
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
