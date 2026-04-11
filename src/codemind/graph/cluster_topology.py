import networkx as nx
from typing import Optional
from codemind.graph.graph_db import KuzuGraphAdapter

class TopologyClusterer:
    """
    Implements Graphify's topology-based clustering architecture.
    Extracts edges from CodeMind's Kuzu DB and uses NetworkX Louvain
    community detection to partition the codebase into contextual microservices
    or functional boundaries, without needing vector embeddings.
    """
    
    def __init__(self, db: KuzuGraphAdapter):
        self.db = db

    def cluster_repository(self, repo_id: str) -> dict[str, str]:
        """
        Runs Louvain community detection on a repository's graph and 
        writes the community_id back to Kuzu DB nodes.
        
        Returns:
            dict mapping node_id -> community_id
        """
        print(f"[CLUSTERING] Starting topology graph extraction for {repo_id}...")
        G = nx.Graph()
        
        # 1. Extract structural edges (IMPORTS, CALLS, INHERITS_FROM, IMPLEMENTS)
        # File imports
        res1 = self.db._execute(
            'MATCH (a:File)-[:IMPORTS]->(b:File) WHERE a.repo_id = $rid RETURN a.id, b.id',
            {"rid": repo_id}
        )
        for row in self.db._result_to_list(res1):
            G.add_edge(row[0], row[1], weight=1.0)
            
        # Function Calls (Map function class/file parents if we want to cluster at file level,
        # but here we cluster all nodes)
        res2 = self.db._execute(
            'MATCH (a:Function)-[:CALLS]->(b:Function) '
            'WHERE a.repo_id = $rid RETURN a.parent_class, b.parent_class, a.file_path, b.file_path',
            {"rid": repo_id}
        )
        for row in self.db._result_to_list(res2):
            # Try linking class logic or file logic depending on function parent
            src_node = f"class:{repo_id}:{row[2]}:{row[0]}" if row[0] else f"file:{repo_id}:{row[2]}"
            tgt_node = f"class:{repo_id}:{row[3]}:{row[1]}" if row[1] else f"file:{repo_id}:{row[3]}"
            G.add_edge(src_node, tgt_node, weight=2.0) # Calls carry higher weight for clustering

        # Semantic links from Vision / LLM inferences
        res3 = self.db._execute(
            'MATCH (a:File)-[r:SEMANTIC_LINK_FILE]->(b:File) '
            'WHERE a.repo_id = $rid RETURN a.id, b.id, r.confidence_score',
            {"rid": repo_id}
        )
        for row in self.db._result_to_list(res3):
            G.add_edge(row[0], row[1], weight=row[2])

        if len(G.nodes) == 0:
            print("[CLUSTERING] Graph is empty. No clustering required.")
            return {}

        print(f"[CLUSTERING] Built NetworkX graph with {len(G.nodes)} nodes and {len(G.edges)} edges.")
        
        # 2. Run Louvain community detection
        try:
            communities = nx.community.louvain_communities(G, resolution=1.0)
        except AttributeError:
            # Fallback for older networkX versions
            communities = nx.community.greedy_modularity_communities(G)

        print(f"[CLUSTERING] Detected {len(communities)} topological communities.")

        # 3. Map back to database
        node_to_community = {}
        for community_idx, comm_nodes in enumerate(communities):
            cid = f"cluster_{community_idx}"
            for node_id in comm_nodes:
                node_to_community[node_id] = cid
                
                # Determine type from ID prefix
                if node_id.startswith("file:"):
                    self.db.update_node_community("File", node_id, cid)
                elif node_id.startswith("class:"):
                    self.db.update_node_community("Class", node_id, cid)

        print("[CLUSTERING] Successfully updated Kuzu DB with community boundaries.")
        return node_to_community

    def get_god_nodes(self, repo_id: str, top_n: int = 5) -> list[dict]:
        """
        Identify 'God Nodes' (huge centralization/betweenness) which are
        critical structural pillars of the repository.
        Useful for the PlannerAgent pre-flight hook.
        """
        G = nx.DiGraph()
        
        # Pull incoming IMPORTS
        res = self.db._execute(
            'MATCH (a:File)-[:IMPORTS]->(b:File) WHERE a.repo_id = $rid RETURN a.id, b.id',
            {"rid": repo_id}
        )
        for row in self.db._result_to_list(res):
            G.add_edge(row[0], row[1])
            
        if len(G.nodes) == 0:
            return []
            
        # Compute in-degree centralization
        in_degrees = dict(G.in_degree())
        sorted_nodes = sorted(in_degrees.items(), key=lambda item: item[1], reverse=True)
        
        god_nodes = []
        for node_id, deg in sorted_nodes[:top_n]:
            god_nodes.append({
                "node_id": node_id,
                "in_degree": deg
            })
            
        return god_nodes
