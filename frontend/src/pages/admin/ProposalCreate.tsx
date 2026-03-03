import { useState, useEffect, useRef } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
    Sparkles,
    Loader2,
    Save,
    ArrowLeft,
    Layers,
    FileText,
    CheckCircle2,
    AlertTriangle,
    Code2,
    Database,
    Link2,
    ListChecks,
    Cpu,
    GitBranch,
    Eye,
    Pencil,
} from "lucide-react";

const API = "";

/** Reusable Markdown preview with prose styling */
function MarkdownPreview({ content, className = "" }: { content: string; className?: string }) {
    return (
        <div className={`prose prose-sm max-w-none prose-headings:text-gray-800 prose-p:text-gray-700 prose-li:text-gray-700 prose-strong:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-violet-700 prose-code:text-xs prose-pre:bg-gray-900 prose-pre:text-gray-100 prose-a:text-violet-600 ${className}`}>
            <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
        </div>
    );
}

/** Toggle button for edit/preview mode */
function FieldToggle({ editing, onToggle }: { editing: boolean; onToggle: () => void }) {
    return (
        <button
            onClick={onToggle}
            className="flex items-center gap-1 text-xs font-medium text-gray-400 hover:text-violet-600 transition-colors px-2 py-1 rounded-md hover:bg-violet-50"
            title={editing ? "Preview" : "Edit"}
        >
            {editing ? <Eye className="w-3.5 h-3.5" /> : <Pencil className="w-3.5 h-3.5" />}
            {editing ? "Preview" : "Edit"}
        </button>
    );
}

interface Requirements {
    functional_requirements: string[];
    non_functional_requirements: string[];
    api_contracts: string[];
    data_model: string;
    integration_points: string[];
    acceptance_criteria: string[];
    tech_stack_suggestion: string;
    estimated_effort: string;
    [key: string]: any;
}

export default function ProposalCreate() {
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();

    const gapName = searchParams.get("gap_name") || "";
    const gapDescription = searchParams.get("gap_description") || "";
    const architectureLayer = searchParams.get("architecture_layer") || "";
    const userQuery = searchParams.get("user_query") || "";
    const buildCostUsd = searchParams.get("build_cost_usd") || "";
    const devWeeks = searchParams.get("dev_weeks") || "";

    const isContribute = searchParams.get("contribute") === "true";
    const existingRepoId = searchParams.get("repo_id") || "";

    const [name, setName] = useState(gapName);
    const [description, setDescription] = useState(gapDescription);
    const [layer, setLayer] = useState(architectureLayer);
    const [org, setOrg] = useState("");
    const [createdBy, setCreatedBy] = useState("");
    const [requirements, setRequirements] = useState<Requirements | null>(null);
    const [isGenerating, setIsGenerating] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [savedId, setSavedId] = useState(existingRepoId);
    const [error, setError] = useState("");
    const [editingField, setEditingField] = useState<string | null>(null);
    const [editingTextFields, setEditingTextFields] = useState<Record<string, boolean>>({});
    const [gitUrl, setGitUrl] = useState("");
    const [gitBranch, setGitBranch] = useState("");

    // Auto-generate requirements on mount if we have gap data
    // useRef guard prevents double-fire under React 18 StrictMode (dev only)
    const hasRun = useRef(false);
    useEffect(() => {
        if (hasRun.current) return;
        hasRun.current = true;

        if (existingRepoId) {
            // Existing proposal (contribute or edit): load it
            loadExistingProposal();
        } else if (gapName && !requirements) {
            // New proposal: auto-generate requirements
            generateRequirements();
        }
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    async function loadExistingProposal() {
        setIsGenerating(true);
        setError("");
        try {
            // GET /catalogs/{repo_id} returns a list of content entries
            const res = await fetch(`/api/v1/catalogs/${encodeURIComponent(existingRepoId)}`);
            if (!res.ok) throw new Error("Failed to load proposal");
            const entries = await res.json();

            if (!Array.isArray(entries) || entries.length === 0) {
                throw new Error("Proposal not found");
            }

            const data = entries[0]; // First entry contains the content JSON
            setName(data.product_name || data.repo_name || gapName);
            setDescription(data.summary_high_level || data.description || gapDescription);
            setLayer(data.architecture_layer || architectureLayer);
            setOrg(data.org || "");

            // Requirements are stored on the CatalogStore row, fetch from list endpoint
            const listRes = await fetch(`/api/v1/catalogs/list`);
            if (listRes.ok) {
                const allEntries = await listRes.json();
                const match = allEntries.find((e: any) => e.repo_id === existingRepoId);
                if (match) {
                    setName(match.repo_name || data.product_name || gapName);
                }
            }

            // Try to get requirements from the content data
            if (data.requirements) {
                setRequirements(typeof data.requirements === "string" ? JSON.parse(data.requirements) : data.requirements);
            }

            setSavedId(existingRepoId);
            setSaved(true);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setIsGenerating(false);
        }
    }

    const [duplicateEntry, setDuplicateEntry] = useState<any>(null);

    async function generateRequirements() {
        setIsGenerating(true);
        setError("");
        setDuplicateEntry(null);
        try {
            // If we already have a saved proposal, use the regenerate endpoint
            if (savedId && saved) {
                const res = await fetch(`${API}/api/v1/catalogs/${encodeURIComponent(savedId)}/regenerate`, {
                    method: "POST",
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({ detail: "Regeneration failed" }));
                    throw new Error(err.detail || "Regeneration failed");
                }
                const data = await res.json();
                setRequirements(data.requirements);
                return;
            }

            // New proposal — call propose endpoint
            const body: any = {
                gap_name: name || gapName,
                gap_description: description || gapDescription,
                architecture_layer: layer || architectureLayer,
                user_query: userQuery,
                org: org || undefined,
                created_by: createdBy || undefined,
            };
            if (buildCostUsd) body.build_cost_usd = parseInt(buildCostUsd);
            if (devWeeks) body.dev_weeks = parseInt(devWeeks);

            const res = await fetch(`${API}/api/v1/catalogs/propose`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });

            if (res.status === 409) {
                const conflict = await res.json();
                setDuplicateEntry(conflict.existing_entry);
                setError(conflict.detail || "A similar component already exists.");
                return;
            }

            if (!res.ok) throw new Error(await res.text());

            const data = await res.json();
            setRequirements(data.requirements);
            setSavedId(data.repo_id);
            setSaved(true);
        } catch (err: any) {
            setError(err.message || "Failed to generate requirements");
        } finally {
            setIsGenerating(false);
        }
    }

    async function saveRequirements() {
        if (!savedId || !requirements) return;
        setIsSaving(true);
        try {
            const res = await fetch(
                `${API}/api/v1/catalogs/${encodeURIComponent(savedId)}/requirements`,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ requirements }),
                }
            );
            if (!res.ok) throw new Error(await res.text());
            setSaved(true);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setIsSaving(false);
        }
    }

    function updateRequirementList(field: string, index: number, value: string) {
        if (!requirements) return;
        const list = [...(requirements[field] as string[])];
        list[index] = value;
        setRequirements({ ...requirements, [field]: list });
        setSaved(false);
    }

    function addRequirementItem(field: string) {
        if (!requirements) return;
        const list = [...(requirements[field] as string[]), ""];
        setRequirements({ ...requirements, [field]: list });
        setSaved(false);
        setEditingField(`${field}-${list.length - 1}`);
    }

    function removeRequirementItem(field: string, index: number) {
        if (!requirements) return;
        const list = (requirements[field] as string[]).filter((_: any, i: number) => i !== index);
        setRequirements({ ...requirements, [field]: list });
        setSaved(false);
    }

    const sectionConfig = [
        { key: "functional_requirements", label: "Functional Requirements", icon: <ListChecks className="h-4 w-4" />, color: "text-blue-600 bg-blue-50" },
        { key: "non_functional_requirements", label: "Non-Functional Requirements", icon: <Cpu className="h-4 w-4" />, color: "text-purple-600 bg-purple-50" },
        { key: "api_contracts", label: "API Contracts", icon: <Code2 className="h-4 w-4" />, color: "text-teal-600 bg-teal-50" },
        { key: "integration_points", label: "Integration Points", icon: <Link2 className="h-4 w-4" />, color: "text-orange-600 bg-orange-50" },
        { key: "acceptance_criteria", label: "Acceptance Criteria", icon: <CheckCircle2 className="h-4 w-4" />, color: "text-emerald-600 bg-emerald-50" },
    ];
    const [contributed, setContributed] = useState(false);
    const [contribUid, setContribUid] = useState("");
    const [contribOrg, setContribOrg] = useState("");
    const [contribSaving, setContribSaving] = useState(false);

    // --- Contribute Mode: simple UID + Org form ---
    if (isContribute) {
        return (
            <div className="max-w-lg mx-auto mt-8">
                <div className="flex items-center gap-3 mb-6">
                    <button
                        onClick={() => navigate(-1)}
                        className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                    >
                        <ArrowLeft className="h-5 w-5 text-gray-500" />
                    </button>
                    <div>
                        <h1 className="text-2xl font-black text-gray-900 flex items-center gap-2">
                            <GitBranch className="h-6 w-6 text-emerald-500" />
                            Contribute to Proposal
                        </h1>
                        <p className="text-sm text-gray-500 mt-0.5">
                            Register your interest in building this component
                        </p>
                    </div>
                </div>

                {/* Gap being contributed to */}
                <div className="bg-gradient-to-br from-violet-50 to-purple-50 border border-violet-200 rounded-2xl p-5 mb-6">
                    <p className="text-xs font-bold uppercase tracking-wider text-violet-600 mb-1">Proposed Component</p>
                    <p className="text-lg font-black text-gray-900">{gapName}</p>
                </div>

                {contributed ? (
                    <div className="bg-emerald-50 border border-emerald-200 rounded-2xl p-6 text-center">
                        <CheckCircle2 className="h-10 w-10 text-emerald-500 mx-auto mb-3" />
                        <p className="font-bold text-emerald-800 text-lg">Contribution Registered!</p>
                        <p className="text-sm text-emerald-600 mt-1">
                            You've been added as a contributor for <strong>{gapName}</strong>
                        </p>
                        <button
                            onClick={() => navigate("/admin/catalogs")}
                            className="mt-4 text-sm text-violet-600 hover:text-violet-800 font-semibold hover:underline"
                        >
                            View in Catalog Registry →
                        </button>
                    </div>
                ) : (
                    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 space-y-4">
                        <div>
                            <label className="block text-xs font-bold text-gray-500 mb-1">Your UID / Name *</label>
                            <input
                                value={contribUid}
                                onChange={(e) => setContribUid(e.target.value)}
                                placeholder="e.g. john.doe or J12345"
                                className="w-full px-3 py-2.5 rounded-lg border border-gray-200 bg-gray-50 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-emerald-300 focus:border-emerald-300"
                            />
                        </div>
                        <div>
                            <label className="block text-xs font-bold text-gray-500 mb-1">Organization</label>
                            <input
                                value={contribOrg}
                                onChange={(e) => setContribOrg(e.target.value)}
                                placeholder="e.g. CT, DTI, CTO"
                                className="w-full px-3 py-2.5 rounded-lg border border-gray-200 bg-gray-50 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-300 focus:border-emerald-300"
                            />
                        </div>

                        {error && (
                            <div className="py-2 px-3 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700">
                                {error}
                            </div>
                        )}

                        <button
                            onClick={async () => {
                                if (!contribUid.trim()) {
                                    setError("UID is required");
                                    return;
                                }
                                setContribSaving(true);
                                setError("");
                                try {
                                    const res = await fetch(
                                        `/api/v1/catalogs/${encodeURIComponent(existingRepoId)}/contribute`,
                                        {
                                            method: "POST",
                                            headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({ uid: contribUid.trim(), org: contribOrg.trim() }),
                                        }
                                    );
                                    if (!res.ok) throw new Error(await res.text());
                                    setContributed(true);
                                } catch (err: any) {
                                    setError(err.message || "Failed to register contribution");
                                } finally {
                                    setContribSaving(false);
                                }
                            }}
                            disabled={contribSaving || !contribUid.trim()}
                            className="w-full py-3 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 text-white font-bold hover:from-emerald-700 hover:to-teal-700 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2 shadow-lg shadow-emerald-200 transition-all"
                        >
                            {contribSaving ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <GitBranch className="h-4 w-4" />
                            )}
                            Register as Contributor
                        </button>
                    </div>
                )}
            </div>
        );
    }

    // --- Propose Mode (original flow) ---
    return (
        <div className="max-w-4xl mx-auto">
            {/* Header */}
            <div className="flex items-center gap-3 mb-6">
                <button
                    onClick={() => navigate(-1)}
                    className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                >
                    <ArrowLeft className="h-5 w-5 text-gray-500" />
                </button>
                <div>
                    <h1 className="text-2xl font-black text-gray-900 flex items-center gap-2">
                        <Sparkles className="h-6 w-6 text-violet-500" />
                        Create Proposal
                    </h1>
                    <p className="text-sm text-gray-500 mt-0.5">
                        Auto-generate requirements for a missing component
                    </p>
                </div>
            </div>

            {/* Gap Info Card */}
            <div className="bg-gradient-to-br from-amber-50 to-orange-50 border border-amber-200 rounded-2xl p-5 mb-6">
                <div className="flex items-center gap-2 mb-3">
                    <AlertTriangle className="h-4 w-4 text-amber-600" />
                    <span className="text-xs font-bold uppercase tracking-wider text-amber-700">
                        Identified Gap
                    </span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="block text-xs font-bold text-gray-500 mb-1">Component Name</label>
                        <input
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            className="w-full px-3 py-2 rounded-lg border border-amber-200 bg-white text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-amber-300"
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-bold text-gray-500 mb-1">Architecture Layer</label>
                        <select
                            value={layer}
                            onChange={(e) => setLayer(e.target.value)}
                            className="w-full px-3 py-2 rounded-lg border border-amber-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-amber-300"
                        >
                            <option value="">Select layer</option>
                            <option value="Presentation">Presentation</option>
                            <option value="Business Logic">Business Logic</option>
                            <option value="Data & Storage">Data & Storage</option>
                            <option value="Infrastructure">Infrastructure</option>
                        </select>
                    </div>
                    <div className="md:col-span-2">
                        <label className="block text-xs font-bold text-gray-500 mb-1">Description</label>
                        <textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            rows={2}
                            className="w-full px-3 py-2 rounded-lg border border-amber-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-amber-300"
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-bold text-gray-500 mb-1">Organization</label>
                        <input
                            value={org}
                            onChange={(e) => setOrg(e.target.value)}
                            placeholder="e.g. CT, DTI, CTO"
                            className="w-full px-3 py-2 rounded-lg border border-amber-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-amber-300"
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-bold text-gray-500 mb-1">Created By</label>
                        <input
                            value={createdBy}
                            onChange={(e) => setCreatedBy(e.target.value)}
                            placeholder="Your name"
                            className="w-full px-3 py-2 rounded-lg border border-amber-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-amber-300"
                        />
                    </div>
                </div>
            </div>

            {/* Generate / Regenerate button */}
            {!requirements && !isGenerating && (
                <button
                    onClick={generateRequirements}
                    disabled={!name}
                    className="w-full py-3 rounded-xl bg-gradient-to-r from-violet-600 to-purple-600 text-white font-bold hover:from-violet-700 hover:to-purple-700 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2 shadow-lg shadow-violet-200 transition-all"
                >
                    <Sparkles className="h-5 w-5" />
                    Generate Requirements with AI
                </button>
            )}

            {/* Loading */}
            {isGenerating && (
                <div className="flex flex-col items-center justify-center py-12 bg-white rounded-2xl border border-gray-100">
                    <Loader2 className="h-8 w-8 text-violet-500 animate-spin mb-3" />
                    <p className="text-sm font-semibold text-gray-600">
                        Generating requirements for "{name}"...
                    </p>
                    <p className="text-xs text-gray-400 mt-1">This may take 15-30 seconds</p>
                </div>
            )}

            {/* Error / Duplicate Warning */}
            {error && (
                <div className={`py-3 px-4 rounded-xl text-sm mb-4 ${duplicateEntry
                    ? "bg-amber-50 border border-amber-200 text-amber-800"
                    : "bg-red-50 border border-red-200 text-red-700"
                    }`}>
                    <p className="font-semibold">{error}</p>
                    {duplicateEntry && (
                        <div className="mt-3 flex items-center justify-between bg-white/70 rounded-lg p-3">
                            <div>
                                <p className="text-xs text-gray-500">Existing component</p>
                                <p className="font-bold text-gray-800">{duplicateEntry.repo_name}</p>
                                <p className="text-xs text-gray-500 mt-0.5">
                                    Status: <span className="font-semibold capitalize">{duplicateEntry.status}</span>
                                    {" · "}Match: <span className="font-semibold">{Math.round(duplicateEntry.match_score * 100)}%</span>
                                </p>
                            </div>
                            <button
                                onClick={() => {
                                    const params = new URLSearchParams({
                                        gap_name: name,
                                        repo_id: duplicateEntry.repo_id,
                                        contribute: "true",
                                    });
                                    window.location.href = `/admin/catalogs/propose?${params.toString()}`;
                                }}
                                className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-xs font-bold hover:bg-emerald-700 transition-colors shrink-0"
                            >
                                ⚡ Contribute Instead
                            </button>
                        </div>
                    )}
                </div>
            )}

            {/* Requirements Display */}
            {requirements && !isGenerating && (
                <div className="space-y-4">
                    {/* Data Model (string field with markdown preview) */}
                    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                        <div className="px-5 py-3 border-b border-gray-50 flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <Database className="h-4 w-4 text-indigo-600" />
                                <h3 className="text-sm font-bold text-gray-800">Data Model</h3>
                            </div>
                            <FieldToggle
                                editing={editingTextFields.data_model ?? true}
                                onToggle={() => setEditingTextFields(s => ({ ...s, data_model: !(s.data_model ?? true) }))}
                            />
                        </div>
                        <div className="p-5">
                            {(editingTextFields.data_model ?? true) ? (
                                <textarea
                                    value={(() => { const v = requirements.data_model as any; if (typeof v === 'string') return v; if (Array.isArray(v)) return v.join('\n'); return v ? JSON.stringify(v) : ''; })()}
                                    onChange={(e) => {
                                        setRequirements({ ...requirements, data_model: e.target.value });
                                        setSaved(false);
                                    }}
                                    rows={5}
                                    className="w-full text-sm text-gray-700 bg-gray-50 rounded-lg p-3 border border-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-200 font-mono"
                                />
                            ) : (
                                <MarkdownPreview
                                    content={(() => { const v = requirements.data_model as any; if (typeof v === 'string') return v; if (Array.isArray(v)) return v.join('\n'); return v ? JSON.stringify(v) : '*No data model defined yet.*'; })()}
                                    className="min-h-[60px]"
                                />
                            )}
                        </div>
                    </div>

                    {/* Tech Stack (string field with markdown preview) */}
                    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                        <div className="px-5 py-3 border-b border-gray-50 flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <Layers className="h-4 w-4 text-cyan-600" />
                                <h3 className="text-sm font-bold text-gray-800">Suggested Tech Stack</h3>
                            </div>
                            <FieldToggle
                                editing={editingTextFields.tech_stack ?? true}
                                onToggle={() => setEditingTextFields(s => ({ ...s, tech_stack: !(s.tech_stack ?? true) }))}
                            />
                        </div>
                        <div className="p-5">
                            {(editingTextFields.tech_stack ?? true) ? (
                                <textarea
                                    value={(() => { const v = requirements.tech_stack_suggestion as any; if (typeof v === 'string') return v; if (Array.isArray(v)) return v.join(', '); return v ? JSON.stringify(v) : ''; })()}
                                    onChange={(e) => {
                                        setRequirements({ ...requirements, tech_stack_suggestion: e.target.value });
                                        setSaved(false);
                                    }}
                                    rows={3}
                                    className="w-full text-sm text-gray-700 bg-gray-50 rounded-lg p-3 border border-gray-200 focus:outline-none focus:ring-2 focus:ring-cyan-200 font-mono"
                                />
                            ) : (
                                <MarkdownPreview
                                    content={(() => { const v = requirements.tech_stack_suggestion as any; if (typeof v === 'string') return v; if (Array.isArray(v)) return v.join(', '); return v ? JSON.stringify(v) : '*No tech stack defined yet.*'; })()}
                                    className="min-h-[40px]"
                                />
                            )}
                        </div>
                    </div>

                    {/* List-based sections */}
                    {sectionConfig.map(({ key, label, icon, color }) => {
                        const rawItems = requirements[key];
                        // Safely coerce to string array — API may return objects
                        const items: string[] = Array.isArray(rawItems)
                            ? rawItems.map((v: any) => typeof v === 'string' ? v : (v?.text || v?.name || JSON.stringify(v)))
                            : [];
                        return (
                            <div key={key} className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                                <div className="px-5 py-3 border-b border-gray-50 flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <span className={`p-1 rounded-md ${color}`}>{icon}</span>
                                        <h3 className="text-sm font-bold text-gray-800">
                                            {label}
                                            <span className="ml-2 text-xs font-normal text-gray-400">
                                                ({items.length})
                                            </span>
                                        </h3>
                                    </div>
                                    <button
                                        onClick={() => addRequirementItem(key)}
                                        className="text-xs font-bold text-violet-600 hover:text-violet-800 px-2 py-1 rounded-md hover:bg-violet-50 transition-colors"
                                    >
                                        + Add
                                    </button>
                                </div>
                                <ul className="p-4 space-y-2">
                                    {items.map((item: string, i: number) => (
                                        <li key={i} className="flex items-start gap-2 group">
                                            <span className="text-gray-300 mt-0.5 text-xs shrink-0">
                                                {i + 1}.
                                            </span>
                                            {editingField === `${key}-${i}` ? (
                                                <input
                                                    autoFocus
                                                    value={item}
                                                    onChange={(e) => updateRequirementList(key, i, e.target.value)}
                                                    onBlur={() => setEditingField(null)}
                                                    onKeyDown={(e) => e.key === "Enter" && setEditingField(null)}
                                                    className="flex-1 text-sm text-gray-700 bg-violet-50 rounded px-2 py-1 border border-violet-200 focus:outline-none focus:ring-2 focus:ring-violet-300"
                                                />
                                            ) : (
                                                <div
                                                    className="flex-1 text-sm text-gray-700 cursor-text hover:bg-gray-50 rounded px-2 py-1 -mx-2"
                                                    onClick={() => setEditingField(`${key}-${i}`)}
                                                >
                                                    {item ? (
                                                        <MarkdownPreview content={item} className="[&>*:first-child]:mt-0 [&>*:last-child]:mb-0" />
                                                    ) : (
                                                        <span className="text-gray-300 italic">Click to edit</span>
                                                    )}
                                                </div>
                                            )}
                                            <button
                                                onClick={() => removeRequirementItem(key, i)}
                                                className="opacity-0 group-hover:opacity-100 text-xs text-red-400 hover:text-red-600 shrink-0 transition-opacity"
                                            >
                                                ×
                                            </button>
                                        </li>
                                    ))}
                                    {items.length === 0 && (
                                        <li className="text-xs text-gray-400 italic py-2">
                                            No items yet. Click "+ Add" to add one.
                                        </li>
                                    )}
                                </ul>
                            </div>
                        );
                    })}

                    {/* Estimated Effort */}
                    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                        <div className="px-5 py-3 border-b border-gray-50 flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <FileText className="h-4 w-4 text-gray-600" />
                                <h3 className="text-sm font-bold text-gray-800">Estimated Effort</h3>
                            </div>
                            <FieldToggle
                                editing={editingTextFields.effort ?? true}
                                onToggle={() => setEditingTextFields(s => ({ ...s, effort: !(s.effort ?? true) }))}
                            />
                        </div>
                        <div className="p-5">
                            {(editingTextFields.effort ?? true) ? (
                                <textarea
                                    value={requirements.estimated_effort || ""}
                                    onChange={(e) => {
                                        setRequirements({ ...requirements, estimated_effort: e.target.value });
                                        setSaved(false);
                                    }}
                                    rows={3}
                                    className="w-full text-sm text-gray-700 bg-gray-50 rounded-lg p-3 border border-gray-200 focus:outline-none focus:ring-2 focus:ring-gray-200 font-mono"
                                />
                            ) : (
                                <MarkdownPreview
                                    content={requirements.estimated_effort || "*No estimate provided.*"}
                                    className="min-h-[40px]"
                                />
                            )}
                        </div>
                    </div>

                    {/* Action Buttons */}
                    <div className="flex items-center gap-3 pt-2">
                        <button
                            onClick={saveRequirements}
                            disabled={isSaving || saved}
                            className={`flex-1 py-3 rounded-xl font-bold flex items-center justify-center gap-2 transition-all ${saved
                                ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                                : "bg-gray-900 text-white hover:bg-violet-700 shadow-lg"
                                }`}
                        >
                            {isSaving ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : saved ? (
                                <CheckCircle2 className="h-4 w-4" />
                            ) : (
                                <Save className="h-4 w-4" />
                            )}
                            {saved ? "Saved as Proposal" : "Save Changes"}
                        </button>
                        <button
                            onClick={generateRequirements}
                            disabled={isGenerating}
                            className="px-5 py-3 rounded-xl border border-gray-200 text-sm font-bold text-gray-600 hover:bg-gray-50 flex items-center gap-2 transition-all"
                        >
                            <Sparkles className="h-4 w-4 text-violet-500" />
                            Regenerate
                        </button>
                    </div>

                    {/* Git Contribution (for contribute mode) */}
                    {isContribute && (
                        <div className="bg-gradient-to-br from-emerald-50 to-teal-50 border border-emerald-200 rounded-2xl p-5">
                            <div className="flex items-center gap-2 mb-3">
                                <GitBranch className="h-4 w-4 text-emerald-600" />
                                <span className="text-xs font-bold uppercase tracking-wider text-emerald-700">
                                    Link Your Repository
                                </span>
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-xs font-bold text-gray-500 mb-1">Git URL</label>
                                    <input
                                        value={gitUrl}
                                        onChange={(e) => setGitUrl(e.target.value)}
                                        placeholder="https://github.com/org/repo"
                                        className="w-full px-3 py-2 rounded-lg border border-emerald-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-emerald-300"
                                    />
                                </div>
                                <div>
                                    <label className="block text-xs font-bold text-gray-500 mb-1">Branch</label>
                                    <input
                                        value={gitBranch}
                                        onChange={(e) => setGitBranch(e.target.value)}
                                        placeholder="main"
                                        className="w-full px-3 py-2 rounded-lg border border-emerald-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-emerald-300"
                                    />
                                </div>
                            </div>
                            {gitUrl && (
                                <button
                                    onClick={async () => {
                                        try {
                                            const res = await fetch(
                                                `/api/v1/catalogs/${encodeURIComponent(savedId)}/promote`,
                                                {
                                                    method: "PUT",
                                                    headers: { "Content-Type": "application/json" },
                                                    body: JSON.stringify({ git_url: gitUrl, git_branch: gitBranch || "main" }),
                                                }
                                            );
                                            if (!res.ok) throw new Error(await res.text());
                                            const data = await res.json();
                                            alert(`Promoted to ${data.new_status}! Quality: ${data.quality_score}/100`);
                                            navigate("/admin/catalogs");
                                        } catch (err: any) {
                                            setError(err.message);
                                        }
                                    }}
                                    className="mt-4 w-full py-2.5 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 text-white font-bold hover:from-emerald-700 hover:to-teal-700 flex items-center justify-center gap-2 shadow-lg shadow-emerald-200 transition-all"
                                >
                                    <GitBranch className="h-4 w-4" />
                                    Promote with Git Repository
                                </button>
                            )}
                        </div>
                    )}

                    {/* Navigation hint */}
                    {saved && (
                        <div className="text-center py-3">
                            <button
                                onClick={() => navigate("/admin/catalogs")}
                                className="text-sm text-violet-600 hover:text-violet-800 font-semibold hover:underline"
                            >
                                View in Catalog Registry →
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
