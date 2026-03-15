import { useEffect, useState } from "react";

import { Play, Loader2 } from "lucide-react";

interface Repo {
    repo_id: string;
    name: string;
}

export default function CatalogCreate() {
    const [repos, setRepos] = useState<Repo[]>([]);
    const [selectedRepo, setSelectedRepo] = useState("");
    const [playbook, setPlaybook] = useState("code_analyzer");
    const [prompt, setPrompt] = useState("");
    const [loading, setLoading] = useState(false);
    const [fetchingRepos, setFetchingRepos] = useState(true);
    const [error, setError] = useState("");
    

    useEffect(() => {
        fetch("/api/v1/repos")
            .then((res) => res.json())
            .then((data) => {
                setRepos(data);
                if (data.length > 0) setSelectedRepo(data[0].repo_id);
                setFetchingRepos(false);
            })
            .catch(() => setFetchingRepos(false));
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError("");

        try {
            const res = await fetch("/api/v1/catalogs", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    repo_id: selectedRepo,
                    playbook_name: playbook,
                    prompt: prompt
                })
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || "Catalog creation failed");
            }

            // Success
            alert("Catalog entry created!");
            // Optionally redirect
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="max-w-2xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6">Create Catalog Entry</h1>

            <div className="bg-white shadow sm:rounded-lg p-6 border border-gray-200">
                <form onSubmit={handleSubmit} className="space-y-6">
                    {error && (
                        <div className="bg-red-50 border-l-4 border-red-400 p-4 text-red-700">
                            {error}
                        </div>
                    )}

                    <div>
                        <label className="block text-sm font-medium text-gray-700">Repository</label>
                        {fetchingRepos ? (
                            <div className="text-sm text-gray-400 mt-1">Loading repos...</div>
                        ) : (
                            <select
                                value={selectedRepo}
                                onChange={(e) => setSelectedRepo(e.target.value)}
                                className="mt-1 block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-primary focus:border-primary sm:text-sm rounded-md border"
                            >
                                {repos.map((r) => (
                                    <option key={r.repo_id} value={r.repo_id}>
                                        {r.name}
                                    </option>
                                ))}
                            </select>
                        )}
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700">Playbook</label>
                        <select
                            value={playbook}
                            onChange={(e) => setPlaybook(e.target.value)}
                            className="mt-1 block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-primary focus:border-primary sm:text-sm rounded-md border"
                        >
                            <option value="code_analyzer">Code Analyzer</option>
                            {/* Add more playbooks as they become available */}
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700">Instruction / Prompt</label>
                        <textarea
                            rows={4}
                            value={prompt}
                            onChange={(e) => setPrompt(e.target.value)}
                            className="mt-1 block w-full shadow-sm focus:ring-primary focus:border-primary sm:text-sm border-gray-300 rounded-md border p-2"
                            placeholder="e.g., Summarize the authentication logic... (Leave empty to use playbook default)"
                        />
                    </div>

                    <div className="flex justify-end">
                        <button
                            type="submit"
                            disabled={loading || !selectedRepo}
                            className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-primary hover:bg-red-700 focus:outline-none disabled:opacity-50"
                        >
                            {loading ? (
                                <Loader2 className="animate-spin -ml-1 mr-2 h-4 w-4" />
                            ) : (
                                <Play className="-ml-1 mr-2 h-4 w-4" />
                            )}
                            Run Playbook
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
