import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
    Save, Eye, Plus, Trash2, ArrowLeft,
    Brain, Code2, Compass, BarChart3, Wrench, Scale, Layers,
    Package, Search, Sparkles, BookOpen, Zap, Globe, Check
} from "lucide-react";

const ICON_LIST = [
    { name: "Brain", comp: Brain },
    { name: "Code2", comp: Code2 },
    { name: "Compass", comp: Compass },
    { name: "BarChart3", comp: BarChart3 },
    { name: "Wrench", comp: Wrench },
    { name: "Scale", comp: Scale },
    { name: "Layers", comp: Layers },
    { name: "Package", comp: Package },
    { name: "Search", comp: Search },
    { name: "Sparkles", comp: Sparkles },
    { name: "BookOpen", comp: BookOpen },
    { name: "Zap", comp: Zap },
    { name: "Globe", comp: Globe },
];

const COLORS = ["violet", "blue", "teal", "emerald", "amber", "rose", "indigo", "orange", "gray"];
const CATEGORIES = ["analysis", "generation", "evaluation", "exploration"];
const COMPLEXITIES = ["low", "medium", "high"];

interface PlaybookForm {
    name: string;
    description: string;
    when_to_use: string;
    category: string;
    complexity: string;
    icon: string;
    color: string;
    system_prompt: string;
    search_strategy: { mode: string; limit: number; min_score: number; queries: string[] };
    output_schema: Record<string, any>;
    behavior: { exclude_test_files: boolean; grounding_fence: boolean; inject_repo_metadata: boolean };
    anti_patterns: string[];
    templates: { label: string; prompt: string }[];
    evaluation_rules: string[];
    requires_repo: boolean;
    tags: string[];
}

const emptyForm: PlaybookForm = {
    name: "",
    description: "",
    when_to_use: "",
    category: "analysis",
    complexity: "medium",
    icon: "Brain",
    color: "violet",
    system_prompt: "You are a helpful coding assistant.\n\n### Rules\n- Be thorough and cite specific files\n- Provide structured output\n",
    search_strategy: { mode: "hybrid", limit: 100, min_score: 0.3, queries: [] },
    output_schema: {},
    behavior: { exclude_test_files: false, grounding_fence: false, inject_repo_metadata: false },
    anti_patterns: [],
    templates: [],
    evaluation_rules: [],
    requires_repo: true,
    tags: [],
};

export default function PlaybookComposer() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const [form, setForm] = useState<PlaybookForm>({ ...emptyForm });
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [activeTab, setActiveTab] = useState<"prompt" | "schema" | "guards" | "templates">("prompt");
    const [newAntiPattern, setNewAntiPattern] = useState("");
    const [newTemplate, setNewTemplate] = useState({ label: "", prompt: "" });
    const [newEvalRule, setNewEvalRule] = useState("");
    const [newTag, setNewTag] = useState("");
    const [isEditing, setIsEditing] = useState(false);
    const [playbookId, setPlaybookId] = useState<string | null>(id || null);

    // Load existing playbook for editing
    useEffect(() => {
        if (id) {
            fetch(`/api/v1/playbooks/${id}`)
                .then(r => r.json())
                .then(data => {
                    setForm({
                        name: data.name || "",
                        description: data.description || "",
                        when_to_use: data.when_to_use || "",
                        category: data.category || "analysis",
                        complexity: data.complexity || "medium",
                        icon: data.icon || "Brain",
                        color: data.color || "violet",
                        system_prompt: data.system_prompt || "",
                        search_strategy: data.search_strategy || emptyForm.search_strategy,
                        output_schema: data.output_schema || {},
                        behavior: data.behavior || emptyForm.behavior,
                        anti_patterns: data.anti_patterns || [],
                        templates: data.templates || [],
                        evaluation_rules: data.evaluation_rules || [],
                        requires_repo: data.requires_repo ?? true,
                        tags: data.tags || [],
                    });
                    setIsEditing(true);
                    setPlaybookId(id);
                })
                .catch(err => console.error("Failed to load playbook", err));
        }
    }, [id]);

    const handleSave = async () => {
        if (!form.name.trim()) return;
        setSaving(true);
        try {
            const body = {
                ...form,
                name: form.name.toLowerCase().replace(/\s+/g, "_"),
            };

            let res;
            if (isEditing && playbookId) {
                res = await fetch(`/api/v1/playbooks/${playbookId}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
            } else {
                res = await fetch("/api/v1/playbooks", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
            }

            if (res.ok) {
                const data = await res.json();
                setPlaybookId(data.id);
                setIsEditing(true);
                setSaved(true);
                setTimeout(() => setSaved(false), 3000);
            }
        } catch (err) {
            console.error("Save failed", err);
        }
        setSaving(false);
    };

    const addAntiPattern = () => {
        if (!newAntiPattern.trim()) return;
        setForm(f => ({ ...f, anti_patterns: [...f.anti_patterns, newAntiPattern.trim()] }));
        setNewAntiPattern("");
    };

    const addTemplate = () => {
        if (!newTemplate.label.trim() || !newTemplate.prompt.trim()) return;
        setForm(f => ({ ...f, templates: [...f.templates, { ...newTemplate }] }));
        setNewTemplate({ label: "", prompt: "" });
    };

    const addEvalRule = () => {
        if (!newEvalRule.trim()) return;
        setForm(f => ({ ...f, evaluation_rules: [...f.evaluation_rules, newEvalRule.trim()] }));
        setNewEvalRule("");
    };

    const addTag = () => {
        if (!newTag.trim()) return;
        setForm(f => ({ ...f, tags: [...f.tags, newTag.trim()] }));
        setNewTag("");
    };

    const iconEntry = ICON_LIST.find(i => i.name === form.icon) || ICON_LIST[0];
    const IconComp = iconEntry.comp;

    const colorGradients: Record<string, string> = {
        violet: "from-violet-500 to-violet-600",
        blue: "from-blue-500 to-blue-600",
        teal: "from-teal-500 to-teal-600",
        emerald: "from-emerald-500 to-emerald-600",
        amber: "from-amber-500 to-amber-600",
        rose: "from-rose-500 to-rose-600",
        indigo: "from-indigo-500 to-indigo-600",
        orange: "from-orange-500 to-orange-600",
        gray: "from-gray-500 to-gray-600",
    };
    const gradient = colorGradients[form.color] || colorGradients.violet;

    return (
        <div className="max-w-7xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                    <button onClick={() => navigate("/playbook-store")} className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
                        <ArrowLeft className="w-4 h-4 text-gray-500" />
                    </button>
                    <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${gradient} flex items-center justify-center text-white shadow-sm`}>
                        <IconComp className="w-5 h-5" />
                    </div>
                    <div>
                        <h1 className="text-xl font-bold text-gray-900">
                            {isEditing ? "Edit Playbook" : "Create Playbook"}
                        </h1>
                        <p className="text-xs text-gray-500">
                            {form.name ? form.name.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase()) : "Untitled playbook"}
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {saved && (
                        <span className="flex items-center gap-1 text-xs font-semibold text-emerald-600 bg-emerald-50 px-3 py-1.5 rounded-lg">
                            <Check className="w-3.5 h-3.5" />
                            Saved!
                        </span>
                    )}
                    <button
                        onClick={handleSave}
                        disabled={saving || !form.name.trim()}
                        className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-violet-600 to-indigo-600 text-white rounded-xl text-sm font-semibold hover:from-violet-700 hover:to-indigo-700 disabled:opacity-40 transition-all shadow-lg shadow-violet-200"
                    >
                        <Save className="w-4 h-4" />
                        {saving ? "Saving..." : "Save"}
                    </button>
                </div>
            </div>

            {/* Split Pane */}
            <div className="grid grid-cols-2 gap-6 min-h-[calc(100vh-12rem)]">
                {/* Left: Form Editor */}
                <div className="space-y-4 overflow-y-auto pr-2">
                    {/* Identity Section */}
                    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
                        <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-4">Identity</h2>
                        <div className="space-y-3">
                            <div>
                                <label className="text-[10px] font-bold text-gray-400 uppercase">Name</label>
                                <input
                                    value={form.name}
                                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                                    placeholder="my_custom_analyzer"
                                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mt-1 focus:outline-none focus:ring-2 focus:ring-violet-200"
                                />
                            </div>
                            <div>
                                <label className="text-[10px] font-bold text-gray-400 uppercase">Description</label>
                                <textarea
                                    value={form.description}
                                    onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                                    placeholder="What this playbook does..."
                                    rows={2}
                                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mt-1 focus:outline-none focus:ring-2 focus:ring-violet-200 resize-none"
                                />
                            </div>
                            <div className="grid grid-cols-3 gap-3">
                                <div>
                                    <label className="text-[10px] font-bold text-gray-400 uppercase">Category</label>
                                    <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
                                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mt-1">
                                        {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                                    </select>
                                </div>
                                <div>
                                    <label className="text-[10px] font-bold text-gray-400 uppercase">Complexity</label>
                                    <select value={form.complexity} onChange={e => setForm(f => ({ ...f, complexity: e.target.value }))}
                                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mt-1">
                                        {COMPLEXITIES.map(c => <option key={c} value={c}>{c}</option>)}
                                    </select>
                                </div>
                                <div>
                                    <label className="text-[10px] font-bold text-gray-400 uppercase">Color</label>
                                    <div className="flex flex-wrap gap-1 mt-1">
                                        {COLORS.map(c => (
                                            <button key={c} onClick={() => setForm(f => ({ ...f, color: c }))}
                                                className={`w-5 h-5 rounded-md bg-gradient-to-br ${colorGradients[c] || ""} ${form.color === c ? "ring-2 ring-offset-1 ring-gray-400" : ""}`}
                                            />
                                        ))}
                                    </div>
                                </div>
                            </div>
                            <div>
                                <label className="text-[10px] font-bold text-gray-400 uppercase">Icon</label>
                                <div className="flex flex-wrap gap-1.5 mt-1">
                                    {ICON_LIST.map(({ name, comp: IC }) => (
                                        <button key={name} onClick={() => setForm(f => ({ ...f, icon: name }))}
                                            className={`p-1.5 rounded-lg transition-all ${form.icon === name ? "bg-violet-100 text-violet-600 ring-1 ring-violet-300" : "text-gray-400 hover:bg-gray-50"}`}>
                                            <IC className="w-4 h-4" />
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <div className="flex items-center gap-3">
                                <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                                    <input type="checkbox" checked={form.requires_repo}
                                        onChange={e => setForm(f => ({ ...f, requires_repo: e.target.checked }))}
                                        className="rounded text-violet-600" />
                                    Requires repository
                                </label>
                            </div>
                            {/* Tags */}
                            <div>
                                <label className="text-[10px] font-bold text-gray-400 uppercase">Tags</label>
                                <div className="flex flex-wrap gap-1.5 mt-1 mb-1.5">
                                    {form.tags.map((t, i) => (
                                        <span key={i} className="flex items-center gap-1 text-[10px] px-2 py-0.5 bg-gray-100 text-gray-600 rounded-md">
                                            {t}
                                            <button onClick={() => setForm(f => ({ ...f, tags: f.tags.filter((_, j) => j !== i) }))}
                                                className="text-gray-400 hover:text-red-500">×</button>
                                        </span>
                                    ))}
                                </div>
                                <div className="flex gap-1.5">
                                    <input value={newTag} onChange={e => setNewTag(e.target.value)}
                                        onKeyDown={e => e.key === "Enter" && (e.preventDefault(), addTag())}
                                        placeholder="Add tag" className="flex-1 border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs" />
                                    <button onClick={addTag} className="px-2.5 py-1.5 text-xs font-semibold text-violet-600 hover:bg-violet-50 rounded-lg"><Plus className="w-3 h-3" /></button>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Content Tabs */}
                    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                        <div className="flex border-b border-gray-100">
                            {(["prompt", "schema", "guards", "templates"] as const).map(tab => (
                                <button key={tab} onClick={() => setActiveTab(tab)}
                                    className={`flex-1 px-4 py-2.5 text-xs font-semibold transition-all ${activeTab === tab ? "text-violet-600 bg-violet-50 border-b-2 border-violet-500" : "text-gray-500 hover:text-gray-700"}`}>
                                    {tab === "prompt" ? "System Prompt" : tab === "schema" ? "Search & Behavior" : tab === "guards" ? "Guards & Rules" : "Templates"}
                                </button>
                            ))}
                        </div>
                        <div className="p-5">
                            {/* System Prompt */}
                            {activeTab === "prompt" && (
                                <textarea
                                    value={form.system_prompt}
                                    onChange={e => setForm(f => ({ ...f, system_prompt: e.target.value }))}
                                    rows={16}
                                    className="w-full border border-gray-200 rounded-lg px-3 py-2.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-violet-200 resize-none"
                                    placeholder="You are a **Lead Analyst**. Your job is to..."
                                />
                            )}

                            {/* Search & Behavior */}
                            {activeTab === "schema" && (
                                <div className="space-y-4">
                                    <div>
                                        <label className="text-[10px] font-bold text-gray-400 uppercase">Search Mode</label>
                                        <select value={form.search_strategy.mode}
                                            onChange={e => setForm(f => ({ ...f, search_strategy: { ...f.search_strategy, mode: e.target.value } }))}
                                            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mt-1">
                                            <option value="hybrid">Hybrid (semantic + structural)</option>
                                            <option value="react">ReAct (iterative agent)</option>
                                            <option value="semantic">Semantic only</option>
                                            <option value="catalog">Catalog search</option>
                                        </select>
                                    </div>
                                    <div className="grid grid-cols-2 gap-3">
                                        <div>
                                            <label className="text-[10px] font-bold text-gray-400 uppercase">Result Limit</label>
                                            <input type="number" value={form.search_strategy.limit}
                                                onChange={e => setForm(f => ({ ...f, search_strategy: { ...f.search_strategy, limit: parseInt(e.target.value) || 100 } }))}
                                                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mt-1" />
                                        </div>
                                        <div>
                                            <label className="text-[10px] font-bold text-gray-400 uppercase">Min Score</label>
                                            <input type="number" step="0.1" value={form.search_strategy.min_score}
                                                onChange={e => setForm(f => ({ ...f, search_strategy: { ...f.search_strategy, min_score: parseFloat(e.target.value) || 0.3 } }))}
                                                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mt-1" />
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <h4 className="text-[10px] font-bold text-gray-400 uppercase">Behavior Flags</h4>
                                        {(["exclude_test_files", "grounding_fence", "inject_repo_metadata"] as const).map(flag => (
                                            <label key={flag} className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                                                <input type="checkbox"
                                                    checked={(form.behavior as any)[flag]}
                                                    onChange={e => setForm(f => ({ ...f, behavior: { ...f.behavior, [flag]: e.target.checked } }))}
                                                    className="rounded text-violet-600" />
                                                {flag.replaceAll("_", " ")}
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Guards & Rules */}
                            {activeTab === "guards" && (
                                <div className="space-y-5">
                                    <div>
                                        <h4 className="text-[10px] font-bold text-gray-400 uppercase mb-2">Anti-Patterns ({form.anti_patterns.length})</h4>
                                        <div className="space-y-1.5 mb-2">
                                            {form.anti_patterns.map((ap, i) => (
                                                <div key={i} className="flex items-start gap-2 text-xs text-red-600 bg-red-50 px-3 py-2 rounded-lg group">
                                                    <span className="shrink-0 mt-0.5">❌</span>
                                                    <span className="flex-1">{ap}</span>
                                                    <button onClick={() => setForm(f => ({ ...f, anti_patterns: f.anti_patterns.filter((_, j) => j !== i) }))}
                                                        className="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-600">
                                                        <Trash2 className="w-3 h-3" />
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                        <div className="flex gap-1.5">
                                            <input value={newAntiPattern} onChange={e => setNewAntiPattern(e.target.value)}
                                                onKeyDown={e => e.key === "Enter" && (e.preventDefault(), addAntiPattern())}
                                                placeholder="Do NOT hallucinate file paths..."
                                                className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-xs" />
                                            <button onClick={addAntiPattern}
                                                className="px-3 py-2 text-xs font-semibold text-violet-600 hover:bg-violet-50 rounded-lg">
                                                <Plus className="w-3.5 h-3.5" />
                                            </button>
                                        </div>
                                    </div>
                                    <div>
                                        <h4 className="text-[10px] font-bold text-gray-400 uppercase mb-2">Evaluation Rules ({form.evaluation_rules.length})</h4>
                                        <div className="space-y-1.5 mb-2">
                                            {form.evaluation_rules.map((rule, i) => (
                                                <div key={i} className="flex items-start gap-2 text-xs text-emerald-700 bg-emerald-50 px-3 py-2 rounded-lg group">
                                                    <span className="shrink-0 mt-0.5">✅</span>
                                                    <span className="flex-1">{rule}</span>
                                                    <button onClick={() => setForm(f => ({ ...f, evaluation_rules: f.evaluation_rules.filter((_, j) => j !== i) }))}
                                                        className="opacity-0 group-hover:opacity-100 text-emerald-400 hover:text-emerald-600">
                                                        <Trash2 className="w-3 h-3" />
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                        <div className="flex gap-1.5">
                                            <input value={newEvalRule} onChange={e => setNewEvalRule(e.target.value)}
                                                onKeyDown={e => e.key === "Enter" && (e.preventDefault(), addEvalRule())}
                                                placeholder="findings must contain >= 3 findings"
                                                className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-xs" />
                                            <button onClick={addEvalRule}
                                                className="px-3 py-2 text-xs font-semibold text-violet-600 hover:bg-violet-50 rounded-lg">
                                                <Plus className="w-3.5 h-3.5" />
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* Templates */}
                            {activeTab === "templates" && (
                                <div>
                                    <h4 className="text-[10px] font-bold text-gray-400 uppercase mb-3">Quick-Start Templates ({form.templates.length})</h4>
                                    <div className="space-y-2 mb-3">
                                        {form.templates.map((t, i) => (
                                            <div key={i} className="flex items-start gap-2 bg-gray-50 px-3 py-2.5 rounded-lg group">
                                                <Zap className="w-3 h-3 text-amber-500 mt-0.5 shrink-0" />
                                                <div className="flex-1 min-w-0">
                                                    <p className="text-xs font-semibold text-gray-700">{t.label}</p>
                                                    <p className="text-[10px] text-gray-500 truncate">{t.prompt}</p>
                                                </div>
                                                <button onClick={() => setForm(f => ({ ...f, templates: f.templates.filter((_, j) => j !== i) }))}
                                                    className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500">
                                                    <Trash2 className="w-3 h-3" />
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                    <div className="space-y-1.5 bg-gray-50 p-3 rounded-lg">
                                        <input value={newTemplate.label} onChange={e => setNewTemplate(t => ({ ...t, label: e.target.value }))}
                                            placeholder="Template label (e.g. 'Full analysis')"
                                            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-xs bg-white" />
                                        <input value={newTemplate.prompt} onChange={e => setNewTemplate(t => ({ ...t, prompt: e.target.value }))}
                                            onKeyDown={e => e.key === "Enter" && (e.preventDefault(), addTemplate())}
                                            placeholder="Template prompt (e.g. 'Analyze the architecture of this codebase')"
                                            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-xs bg-white" />
                                        <button onClick={addTemplate}
                                            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-violet-600 hover:bg-violet-100 rounded-lg transition-colors">
                                            <Plus className="w-3 h-3" />
                                            Add Template
                                        </button>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* Right: Live Preview */}
                <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden flex flex-col">
                    <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2 bg-gray-50/50">
                        <Eye className="w-4 h-4 text-gray-400" />
                        <span className="text-xs font-bold text-gray-500 uppercase tracking-wider">Live Preview</span>
                    </div>
                    <div className="flex-1 overflow-y-auto p-5">
                        <pre className="text-xs font-mono text-gray-700 whitespace-pre-wrap leading-relaxed">
                            {`---
name: ${form.name || "untitled"}
version: "1.0"
description: ${form.description || "No description"}
category: ${form.category}
complexity: ${form.complexity}
---

# Playbook: ${form.name || "untitled"}
name: ${form.name || "untitled"}
description: ${form.description || "No description"}

## Description
${form.description || "No description provided."}

## When to Use
${form.when_to_use || "Not specified."}

## System Prompt
${form.system_prompt}
${form.anti_patterns.length > 0 ? `
## Anti-Patterns
${form.anti_patterns.map(a => `- ${a}`).join("\n")}` : ""}
${form.templates.length > 0 ? `
## Templates
${form.templates.map(t => `- **${t.label}**: "${t.prompt}"`).join("\n")}` : ""}
${form.evaluation_rules.length > 0 ? `
## Evaluation
${form.evaluation_rules.map(r => `- ${r}`).join("\n")}` : ""}

## Behavior
\`\`\`yaml
exclude_test_files: ${form.behavior.exclude_test_files}
grounding_fence: ${form.behavior.grounding_fence}
inject_repo_metadata: ${form.behavior.inject_repo_metadata}
\`\`\`

## Search Strategy
\`\`\`yaml
mode: ${form.search_strategy.mode}
limit: ${form.search_strategy.limit}
min_score: ${form.search_strategy.min_score}
queries: []
\`\`\``}
                        </pre>
                    </div>
                </div>
            </div>
        </div>
    );
}
