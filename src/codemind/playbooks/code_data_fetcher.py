"""
Graphify-first code data fetching for playbook runtime.

Moves "what to fetch first" out of prompt prose and into deterministic code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GraphPrefetch:
    """Pre-fetched graph context used to steer first ReAct iterations."""

    repo_id: str
    top_nodes: list[dict]
    entry_points: list[dict]
    ordered_files: list[str]
    candidate_files: list[str]

    @property
    def evidence_count(self) -> int:
        return len(self.top_nodes) + len(self.entry_points) + len(self.candidate_files)


class CodeDataFetcher:
    """Deterministic Graphify-first retrieval strategy."""

    _AUTH_HINT_TERMS = (
        "auth",
        "login",
        "session",
        "jwt",
        "oauth",
        "permission",
        "acl",
        "rbac",
        "policy",
        "token",
    )

    def __init__(self, playbook_tools):
        self.tools = playbook_tools

    @staticmethod
    def _dedupe_keep_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for v in values:
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out

    def build_graph_prefetch(self, repo_id: str, goal: str = "", limit: int = 12) -> GraphPrefetch:
        arch = self.tools.graph.get_architecture_map(repo_id, limit=limit) or {}
        top_nodes = list(arch.get("top_nodes") or [])
        entry_points = list(arch.get("entry_points") or [])

        ordered_files = self._dedupe_keep_order(
            [n.get("file_path") for n in top_nodes if n.get("file_path")]
            + [e.get("file_path") for e in entry_points if e.get("file_path")]
        )

        # Goal-aware candidate discovery (auth/authz and similar domain probes).
        goal_l = (goal or "").lower()
        candidate_files: list[str] = []
        terms = [t for t in self._AUTH_HINT_TERMS if t in goal_l]
        if terms:
            for term in terms[:5]:
                hits = self.tools.graph.find_files_by_pattern(repo_id, pattern=term) or []
                for h in hits[:8]:
                    fp = h.get("file_path")
                    if fp:
                        candidate_files.append(fp)

        candidate_files = self._dedupe_keep_order(candidate_files)[:20]

        return GraphPrefetch(
            repo_id=repo_id,
            top_nodes=top_nodes[:limit],
            entry_points=entry_points[:8],
            ordered_files=ordered_files[:20],
            candidate_files=candidate_files,
        )

    @staticmethod
    def to_prompt_block(prefetch: GraphPrefetch) -> str:
        """Render compact preflight block for the system prompt."""
        lines: list[str] = []
        lines.append("\n### GRAPHIFY PREFLIGHT (SERVER FETCH)")
        lines.append("Use this as your initial reading order and tool-call plan.")

        if prefetch.top_nodes:
            lines.append("\nTop connected nodes:")
            for n in prefetch.top_nodes[:10]:
                lines.append(
                    f"- {n.get('name')} ({n.get('type')}) in {n.get('file_path')} [{n.get('connections')} links]"
                )

        if prefetch.entry_points:
            lines.append("\nEntry points:")
            for ep in prefetch.entry_points[:5]:
                lines.append(f"- {ep.get('name')} ({ep.get('route')}) in {ep.get('file_path')}")

        if prefetch.ordered_files:
            lines.append("\nOrdered file roadmap (open in this order):")
            for fp in prefetch.ordered_files[:12]:
                lines.append(f"- {fp}")

        if prefetch.candidate_files:
            lines.append("\nGoal-specific candidate files:")
            for fp in prefetch.candidate_files[:10]:
                lines.append(f"- {fp}")

        lines.append(
            "\nExecution hint: call get_file_outline before read_file on large modules; "
            "use trace_path/get_callers/get_callees/get_dependencies to refine order."
        )
        return "\n".join(lines) + "\n"
