import { useState, useEffect, useRef } from "react";
import { Send, Loader2, Play, Terminal, Book, FileCode } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Repo {
    id: string;
    path: string;
}

interface LogEntry {
    message: string;
    type: "thought" | "action";
    timestamp: string;
}

interface AgentJob {
    job_id: string;
    status: "pending" | "running" | "completed" | "failed";
    result?: {
        answer: any;
        iterations: number;
        steps_taken: number;
    };
    logs: string[];
}

export default function ChatInterface() {
    // State
    const [repos, setRepos] = useState<Repo[]>([]);
    const [selectedRepo, setSelectedRepo] = useState("");

    const [goal, setGoal] = useState("");
    const [selectedPlaybook, setSelectedPlaybook] = useState("auto"); // auto, code_analyzer, catalog_browser

    const [currentJobId, setCurrentJobId] = useState<string | null>(null);
    const [jobStatus, setJobStatus] = useState<AgentJob | null>(null);
    const [isPolling, setIsPolling] = useState(false);

    const logsEndRef = useRef<HTMLDivElement>(null);

    // Load repositories on mount
    useEffect(() => {
        fetch("/api/v1/repos") // We'll need to ensure this endpoint returns IDs
            .then(res => res.json())
            .then(data => {
                // Assuming data is list of strings or objects. 
                // Adjust based on actual API response structure for /repos
                // If /repos returns paths, we might need a way to get IDs. 
                // For now, let's assume the API returns objects with id/path or just map path to id if they are same.
                // Actually server.py list_repos returns list of objects with repo_id and path.
                setRepos(data);
                if (data.length > 0) setSelectedRepo(data[0].repo_id);
            })
            .catch(err => console.error("Failed to load repos", err));
    }, []);

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
        if (!goal.trim() || !selectedRepo) return;

        // Reset state
        setJobStatus(null);
        setIsPolling(true);

        try {
            // If specific playbook selected, wrap goal? 
            // Actually, the autonomous agent picks the playbook.
            // But if we want to FORCE a playbook, we might need to change the API or prompt.
            // "User selected Catalog Browser". 
            // We can prepend instruction: "Use the catalog_browser playbook to..."

            let finalGoal = goal;
            let allowedPlaybooks = null;

            if (selectedPlaybook !== "auto") {
                finalGoal = `Use the ${selectedPlaybook} playbook to: ${goal}`;
                allowedPlaybooks = [selectedPlaybook];
            }

            const res = await fetch("/api/v1/agents/autonomous", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    goal: finalGoal,
                    repo_id: selectedRepo,
                    max_iterations: 15,
                    allowed_playbooks: allowedPlaybooks
                })
            });

            const data = await res.json();
            setCurrentJobId(data.job_id);
            setJobStatus({
                job_id: data.job_id,
                status: "pending",
                logs: ["Initializing..."]
            });
        } catch (err) {
            console.error("Failed to start job", err);
            setIsPolling(false);
        }
    };

    const renderLogLine = (log: string, index: number) => {
        const isThought = log.startsWith("Thinking:");
        const isAction = log.startsWith("Action");

        return (
            <div key={index} className={`mb-2 font-mono text-xs md:text-sm p-2 rounded ${isAction ? "bg-blue-50 text-blue-800 border-l-4 border-blue-400" :
                isThought ? "bg-gray-50 text-gray-600 italic" :
                    "text-gray-700"
                }`}>
                {log}
            </div>
        );
    };

    return (
        <div className="max-w-6xl mx-auto h-[calc(100vh-6rem)] flex flex-col space-y-4">
            {/* Header / Config */}
            <div className="bg-white p-4 rounded-lg shadow-sm border border-gray-200 flex flex-wrap gap-4 items-center justify-between">
                <div className="flex items-center gap-4 flex-1">
                    <div className="w-64">
                        <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider block mb-1">Target Repository</label>
                        <select
                            value={selectedRepo}
                            onChange={(e) => setSelectedRepo(e.target.value)}
                            className="w-full text-sm border-gray-300 rounded-md shadow-sm focus:border-primary focus:ring-primary"
                        >
                            {repos.map(r => (
                                <option key={r.id} value={r.id}>{r.path.split('/').pop()}</option>
                            ))}
                        </select>
                    </div>

                    <div className="w-64">
                        <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider block mb-1">Agent Strategy</label>
                        <select
                            value={selectedPlaybook}
                            onChange={(e) => setSelectedPlaybook(e.target.value)}
                            className="w-full text-sm border-gray-300 rounded-md shadow-sm focus:border-primary focus:ring-primary"
                        >
                            <option value="auto">🤖 Auto-Pilot (Let Agent Decide)</option>
                            <option value="code_analyzer">💻 Deep Code Analysis</option>
                            <option value="catalog_browser">📚 Catalog Search</option>
                            <option value="generate_catalog">🏗️ Catalog Generator</option>
                            <option value="analyze_svp">📊 SVP Product Analyzer</option>
                        </select>
                    </div>
                </div>
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
                                <p className="text-lg font-medium">Ready to assist.</p>
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
                                                    <Play className="h-5 w-5 text-blue-500" />
                                                ) : (
                                                    <Loader2 className="h-5 w-5 text-indigo-500 animate-spin" />
                                                )}
                                                <span className={`font-bold text-sm uppercase ${isAction ? "text-blue-600" : "text-indigo-600"}`}>
                                                    {isAction ? "Executing Tool" : "Reasoning"}
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
                                Task Completed
                            </div>
                        )}
                    </div>

                    {/* Input Area */}
                    <div className="p-4 bg-white border-t border-gray-200">
                        <form onSubmit={handleSubmit} className="flex gap-2">
                            <input
                                type="text"
                                className="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent font-sans"
                                placeholder={
                                    selectedPlaybook === 'catalog_browser' ? "Search for architectural patterns or repo features..." :
                                        selectedPlaybook === 'generate_catalog' ? "Analyze repo and generate catalog entry..." :
                                            "Describe your coding task or question..."
                                }
                                value={goal}
                                onChange={(e) => setGoal(e.target.value)}
                                disabled={isPolling}
                            />
                            <button
                                type="submit"
                                disabled={isPolling || !goal.trim() || !selectedRepo}
                                className="bg-primary text-white px-6 py-2 rounded-lg font-medium hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 transition-colors"
                            >
                                {isPolling ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
                                Start
                            </button>
                        </form>
                    </div>
                </div>

                {/* Right: Final Result Display */}
                <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex flex-col">
                    <div className="bg-gray-100 px-4 py-2 border-b border-gray-200 font-semibold text-gray-700 flex items-center gap-2">
                        <FileCode className="h-4 w-4" /> Final Answer
                    </div>
                    <div className="flex-1 overflow-y-auto p-6 prose prose-red max-w-none">
                        {jobStatus?.result?.answer ? (
                            <Markdown remarkPlugins={[remarkGfm]}>{
                                typeof jobStatus.result.answer === "string"
                                    ? jobStatus.result.answer
                                    : jobStatus.result.answer.report_markdown
                                        ? jobStatus.result.answer.report_markdown
                                        : JSON.stringify(jobStatus.result.answer, null, 2)
                            }</Markdown>
                        ) : (
                            <div className="text-center text-gray-400 mt-20 italic">
                                Final answer will appear here when analysis is complete.
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
