import { useState } from "react";
import { Search, Loader2 } from "lucide-react";
import Markdown from "react-markdown";

interface SearchResult {
    catalog_id: string;
    repo_name: string;
    result: string;
    score: number;
    metadata: string; // JSON string
    created_at: string;
}

export default function CatalogSearch() {
    const [query, setQuery] = useState("");
    const [results, setResults] = useState<SearchResult[]>([]);
    const [loading, setLoading] = useState(false);
    const [hasSearched, setHasSearched] = useState(false);

    // Advanced parameters
    const [limit, setLimit] = useState(5);
    const [minScore, setMinScore] = useState(0.7);

    const handleSearch = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!query.trim()) return;

        setLoading(true);
        setHasSearched(true);

        try {
            const res = await fetch("/api/v1/catalogs/search", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    query: query,
                    limit: limit,
                    min_score: minScore
                })
            });

            const data = await res.json();
            setResults(data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="max-w-4xl mx-auto space-y-8">
            <div className="text-center space-y-4">
                <h1 className="text-4xl font-bold text-gray-900">UnifyX Search</h1>
                <p className="text-xl text-gray-600">
                    Semantic search across your indexed repositories and catalogs.
                </p>
            </div>

            {/* Search Input */}
            <div className="max-w-2xl mx-auto relative space-y-4">
                <form onSubmit={handleSearch}>
                    <div className="relative">
                        <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                            <Search className="h-6 w-6 text-gray-400" />
                        </div>
                        <input
                            type="text"
                            className="block w-full pl-12 pr-4 py-4 border border-gray-300 rounded-full leading-5 bg-white placeholder-gray-500 focus:outline-none focus:placeholder-gray-400 focus:ring-2 focus:ring-primary focus:border-primary sm:text-lg shadow-sm transition-shadow hover:shadow-md"
                            placeholder="Ask a question about your code..."
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                        />
                        <button
                            type="submit"
                            disabled={loading}
                            className="absolute inset-y-2 right-2 px-6 py-2 bg-primary text-white font-medium rounded-full hover:bg-red-700 disabled:opacity-50 transition-colors"
                        >
                            {loading ? <Loader2 className="animate-spin h-5 w-5" /> : "Search"}
                        </button>
                    </div>
                </form>

                {/* Advanced Filters */}
                <div className="flex justify-center space-x-8 text-sm text-gray-600 bg-white p-4 rounded-lg shadow-sm border border-gray-100 max-w-lg mx-auto">
                    <div className="flex items-center space-x-2">
                        <label htmlFor="limit" className="font-medium">Num Results:</label>
                        <select
                            id="limit"
                            value={limit}
                            onChange={(e) => setLimit(Number(e.target.value))}
                            className="border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary py-1"
                        >
                            <option value="3">3</option>
                            <option value="5">5</option>
                            <option value="10">10</option>
                            <option value="20">20</option>
                        </select>
                    </div>

                    <div className="flex items-center space-x-2">
                        <label htmlFor="score" className="font-medium">Min Similarity:</label>
                        <input
                            type="range"
                            id="score"
                            min="0.5"
                            max="0.95"
                            step="0.05"
                            value={minScore}
                            onChange={(e) => setMinScore(Number(e.target.value))}
                            className="w-24 accent-primary"
                        />
                        <span>{(minScore * 100).toFixed(0)}%</span>
                    </div>
                </div>
            </div>

            {/* Results */}
            <div className="space-y-6">
                {results.map((item) => (
                    <div key={item.catalog_id} className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden hover:shadow-md transition-shadow">
                        <div className="px-6 py-4 border-b border-gray-100 bg-gray-50 flex justify-between items-center">
                            <div>
                                <h3 className="text-lg font-semibold text-gray-900">{item.repo_name}</h3>
                                <p className="text-xs text-gray-500">
                                    Matches similarity: {(item.score * 100).toFixed(0)}%
                                </p>
                            </div>
                            <span className="text-xs text-gray-400">
                                {new Date(item.created_at).toLocaleDateString()}
                            </span>
                        </div>
                        <div className="px-6 py-4 prose prose-red max-w-none">
                            <div className="line-clamp-6 text-gray-700 whitespace-pre-wrap">
                                <Markdown>{item.result}</Markdown>
                            </div>
                        </div>
                        <div className="px-6 py-3 bg-gray-50 border-t border-gray-100 text-right">
                            <button className="text-sm font-medium text-primary hover:text-red-700">
                                Read full analysis →
                            </button>
                        </div>
                    </div>
                ))}

                {hasSearched && !loading && results.length === 0 && (
                    <div className="text-center py-12 text-gray-500">
                        No relevant results found. Try adjusting your query.
                    </div>
                )}
            </div>
        </div>
    );
}
