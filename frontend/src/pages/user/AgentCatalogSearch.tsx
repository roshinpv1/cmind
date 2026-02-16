import { useState, useEffect, useRef } from "react";
import { Send, Loader2, Terminal, FileCode, BookOpen } from "lucide-react";
import Markdown from "react-markdown";

// LogEntry removed as it was unused

interface AgentJob {
    job_id: string;
    status: "pending" | "running" | "completed" | "failed";
    result?: {
        answer: string;
        iterations: number;
        steps_taken: number;
    };
    logs: string[];
}

export default function AgentCatalogSearch() {
    // State
    const [goal, setGoal] = useState("");

    const [currentJobId, setCurrentJobId] = useState<string | null>(null);
    const [jobStatus, setJobStatus] = useState<AgentJob | null>(null);
    const [isPolling, setIsPolling] = useState(false);

    const logsEndRef = useRef<HTMLDivElement>(null);

    // Poll status
    useEffect(() => {
        if (!currentJobId || !isPolling) return;

        const interval = setInterval(async () => {
            try {
                const res = await fetch(`/api/v1/agents/autonomous/${currentJobId}/status`);
                if (res.ok) {
                    const data = await res.json();
                    setJobStatus(data);

                    if (data.status === "completed" || data.status === "failed") {
                        setIsPolling(false);
                        // Fetch final result details if completed
                        if (data.status === "completed") {
                            const resultRes = await fetch(`/api/v1/agents/autonomous/${currentJobId}/result`);
                            const resultData = await resultRes.json();
                            setJobStatus(prev => prev ? { ...prev, result: resultData } : resultData);
                        }
                    }
                }
            } catch (err) {
                console.error("Polling error", err);
            }
        }, 1000); // Poll every second for real-time feel

        return () => clearInterval(interval);
    }, [currentJobId, isPolling]);

    // Scroll logs to bottom
    useEffect(() => {
        logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [jobStatus?.logs]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!goal.trim()) return;

        // Reset state
        setJobStatus(null);
        setIsPolling(true);

        try {
            // Force catalog_search playbook
            const finalGoal = `Use the catalog_search playbook to: ${goal}`;

            const res = await fetch("/api/v1/agents/autonomous", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    goal: finalGoal,
                    repo_id: null, // Global search
                    max_iterations: 5, // catalog_search is usually single-shot
                    allowed_playbooks: ["catalog_search"]
                })
            });

            const data = await res.json();
            setCurrentJobId(data.job_id);
            setJobStatus({
                job_id: data.job_id,
                status: "pending",
                logs: ["Initializing Solution Architect..."]
            });
        } catch (err) {
            console.error("Failed to start job", err);
            setIsPolling(false);
        }
    };

    return (
        <div className="max-w-6xl mx-auto h-[calc(100vh-6rem)] flex flex-col space-y-4">
            {/* Header */}
            <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200 flex flex-col items-center justify-center text-center">
                <div className="flex items-center gap-3 mb-2">
                    <BookOpen className="h-8 w-8 text-primary" />
                    <h1 className="text-2xl font-bold text-gray-900">Solution Architect Agent</h1>
                </div>
                <p className="text-gray-500">
                    Describe your software requirement (e.g., "I need an e-commerce backend"). The agent will search the catalog and recommend the best components.
                </p>
            </div>

            {/* Main Content Area: Split View */}
            <div className="flex-1 flex gap-4 min-h-0">
                {/* Left: Chat / Input / Logs */}
                <div className="flex-1 flex flex-col bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">

                    {/* Log Stream -> Current Activity Display */}
                    <div className="flex-1 overflow-y-auto p-8 bg-gray-50 flex flex-col items-center justify-center text-center">
                        {!currentJobId && !jobStatus && (
                            <div className="text-gray-400 flex flex-col items-center">
                                <Terminal className="h-16 w-16 opacity-10 mb-4" />
                                <p className="text-lg font-medium">Ready to design solutions.</p>
                                <div className="mt-6 grid grid-cols-1 gap-2 text-sm text-gray-500">
                                    <p>Try asking:</p>
                                    <button onClick={() => setGoal("Recommend modules for a video streaming app")} className="px-3 py-1 bg-white border border-gray-200 rounded-full hover:border-primary hover:text-primary transition-colors">
                                        "Recommend modules for a video streaming app"
                                    </button>
                                    <button onClick={() => setGoal("I need a vector search system")} className="px-3 py-1 bg-white border border-gray-200 rounded-full hover:border-primary hover:text-primary transition-colors">
                                        "I need a vector search system"
                                    </button>
                                </div>
                            </div>
                        )}

                        {jobStatus?.logs && jobStatus.logs.length > 0 && (
                            <div className="max-w-xl w-full animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <div className="mb-4 text-xs font-semibold text-gray-400 uppercase tracking-widest">
                                    Current Activity
                                </div>
                                {(() => {
                                    const latestLog = jobStatus.logs[jobStatus.logs.length - 1];
                                    const isThought = latestLog.startsWith("Thinking:");
                                    const isAction = latestLog.startsWith("Action");
                                    const cleanText = latestLog.replace(/^(Thinking:|Action \d+:)/, '').trim();

                                    return (
                                        <div className={`p-6 rounded-xl shadow-sm border transition-all duration-300 ${isAction ? "bg-blue-50 border-blue-100 text-blue-900" :
                                            isThought ? "bg-white border-gray-200 text-gray-700" :
                                                "bg-gray-100 text-gray-800"
                                            }`}>
                                            <div className="flex items-center justify-center gap-3 mb-3">
                                                {isAction ? (
                                                    <BookOpen className="h-5 w-5 text-blue-500" />
                                                ) : (
                                                    <Loader2 className="h-5 w-5 text-indigo-500 animate-spin" />
                                                )}
                                                <span className={`font-bold text-sm uppercase ${isAction ? "text-blue-600" : "text-indigo-600"}`}>
                                                    {isAction ? "Searching Catalog" : "Reasoning"}
                                                </span>
                                            </div>
                                            <div className="font-mono text-sm md:text-base leading-relaxed whitespace-pre-wrap text-left bg-white/50 p-3 rounded border border-black/5">
                                                {cleanText}
                                            </div>
                                        </div>
                                    );
                                })()}
                            </div>
                        )}

                        {jobStatus?.status === "completed" && (
                            <div className="mt-8 text-green-600 font-medium flex items-center gap-2 animate-in fade-in">
                                <span className="h-2 w-2 rounded-full bg-green-500" />
                                Analysis Completed
                            </div>
                        )}
                    </div>

                    {/* Input Area */}
                    <div className="p-4 bg-white border-t border-gray-200">
                        <form onSubmit={handleSubmit} className="flex gap-2">
                            <input
                                type="text"
                                className="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent font-sans"
                                placeholder="Describe your requirement..."
                                value={goal}
                                onChange={(e) => setGoal(e.target.value)}
                                disabled={isPolling}
                            />
                            <button
                                type="submit"
                                disabled={isPolling || !goal.trim()}
                                className="bg-primary text-white px-6 py-2 rounded-lg font-medium hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 transition-colors"
                            >
                                {isPolling ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
                                Analyze
                            </button>
                        </form>
                    </div>
                </div>

                {/* Right: Final Result Display */}
                <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex flex-col">
                    <div className="bg-gray-100 px-4 py-2 border-b border-gray-200 font-semibold text-gray-700 flex items-center gap-2">
                        <FileCode className="h-4 w-4" /> Answer
                    </div>
                    <div className="flex-1 overflow-y-auto p-6 prose prose-red max-w-none">
                        {jobStatus?.result?.answer ? (
                            <Markdown>{jobStatus.result.answer}</Markdown>
                        ) : (
                            <div className="text-center text-gray-400 mt-20 italic">
                                Agent's answer will appear here.
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
