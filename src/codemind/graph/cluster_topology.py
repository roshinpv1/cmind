"""
Implements Graphify's topology-based clustering architecture.
Uses Graphify's native clustering and god_node analysis over NetworkX.
"""

from typing import Optional
from codemind.graph.graph_db import GraphifyAdapter
from codemind.graphify.cluster import cluster as graphify_cluster

class TopologyClusterer:
    """
    Extracts edges from CodeMind's Graphify DB and uses NetworkX Louvain
    community detection to partition the codebase into contextual microservices.
    """
    
    def __init__(self, db: GraphifyAdapter):
        self.db = db

    def cluster_repository(self, repo_id: str) -> dict[str, str]:
        """
        Runs community detection on a repository's graph and 
        writes the community_id back to Graphify DB nodes.
        """
        print(f"[CLUSTERING] Starting topology graph clustering for {repo_id}...")
        G = self.db.get_graph(repo_id)
        
        if len(G.nodes) == 0:
            print("[CLUSTERING] Graph is empty. No clustering required.")
            return {}

        print(f"[CLUSTERING] Using Graphify NetworkX graph with {len(G.nodes)} nodes and {len(G.edges)} edges.")
        
        # 1. Run Graphify's native clustering
        try:
            # graphify_cluster returns dict[int, list[str]] mapping cid -> list[node_id]
            communities_map = graphify_cluster(G)
        except Exception as e:
            print(f"[CLUSTERING] ⚠️ Clustering failed: {e}")
            return {}

        print(f"[CLUSTERING] Detected {len(communities_map)} topological communities.")

        # 2. Map back to database by writing property to nodes
        node_to_community = {}
        for cid_num, comm_nodes in communities_map.items():
            cid = f"cluster_{cid_num}"
            for node_id in comm_nodes:
                node_to_community[node_id] = cid
                # NetworkX nodes can be updated directly
                G.nodes[node_id]["community"] = cid_num

        # Save updated graph to persist community identifiers
        self.db.save_graph(repo_id, G)

        print("[CLUSTERING] Successfully updated Graphify JSON with community boundaries.")
        return node_to_community

    def get_god_nodes(self, repo_id: str, top_n: int = 5) -> list[dict]:
        """
        Identify 'God Nodes' (huge centralization/betweenness) which are
        critical structural pillars of the repository.
        """
        G = self.db.get_graph(repo_id)
        if len(G.nodes) == 0:
            return []
            
        # Compute in-degree centralization if directed, else use regular degree
        if G.is_directed():
            degrees = dict(G.in_degree())
        else:
            degrees = dict(G.degree())
            
        sorted_nodes = sorted(degrees.items(), key=lambda item: item[1], reverse=True)
        
        god_nodes = []
        for node_id, deg in sorted_nodes[:top_n]:
            god_nodes.append({
                "node_id": node_id,
                "in_degree": deg
            })
            
        return god_nodes
