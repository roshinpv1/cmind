import sys
import json
from pathlib import Path

from graphify.detect import detect
from graphify.extract import extract
from graphify.semantic import extract_semantic
from graphify.build import build
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json

def generate_graph(target_dir: str | Path, deep_mode: bool = False):
    target_path = Path(target_dir).resolve()
    print(f"Starting standalone graphify extraction for {target_path}...")
    
    # 1. Detect files
    detection = detect(target_path)
    files_by_type = detection.get("files", {})
    
    code_files = files_by_type.get("code", [])
    doc_files = files_by_type.get("docs", []) + files_by_type.get("papers", []) + files_by_type.get("images", [])
    
    print(f"Detected {len(code_files)} code files, {len(doc_files)} semantic files.")
    
    extractions = []
    
    # 2. Extract AST (Structure)
    if code_files:
        print("Running AST structural extraction...")
        ast_result = extract([Path(p) for p in code_files])
        extractions.append(ast_result)
        print(f"AST extracted {len(ast_result.get('nodes', []))} nodes, {len(ast_result.get('edges', []))} edges.")
        
    # 3. Extract Semantics (LLM)
    if doc_files:
        print("Running Semantic extraction via LLM module...")
        sem_result = extract_semantic(doc_files, deep_mode=deep_mode)
        extractions.append(sem_result)
        print(f"Semantic extracted {len(sem_result.get('nodes', []))} nodes, {len(sem_result.get('edges', []))} edges.")
        
    # 4. Build Graph
    if not extractions:
        print("Nothing to process.")
        sys.exit(0)
        
    print("Building knowledge graph...")
    G = build(extractions, directed=False)
    
    if G.number_of_nodes() == 0:
        print("ERROR: Graph is empty - extraction produced no nodes.")
        sys.exit(1)
        
    # 5. Cluster & Analyze
    print("Discovering communities...")
    communities = cluster(G)
    cohesion = score_all(G, communities)
    
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)
    
    # Very basic labeling for this pass (you can do a second LLM pass later to label these nicely)
    labels = {cid: f'Community {cid}' for cid in communities}
    questions = suggest_questions(G, communities, labels)
    
    # Compile token counts
    total_input_tokens = sum(ext.get("input_tokens", 0) for ext in extractions)
    total_output_tokens = sum(ext.get("output_tokens", 0) for ext in extractions)
    tokens = {'input': total_input_tokens, 'output': total_output_tokens}
    
    # 6. Generate Outputs
    out_dir = target_path / "graphify-out"
    out_dir.mkdir(exist_ok=True)
    
    report = generate(
        G, communities, cohesion, labels, gods, surprises, 
        detection, tokens, str(target_path), suggested_questions=questions
    )
    
    (out_dir / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
    to_json(G, communities, str(out_dir / "graph.json"))
    
    print(f"Graph complete: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(communities)} communities.")
    print(f"Outputs written to: {out_dir}")

