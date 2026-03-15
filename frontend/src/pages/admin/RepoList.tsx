import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    GitBranch,
    FileText,
    Clock,
    Plus,
    Pencil
} from "lucide-react";
import { authService } from "../../lib/auth";


interface Repo {
    repo_id: string;
    name: string;
    branch: string;
    path: string;
    status: string;
    total_files: number;
    last_indexed: string;
    first_author?: string;
    total_commits?: number;
    last_pr_title?: string;
}

export default function RepoList() {
    const [repos, setRepos] = useState<Repo[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        fetch("/api/v1/repos", {
            headers: { ...authService.getAuthHeader() }
        })
            .then((res) => {
                if (!res.ok) throw new Error("Failed to fetch repos");
                return res.json();
            })
            .then((data) => {
                setRepos(data);
                setLoading(false);
            })
            .catch((err) => {
                setError(err.message);
                setLoading(false);
            });
    }, []);

    if (loading) return <div className="p-8 text-center">Loading repositories...</div>;
    if (error) return <div className="p-8 text-center text-red-500">Error: {error}</div>;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-gray-900">Repositories</h1>
                <Link
                    to="/admin/index"
                    className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-primary hover:bg-red-700"
                >
                    <Plus className="w-4 h-4 mr-2" />
                    Index New Repo
                </Link>
            </div>

            <div className="bg-white shadow overflow-hidden sm:rounded-lg border border-gray-200">
                <ul className="divide-y divide-gray-200">
                    {repos.map((repo) => (
                        <li key={repo.repo_id} className="hover:bg-gray-50 transition-colors">
                            <div className="px-4 py-4 sm:px-6">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center min-w-0">
                                        <div className="flex-shrink-0">
                                            <div className="h-10 w-10 rounded-full bg-red-100 flex items-center justify-center">
                                                <GitBranch className="h-5 w-5 text-primary" />
                                            </div>
                                        </div>
                                        <div className="ml-4 truncate">
                                            <div className="flex text-sm font-medium text-primary truncate">
                                                {repo.name}
                                                <span className="ml-2 px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">
                                                    {repo.status}
                                                </span>
                                            </div>
                                            <div className="mt-1 flex items-center text-sm text-gray-500">
                                                <span className="truncate">{repo.path}</span>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-4">
                                        <div className="flex flex-col items-end text-sm text-gray-500 space-y-1">
                                            <div className="flex items-center">
                                                <FileText className="h-4 w-4 mr-1" />
                                                {repo.total_files} files
                                            </div>
                                            <div className="flex items-center">
                                                <Clock className="h-4 w-4 mr-1" />
                                                {new Date(repo.last_indexed).toLocaleDateString()}
                                            </div>
                                        </div>
                                        <Link
                                            to={`/admin/repos/${encodeURIComponent(repo.repo_id)}/edit`}
                                            className="p-2 text-gray-400 hover:text-primary hover:bg-red-50 rounded-md transition-colors"
                                            title="Edit metadata"
                                        >
                                            <Pencil className="h-4 w-4" />
                                        </Link>
                                    </div>
                                </div>
                                {repo.last_pr_title && (
                                    <div className="mt-2 pl-14 text-sm text-gray-500 border-l-2 border-gray-100 pl-2">
                                        <p className="truncate">Last PR: {repo.last_pr_title}</p>
                                        <p className="text-xs text-gray-400">by {repo.first_author} • {repo.total_commits} commits</p>
                                    </div>
                                )}
                            </div>
                        </li>
                    ))}
                    {repos.length === 0 && (
                        <li className="px-4 py-8 text-center text-gray-500">
                            No repositories indexed yet.
                        </li>
                    )}
                </ul>
            </div>
        </div>
    );
}
