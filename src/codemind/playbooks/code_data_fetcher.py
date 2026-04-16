"""
Graphify-first code data fetching for playbook runtime.

Implements the full goal-to-context pipeline:

  Step 1 – Map Task to Code Entities
           Match goal keywords to specific Functions / Classes in the graph.

  Step 2 – Identify Relevant Cluster
           Read the `community` attribute of matched entities.
           Retrieve other nodes in the same cluster to constrain scope.

  Step 3 – Traverse Dependencies
           Expand matched entities one hop outward via CALLS + IMPORTS edges.
           Stay in-cluster where possible; flag cross-cluster edges as notable.

  Step 4 – Select Relevant Code
           Fetch lightweight signatures (name, type, file, line range,
           callers/callees count) — no full source yet; that is the agent's job.

  Step 5 – Build Minimal Context
           Assemble a structured block:
             • identified entities with signatures
             • immediate call graph (what calls what)
             • cluster scope (which community owns this code)
             • file reading order + entry points

  Step 6 – Hand to LLM
           The block is injected into the agent's system prompt as
           CODEBASE PREFLIGHT, alongside the top architecture nodes.
           The agent is instructed to use tools for the actual code reading.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from dataclasses import dataclass, field


# ── Domain keyword maps ──────────────────────────────────────────────────────
# Maps domain labels to representative terms.  Any matching term in the goal
# triggers graph entity + file searches for that domain.

_DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "auth":       ("auth", "login", "session", "jwt", "oauth", "token"),
    "permission": ("permission", "acl", "rbac", "policy", "role", "privilege", "access"),
    "crypto":     ("crypto", "encrypt", "decrypt", "hash", "cipher", "sign", "hmac"),
    "injection":  ("sql", "query", "exec", "subprocess", "eval", "inject", "shell", "command"),
    "ssrf":       ("http", "request", "fetch", "url", "webhook", "proxy", "curl"),
    "deser":      ("pickle", "deserializ", "marshal", "load", "unmarshal"),
    "migration":  ("migration", "migrate", "upgrade", "downgrade", "deprecat", "convert"),
    "java":       ("java", "spring", "maven", "gradle", "pom", "servlet", "bean"),
    "testing":    ("test", "spec", "playwright", "selenium", "cypress", "puppeteer"),
    "database":   ("database", "db", "orm", "schema", "model", "dao", "repository", "entity"),
    "api":        ("api", "endpoint", "route", "controller", "handler", "router", "middleware"),
    "config":     ("config", "setting", "env", "environment", "properties", "yaml", "toml"),
    "error":      ("error", "exception", "fault", "fail", "retry", "circuit", "fallback"),
    "perf":       ("perf", "cache", "latency", "throughput", "timeout", "pool", "async"),
    "pii":        ("pii", "personal", "sensitive", "privacy", "gdpr", "email", "ssn", "phone"),
    "payment":    ("payment", "billing", "charge", "invoice", "price", "cart", "checkout"),
    "security":   ("security", "vuln", "attack", "exploit", "sanitize", "validate", "escape"),
    "logging":    ("log", "trace", "span", "metric", "monitor", "audit", "event"),
}

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "using",
    "should", "must", "will", "would", "could", "need", "want", "give",
    "make", "have", "also", "when", "where", "what", "how", "why", "any",
    "all", "get", "set", "run", "its", "our", "code", "file", "files",
    "function", "class", "method", "module", "type", "value", "data",
})


def _extract_goal_terms(goal: str) -> list[str]:
    """Extract meaningful lowercase tokens from the goal string."""
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", goal.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _goal_domain_terms(goal: str) -> list[str]:
    """Return file/entity search terms relevant to the goal, deduplicated."""
    goal_l = goal.lower()
    matched: list[str] = []
    seen: set[str] = set()
    for _domain, terms in _DOMAIN_TERMS.items():
        for term in terms:
            if term in goal_l and term not in seen:
                matched.append(term)
                seen.add(term)
    for tok in _extract_goal_terms(goal):
        if tok not in seen and len(tok) >= 4:
            matched.append(tok)
            seen.add(tok)
    return matched[:14]


# ── Data containers ──────────────────────────────────────────────────────────

@dataclass
class GraphPrefetch:
    """Pre-fetched graph context for a single playbook execution."""

    repo_id: str
    # Step 1: matched entities (Functions/Classes matching goal keywords)
    matched_entities: list[dict] = field(default_factory=list)
    # Step 2: dominant community/cluster for matched entities
    primary_community: int | None = None
    cluster_peers: list[dict] = field(default_factory=list)   # other nodes in same cluster
    # Step 3: immediate dependency neighborhood of matched entities
    neighbors: list[dict] = field(default_factory=list)
    # Step 4: lightweight signatures for matched + neighbor entities
    signatures: list[dict] = field(default_factory=list)
    # Step 5 inputs: architecture-level data
    top_nodes: list[dict] = field(default_factory=list)
    entry_points: list[dict] = field(default_factory=list)
    # Derived file reading order
    ordered_files: list[str] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)
    # Parallel retrieval lanes
    lexical_hits: list[dict] = field(default_factory=list)
    structural_hits: list[dict] = field(default_factory=list)
    # Final fused queue with provenance + score
    ranked_candidates: list[dict] = field(default_factory=list)

    @property
    def evidence_count(self) -> int:
        return (
            len(self.matched_entities)
            + len(self.signatures)
            + len(self.neighbors)
            + len(self.top_nodes)
            + len(self.lexical_hits)
            + len(self.structural_hits)
        )


# ── Main class ───────────────────────────────────────────────────────────────

class CodeDataFetcher:
    """
    Implements the Graphify goal-to-context pipeline.

    Caller passes a goal string (the user's task) and a repo_id.
    Returns a GraphPrefetch with structured context suitable for
    injection into the agent's system prompt.
    """

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

    @staticmethod
    def _dedupe_dicts_keep_order(items: list[dict], key: str) -> list[dict]:
        """Deduplicate dict items by a key while preserving first-seen order."""
        seen: set[str] = set()
        out: list[dict] = []
        for item in items:
            val = str(item.get(key) or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            out.append(item)
        return out

    def _parallel_goal_retrieval(
        self,
        gqs,
        repo_id: str,
        goal_terms: list[str],
    ) -> tuple[list[dict], list[dict]]:
        """
        Run structural and lexical retrieval lanes in parallel.

        Structural lane:
          - symbol lookups (definitions/references by name) via graph index.
        Lexical lane:
          - file-path pattern matches for exact/substring terms.
        """
        structural_hits: list[dict] = []
        lexical_hits: list[dict] = []
        terms = [t for t in goal_terms[:10] if t]
        if not terms:
            return structural_hits, lexical_hits

        with ThreadPoolExecutor(max_workers=min(8, max(2, len(terms) * 2))) as pool:
            tasks = {}
            for term in terms:
                tasks[pool.submit(gqs.find_symbol_by_name, repo_id, term, None)] = (
                    "symbol", term
                )
                tasks[pool.submit(gqs.find_files_by_pattern, repo_id, term)] = (
                    "lexical", term
                )

            for future in as_completed(tasks):
                lane, term = tasks[future]
                try:
                    rows = future.result() or []
                except Exception:
                    rows = []

                if lane == "symbol":
                    for row in rows[:6]:
                        fp = row.get("file_path")
                        if not fp:
                            continue
                        structural_hits.append(
                            {
                                "term": term,
                                "name": row.get("name"),
                                "type": row.get("type"),
                                "file_path": fp,
                                "start_line": row.get("start_line", 0),
                                "end_line": row.get("end_line", 0),
                            }
                        )
                else:
                    for row in rows[:8]:
                        fp = row.get("file_path")
                        if not fp:
                            continue
                        lexical_hits.append(
                            {
                                "term": term,
                                "file_path": fp,
                                "language": row.get("language", ""),
                            }
                        )

        structural_hits = self._dedupe_dicts_keep_order(structural_hits, "file_path")[:24]
        lexical_hits = self._dedupe_dicts_keep_order(lexical_hits, "file_path")[:28]
        return structural_hits, lexical_hits

    def _rank_candidates(self, prefetch: GraphPrefetch) -> list[dict]:
        """
        Fuse structural + lexical + graph signals into a ranked file queue.
        """
        score_map: dict[str, int] = {}
        reasons: dict[str, list[str]] = {}

        def add(path: str | None, weight: int, reason: str) -> None:
            if not path:
                return
            p = str(path).strip()
            if not p:
                return
            score_map[p] = score_map.get(p, 0) + weight
            reasons.setdefault(p, [])
            if reason not in reasons[p]:
                reasons[p].append(reason)

        for e in prefetch.matched_entities:
            add(e.get("file_path"), 8, "goal-entity-match")
        for s in prefetch.signatures:
            add(s.get("file_path"), 5, "signature")
        for n in prefetch.neighbors:
            add(n.get("file_path"), 3, "dependency-neighbor")
        for p in prefetch.cluster_peers:
            add(p.get("file_path"), 3, "same-community")
        for h in prefetch.structural_hits:
            add(h.get("file_path"), 4, "symbol-hit")
        for h in prefetch.lexical_hits:
            add(h.get("file_path"), 2, "lexical-hit")
        for f in prefetch.candidate_files:
            add(f, 2, "pattern-candidate")
        for ep in prefetch.entry_points:
            add(ep.get("file_path"), 2, "entry-point")
        for n in prefetch.top_nodes:
            add(n.get("file_path"), 1, "topology-hub")

        ranked = sorted(
            (
                {
                    "file_path": path,
                    "score": score,
                    "provenance": reasons.get(path, []),
                }
                for path, score in score_map.items()
            ),
            key=lambda x: (-x["score"], x["file_path"]),
        )
        return ranked[:32]

    def build_graph_prefetch(
        self,
        repo_id: str,
        goal: str = "",
        limit: int = 12,
    ) -> GraphPrefetch:
        """
        Run the full 6-step pipeline and return a GraphPrefetch.

        Gracefully degrades at each step — if the graph is empty or a step
        fails, later steps still run with whatever data is available.
        """
        gqs = self.tools.graph          # GraphQueryService
        prefetch = GraphPrefetch(repo_id=repo_id)

        # ── Architecture baseline (used by all steps) ─────────────────────
        arch = {}
        try:
            arch = gqs.get_architecture_map(repo_id, limit=limit) or {}
        except Exception:
            pass
        prefetch.top_nodes    = list(arch.get("top_nodes") or [])
        prefetch.entry_points = list(arch.get("entry_points") or [])

        # ── Step 1: Map goal → code entities ─────────────────────────────
        # Find specific Functions/Classes whose names match goal keywords.
        goal_terms = _goal_domain_terms(goal)
        try:
            if goal_terms:
                prefetch.matched_entities = gqs.find_entities_by_terms(
                    repo_id, goal_terms, max_per_term=5
                )
        except Exception:
            pass

        # ── Parallel lanes: lexical + structural retrieval ───────────────
        # These run independently from graph topology steps and broaden recall.
        try:
            structural_hits, lexical_hits = self._parallel_goal_retrieval(
                gqs, repo_id, goal_terms
            )
            prefetch.structural_hits = structural_hits
            prefetch.lexical_hits = lexical_hits
        except Exception:
            pass

        # If entity matching misses, bootstrap from structural symbol hits.
        if not prefetch.matched_entities and prefetch.structural_hits:
            prefetch.matched_entities = [
                {
                    "node_id": None,
                    "name": s.get("name"),
                    "type": s.get("type") or "Symbol",
                    "file_path": s.get("file_path"),
                    "start_line": s.get("start_line", 0),
                    "end_line": s.get("end_line", 0),
                    "community": None,
                    "degree": 0,
                }
                for s in prefetch.structural_hits[:12]
            ]

        # ── Step 2: Identify relevant cluster ────────────────────────────
        # Determine which community the matched entities belong to.
        # Use the community that appears most often among top matches.
        if prefetch.matched_entities:
            community_counts: dict[int, int] = {}
            for ent in prefetch.matched_entities[:8]:
                cid = ent.get("community")
                if cid is not None:
                    community_counts[cid] = community_counts.get(cid, 0) + 1

            if community_counts:
                prefetch.primary_community = max(
                    community_counts, key=community_counts.get
                )
                try:
                    prefetch.cluster_peers = gqs.get_nodes_in_community(
                        repo_id,
                        prefetch.primary_community,
                        entity_types=("Function", "Method", "Class", "Interface"),
                        limit=20,
                    )
                except Exception:
                    pass

        # ── Step 3: Traverse dependencies ────────────────────────────────
        # Expand matched entities one hop via CALLS + IMPORTS edges.
        entity_ids = [e["node_id"] for e in prefetch.matched_entities if e.get("node_id")]
        if entity_ids:
            try:
                prefetch.neighbors = gqs.get_immediate_neighborhood(
                    repo_id, entity_ids, edge_types=("CALLS", "IMPORTS")
                )
            except Exception:
                pass

        # ── Step 4: Select relevant code (signatures) ─────────────────────
        # Lightweight signatures for matched entities + top neighbors.
        # Ordered: matched first, then neighbors in the same cluster.
        sig_ids = list(entity_ids)
        neighbor_ids = [
            n["node_id"]
            for n in prefetch.neighbors
            if n.get("node_id")
            and (
                prefetch.primary_community is None
                or n.get("community") == prefetch.primary_community
            )
        ][:8]
        sig_ids.extend(neighbor_ids)
        if sig_ids:
            try:
                prefetch.signatures = gqs.get_entity_signatures(repo_id, sig_ids)
            except Exception:
                pass

        # ── Step 5: Build ordered file reading list ───────────────────────
        # Collect unique file paths from: matched entities → cluster peers
        # → architecture entry points → top connected nodes.
        # Files from the primary cluster come first.
        cluster_files = self._dedupe_keep_order(
            [e.get("file_path") for e in prefetch.matched_entities if e.get("file_path")]
            + [e.get("file_path") for e in prefetch.cluster_peers if e.get("file_path")]
        )
        arch_files = self._dedupe_keep_order(
            [n.get("file_path") for n in prefetch.top_nodes if n.get("file_path")]
            + [ep.get("file_path") for ep in prefetch.entry_points if ep.get("file_path")]
        )
        structural_files = self._dedupe_keep_order(
            [h.get("file_path") for h in prefetch.structural_hits if h.get("file_path")]
        )
        lexical_files = self._dedupe_keep_order(
            [h.get("file_path") for h in prefetch.lexical_hits if h.get("file_path")]
        )
        prefetch.ordered_files = self._dedupe_keep_order(
            cluster_files + structural_files + lexical_files + arch_files
        )[:28]

        # Keyword-matched candidate files (goal-domain terms → file pattern)
        candidate_files: list[str] = []
        for term in goal_terms[:8]:
            try:
                hits = gqs.find_files_by_pattern(repo_id, pattern=term) or []
                for h in hits[:5]:
                    fp = h.get("file_path")
                    if fp:
                        candidate_files.append(fp)
            except Exception:
                pass
        prefetch.candidate_files = self._dedupe_keep_order(candidate_files)[:24]
        prefetch.ranked_candidates = self._rank_candidates(prefetch)

        return prefetch

    # ── Step 6: Render for LLM ────────────────────────────────────────────

    @staticmethod
    def to_prompt_block(prefetch: GraphPrefetch) -> str:
        """
        Render the GraphPrefetch as a structured system-prompt block.

        Frames the context so the agent knows:
        - Which entities were matched to its goal
        - Which cluster/community owns that code
        - The immediate call graph from those entities
        - The suggested file reading order
        - That it MUST call tools to read actual code before concluding
        """
        lines: list[str] = []
        lines.append(
            "\n### CODEBASE PREFLIGHT  (pre-fetched — use as starting point only)\n"
            "The server has run the goal-to-context pipeline against the knowledge graph.\n"
            "This is a MAP, not a conclusion. You MUST call tools to read actual code\n"
            "before drawing any findings. Do not answer based solely on this data."
        )

        # Step 1 output: matched entities
        if prefetch.matched_entities:
            lines.append(
                f"\n#### Step 1 — Goal-matched entities "
                f"({len(prefetch.matched_entities)} found):"
            )
            for e in prefetch.matched_entities[:10]:
                community_tag = (
                    f"  cluster={e['community']}" if e.get("community") is not None else ""
                )
                lines.append(
                    f"  • [{e.get('type','?')}] **{e.get('name','?')}**"
                    f"  — {e.get('file_path','?')}"
                    f"  L{e.get('start_line',0)}–{e.get('end_line',0)}"
                    f"{community_tag}"
                )
        else:
            lines.append(
                "\n#### Step 1 — Goal-matched entities: none found by keyword.\n"
                "  Use `search_code` or `search_symbol` to find relevant entities manually."
            )

        # Step 2 output: cluster scope
        if prefetch.primary_community is not None:
            lines.append(
                f"\n#### Step 2 — Primary cluster: community {prefetch.primary_community}"
            )
            if prefetch.cluster_peers:
                lines.append(
                    f"  {len(prefetch.cluster_peers)} peers in this cluster "
                    f"(top by connectivity):"
                )
                for p in prefetch.cluster_peers[:8]:
                    lines.append(
                        f"    – [{p.get('type','?')}] {p.get('name','?')}"
                        f"  — {p.get('file_path','?')}"
                    )

        # Step 3 output: immediate neighborhood
        if prefetch.neighbors:
            in_cluster = [
                n for n in prefetch.neighbors
                if prefetch.primary_community is None
                or n.get("community") == prefetch.primary_community
            ]
            cross_cluster = [
                n for n in prefetch.neighbors
                if prefetch.primary_community is not None
                and n.get("community") != prefetch.primary_community
            ]
            lines.append(
                f"\n#### Step 3 — Dependency neighborhood "
                f"({len(in_cluster)} in-cluster, {len(cross_cluster)} cross-cluster):"
            )
            for n in (in_cluster + cross_cluster)[:10]:
                tag = " ⚠ cross-cluster" if n in cross_cluster else ""
                lines.append(
                    f"  • [{n.get('edge_type','?')}→{n.get('type','?')}]"
                    f" {n.get('name','?')}"
                    f"  — {n.get('file_path','?')}{tag}"
                )

        # Step 4 output: signatures (minimal code context)
        if prefetch.signatures:
            lines.append(
                f"\n#### Step 4 — Entity signatures (minimal context, no full source):"
            )
            for sig in prefetch.signatures[:12]:
                callers_tag = f"  ←{sig['callers']} callers" if sig.get("callers") else ""
                calls_tag = (
                    f"  calls: {', '.join(sig['calls'][:3])}"
                    if sig.get("calls") else ""
                )
                parent_tag = (
                    f"  in class {sig['parent_class']}"
                    if sig.get("parent_class") else ""
                )
                lines.append(
                    f"  • [{sig.get('type','?')}] **{sig.get('name','?')}**"
                    f"{parent_tag}"
                    f"  — {sig.get('file_path','?')}"
                    f"  L{sig.get('start_line',0)}–{sig.get('end_line',0)}"
                    f"{callers_tag}{calls_tag}"
                )

        # Architecture-level data
        if prefetch.top_nodes:
            lines.append("\n#### Architecture — highest-connectivity hubs:")
            for n in prefetch.top_nodes[:8]:
                lines.append(
                    f"  • [{n.get('type','?')}] {n.get('name','?')}"
                    f"  {n.get('connections','?')} links"
                    f"  — {n.get('file_path','?')}"
                )

        if prefetch.entry_points:
            lines.append("\n#### Architecture — entry points:")
            for ep in prefetch.entry_points[:6]:
                route = f"  {ep.get('route')}" if ep.get("route") else ""
                lines.append(
                    f"  • {ep.get('name','?')}{route}  — {ep.get('file_path','?')}"
                )

        # Suggested reading order
        if prefetch.ordered_files:
            lines.append(
                f"\n#### Step 5 — Suggested reading order "
                f"(cluster-first, then architecture hubs):"
            )
            for i, fp in enumerate(prefetch.ordered_files[:14], 1):
                lines.append(f"  {i}. {fp}")

        if prefetch.candidate_files:
            lines.append("\n#### Keyword-matched candidate files:")
            for fp in prefetch.candidate_files[:10]:
                lines.append(f"  • {fp}")

        if prefetch.structural_hits or prefetch.lexical_hits:
            lines.append(
                f"\n#### Parallel retrieval lanes:"
                f"\n  • structural/symbol hits: {len(prefetch.structural_hits)}"
                f"\n  • lexical/path hits: {len(prefetch.lexical_hits)}"
            )
            for s in prefetch.structural_hits[:6]:
                lines.append(
                    f"    - symbol[{s.get('term','?')}]: {s.get('name','?')} "
                    f"→ {s.get('file_path','?')}"
                )
            for l in prefetch.lexical_hits[:6]:
                lines.append(
                    f"    - lexical[{l.get('term','?')}]: {l.get('file_path','?')}"
                )

        if prefetch.ranked_candidates:
            lines.append("\n#### Fused ranked read queue (lexical + structural + graph):")
            for idx, cand in enumerate(prefetch.ranked_candidates[:12], 1):
                why = ", ".join(cand.get("provenance", [])[:3])
                lines.append(
                    f"  {idx}. {cand.get('file_path','?')}  "
                    f"(score={cand.get('score', 0)}; why: {why})"
                )

        lines.append(
            "\n#### MANDATORY — you MUST call tools before concluding:\n"
            "  1. `get_map(repo_id=...)` — confirm architecture and reveal additional hubs.\n"
            "  2. `get_file_outline(file_path, repo_id)` — scan structure before full read.\n"
            "  3. `read_file(file_path, repo_id)` — read the actual source for evidence.\n"
            "  4. `get_callers` / `get_callees` — trace the full call chain.\n"
            "  5. `search_code(queries=[...], repo_id=...)` — search for specific patterns.\n"
            "  The preflight above narrows your scope — tool calls provide the evidence."
        )

        return "\n".join(lines) + "\n"
