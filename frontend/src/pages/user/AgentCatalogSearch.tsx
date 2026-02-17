import { useState, useEffect, useRef } from "react";
import { Send, Loader2, Terminal, FileCode, BookOpen, Info } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

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
        }, 1000);

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
            const finalGoal = `Use the catalog_search playbook to analyze this request: ${goal}`;

            const res = await fetch("/api/v1/agents/autonomous", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    goal: finalGoal,
                    repo_id: null, // Always global
                    max_iterations: 5,
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
        <div className="bg-gray-50 h-[calc(100vh-4rem)] overflow-hidden">
            {/* Main Workspace */}
            <main className="h-full flex flex-col min-w-0 bg-gray-50">
                {/* Status Bar */}
                <div className="h-14 bg-white border-b border-gray-200 px-6 flex items-center justify-between shadow-sm shrink-0">
                    <div className="flex items-center gap-4 overflow-hidden">
                        <div className="flex items-center gap-2 text-gray-900">
                            <BookOpen className="h-5 w-5 text-primary" />
                            <h1 className="font-bold whitespace-nowrap">Global Catalog Search</h1>
                        </div>
                        <div className="h-4 w-[1px] bg-gray-200" />
                        <span className="text-sm text-gray-400 flex items-center gap-2">
                            <Info className="h-3.5 w-3.5" />
                            Analyzing entire repository ecosystem
                        </span>
                    </div>
                    <div className="flex items-center gap-4 text-[10px] font-bold uppercase tracking-widest text-gray-400">
                        {isPolling && (
                            <div className="flex items-center gap-2 text-indigo-600 bg-indigo-50 px-3 py-1 rounded-full animate-pulse">
                                <Loader2 className="h-3 w-3 animate-spin" />
                                Agent Active
                            </div>
                        )}
                    </div>
                </div>

                <div className="flex-1 flex min-h-0">
                    {/* Activity Column */}
                    <div className="flex-1 flex flex-col border-r border-gray-200 bg-white/40 backdrop-blur-sm relative">
                        {/* Log Visualization */}
                        <div className="flex-1 overflow-y-auto p-6 flex flex-col items-center justify-center relative">
                            {!currentJobId && !jobStatus && (
                                <div className="max-w-md w-full text-center space-y-6 animate-in fade-in duration-700">
                                    <div className="inline-flex p-5 bg-white rounded-3xl shadow-xl shadow-red-500/5 ring-1 ring-gray-100 relative">
                                        <Terminal className="h-12 w-12 text-primary/20" />
                                        <div className="absolute inset-0 bg-primary/5 rounded-3xl animate-pulse" />
                                    </div>
                                    <div>
                                        <h2 className="text-xl font-bold text-gray-900 mb-2">Global Architect Intelligence</h2>
                                        <p className="text-sm text-gray-500">I will analyze your requirements and recommend the best reusable modules from across the entire ecosystem.</p>
                                    </div>
                                    <div className="grid grid-cols-1 gap-2 text-sm">
                                        <button onClick={() => setGoal("Recommend modules for a high-performance video streaming platform")} className="px-4 py-3 bg-white border border-gray-100 rounded-xl hover:border-primary hover:text-primary text-gray-600 transition-all font-medium text-left shadow-sm group">
                                            <span className="text-gray-300 mr-2 font-mono group-hover:text-primary">1</span> Architecture for a video streaming platform
                                        </button>
                                        <button onClick={() => setGoal("I need a robust vector search implementation with LanceDB")} className="px-4 py-3 bg-white border border-gray-100 rounded-xl hover:border-primary hover:text-primary text-gray-600 transition-all font-medium text-left shadow-sm group">
                                            <span className="text-gray-300 mr-2 font-mono group-hover:text-primary">2</span> Build a vector search system
                                        </button>
                                    </div>
                                </div>
                            )}

                            {jobStatus?.logs && jobStatus.logs.length > 0 && (
                                <div className="max-w-2xl w-full flex flex-col h-full py-6">
                                    <div className="mb-4 text-[10px] font-bold text-gray-400 uppercase tracking-[0.2em]">Live Activity Thread</div>
                                    <div className="flex-1 space-y-4">
                                        {jobStatus.logs.map((log, i) => {
                                            const isLatest = i === jobStatus.logs.length - 1;
                                            const isThought = log.startsWith("Thinking:");
                                            const isAction = log.startsWith("Action");
                                            const cleanText = log
                                                .replace(/^(Thinking:|Action \d+:)/, '')
                                                .replace(/<\|channel\|>.*?<\|message\|>/g, '') // Strip meta-channel info
                                                .replace(/<\|.*?\|>/g, '') // Strip remaining meta tags
                                                .replace(/\{"query":.*?\}/g, '') // Strip tool JSON queries
                                                .replace(/commentary to=\w+/g, '') // Strip orchestration labels
                                                .trim();

                                            return (
                                                <div
                                                    key={i}
                                                    className={`p-5 rounded-2xl border transition-all duration-500 animate-in slide-in-from-bottom-2 ${isLatest
                                                        ? (isAction ? "bg-red-50 border-red-100 text-red-950 shadow-lg shadow-red-500/5 ring-2 ring-red-50" :
                                                            isThought ? "bg-indigo-50/30 border-indigo-100/50 text-indigo-900 shadow-sm" :
                                                                "bg-white border-gray-200 text-gray-800 shadow-xl shadow-gray-200/20")
                                                        : "bg-gray-50 border-transparent text-gray-400 scale-[0.98] opacity-50"
                                                        }`}
                                                >
                                                    <div className="flex items-center gap-3 mb-3">
                                                        {isAction ? (
                                                            <div className="bg-primary/10 p-1.5 rounded-lg text-primary">
                                                                <BookOpen className="h-4 w-4" />
                                                            </div>
                                                        ) : (
                                                            <div className="bg-indigo-50 p-1.5 rounded-lg text-indigo-600">
                                                                {isLatest && !isThought ? <Loader2 className="h-4 w-4 animate-spin" /> : <Terminal className="h-4 w-4" />}
                                                            </div>
                                                        )}
                                                        <span className="text-[10px] font-black uppercase tracking-widest bg-black/5 px-2 py-0.5 rounded">
                                                            {isAction ? "Searching Catalog" : isThought ? "Reasoning" : "Activity Log"}
                                                        </span>
                                                    </div>
                                                    <div className="prose prose-sm prose-slate max-w-none prose-p:leading-relaxed prose-pre:bg-gray-800 prose-pre:text-gray-100">
                                                        <Markdown remarkPlugins={[remarkGfm]}>
                                                            {cleanText}
                                                        </Markdown>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                        <div ref={logsEndRef} />
                                    </div>

                                    {jobStatus.status === "completed" && (
                                        <div className="mt-6 flex items-center justify-center gap-3 py-3 bg-green-50 border border-green-100 rounded-2xl text-green-700 animate-in fade-in">
                                            <div className="h-2 w-2 rounded-full bg-green-500 animate-ping" />
                                            <span className="text-sm font-bold uppercase tracking-wider">Analysis Compiled Successfully</span>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>

                        {/* Search Input Box */}
                        <div className="p-6 bg-white border-t border-gray-200">
                            <form onSubmit={handleSubmit} className="max-w-3xl mx-auto relative group">
                                <div className="absolute inset-0 bg-primary/5 blur-2xl rounded-full scale-50 group-hover:scale-100 transition-all opacity-0 group-hover:opacity-100" />
                                <div className="relative flex items-center gap-3 bg-white border-2 border-gray-100 rounded-2xl p-2 shadow-2xl shadow-gray-200/50 hover:border-gray-200 focus-within:border-primary transition-all">
                                    <input
                                        type="text"
                                        className="flex-1 py-3 px-4 bg-transparent focus:outline-none text-gray-900 placeholder:text-gray-400 font-medium"
                                        placeholder="Explain your software requirement..."
                                        value={goal}
                                        onChange={(e) => setGoal(e.target.value)}
                                        disabled={isPolling}
                                    />
                                    <button
                                        type="submit"
                                        disabled={isPolling || !goal.trim()}
                                        className="bg-black text-white px-6 py-3 rounded-xl font-bold hover:bg-primary disabled:opacity-20 disabled:hover:bg-black flex items-center gap-2 transition-all active:scale-95 shadow-lg shadow-black/10"
                                    >
                                        {isPolling ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
                                        Analyze
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>

                    {/* Result Column */}
                    <div className="w-[45%] flex flex-col bg-white overflow-hidden">
                        <div className="h-14 flex items-center justify-between px-6 border-b border-gray-100 shrink-0">
                            <div className="flex items-center gap-2 text-gray-800 font-bold">
                                <FileCode className="h-4 w-4 text-primary" />
                                <span>Architect's Recommendation</span>
                            </div>
                            {jobStatus?.result && (
                                <span className="text-[10px] font-bold text-gray-400">
                                    {jobStatus.result.steps_taken} STEPS • {jobStatus.result.iterations} ITERATIONS
                                </span>
                            )}
                        </div>
                        <div className="flex-1 overflow-y-auto p-10 prose prose-slate max-w-none">
                            {jobStatus?.result?.answer ? (
                                <div className="animate-in fade-in slide-in-from-right-4 duration-1000">
                                    <Markdown
                                        remarkPlugins={[remarkGfm]}
                                        components={{
                                            h1: ({ node, ...props }) => <h1 className="text-3xl font-black text-gray-900 border-b-4 border-primary/10 pb-4 mb-8" {...props} />,
                                            h2: ({ node, ...props }) => <h2 className="text-xl font-bold text-gray-800 mt-12 mb-6 flex items-center gap-2 before:content-[''] before:block before:w-1 before:h-6 before:bg-primary" {...props} />,
                                            h3: ({ node, ...props }) => <h3 className="text-lg font-bold text-gray-800" {...props} />,
                                            ul: ({ node, ...props }) => <ul className="space-y-3 list-disc pl-5" {...props} />,
                                            ol: ({ node, ...props }) => <ol className="space-y-3 list-decimal pl-5" {...props} />,
                                            li: ({ node, ...props }) => <li className="text-gray-600 leading-relaxed" {...props} />,
                                            code: ({ node, inline, className, children, ...props }: any) => {
                                                const match = /language-(\w+)/.exec(className || "");
                                                return !inline && match ? (
                                                    <div className="rounded-xl overflow-hidden my-6 shadow-sm border border-gray-100">
                                                        <div className="bg-gray-800 px-4 py-1.5 flex justify-between items-center">
                                                            <span className="text-[10px] text-gray-400 font-mono uppercase tracking-widest">{match[1]}</span>
                                                        </div>
                                                        <SyntaxHighlighter
                                                            style={vscDarkPlus as any}
                                                            language={match[1]}
                                                            PreTag="div"
                                                            customStyle={{ margin: 0, padding: '1.5rem', fontSize: '0.85rem' }}
                                                            {...props}
                                                        >
                                                            {String(children).replace(/\n$/, "")}
                                                        </SyntaxHighlighter>
                                                    </div>
                                                ) : (
                                                    <code className="bg-gray-100 text-primary px-1.5 py-0.5 rounded font-mono text-xs" {...props}>
                                                        {children}
                                                    </code>
                                                );
                                            },
                                            blockquote: ({ node, ...props }) => <blockquote className="border-l-4 border-primary bg-red-50/50 p-6 italic rounded-r-2xl" {...props} />,
                                            table: ({ node, ...props }) => (
                                                <div className="overflow-x-auto my-8 border border-gray-100 rounded-xl">
                                                    <table className="min-w-full divide-y divide-gray-200" {...props} />
                                                </div>
                                            ),
                                            thead: ({ node, ...props }) => <thead className="bg-gray-50" {...props} />,
                                            th: ({ node, ...props }) => <th className="px-6 py-3 text-left text-xs font-bold text-gray-500 uppercase tracking-wider" {...props} />,
                                            td: ({ node, ...props }) => <td className="px-6 py-4 text-sm text-gray-600 border-t border-gray-50" {...props} />,
                                        }}
                                    >
                                        {jobStatus.result.answer}
                                    </Markdown>
                                </div>
                            ) : (
                                <div className="h-full flex flex-col items-center justify-center text-gray-300 text-center space-y-4 opacity-40 grayscale">
                                    <FileCode className="h-20 w-20" />
                                    <p className="text-lg font-medium italic">Compilation waiting for agent analysis...</p>
                                </div>
                            )}
                        </div>
                        <div className="p-4 bg-gray-50 border-t border-gray-100 flex justify-between items-center px-8 shrink-0">
                            <div className="flex items-center gap-1.5">
                                <div className="h-2 w-2 rounded-full bg-primary" />
                                <span className="text-[10px] font-black uppercase text-gray-400 tracking-widest">Architect System v0.1.0</span>
                            </div>
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
