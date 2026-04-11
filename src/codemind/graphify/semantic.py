import asyncio
import json
import logging
from pathlib import Path
import sys
import os

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from llm.factory import get_llm_client

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """\
You are a graphify extraction subagent. Read the files listed and extract a knowledge graph fragment.
Output ONLY valid JSON matching the schema below - no explanation, no markdown fences, no preamble.

Files (chunk {CHUNK_NUM} of {TOTAL_CHUNKS}):
--------------------------------------------------
{FILE_LIST}
--------------------------------------------------

Rules:
- EXTRACTED: relationship explicit in source (import, call, citation, "see §3.2")
- INFERRED: reasonable inference (shared data structure, implied dependency)
- AMBIGUOUS: uncertain - flag for review, do not omit

Code files: focus on semantic edges AST cannot find (call relationships, shared data, arch patterns).
  Do not re-extract imports - AST already has those.
Doc/paper files: extract named concepts, entities, citations. Also extract rationale — sections that explain WHY a decision was made, trade-offs chosen, or design intent. These become nodes with `rationale_for` edges pointing to the concept they explain.
Image files: use vision to understand what the image IS - do not just OCR.

{DEEP_MODE_PROMPT}

Semantic similarity: if two concepts in this chunk solve the same problem or represent the same idea without any structural link (no import, no call, no citation), add a `semantically_similar_to` edge marked INFERRED with a confidence_score reflecting how similar they are (0.6-0.95). 
Only add these when the similarity is genuinely non-obvious and cross-cutting. Do not add them for trivially similar things.

Hyperedges: if 3 or more nodes clearly participate together in a shared concept, flow, or pattern that is not captured by pairwise edges alone, add a hyperedge to a top-level `hyperedges` array. 
Use sparingly — only when the group relationship adds information beyond the pairwise edges. Maximum 3 hyperedges per chunk.

If a file has YAML frontmatter (--- ... ---), copy source_url, captured_at, author,
  contributor onto every node from that file.

confidence_score is REQUIRED on every edge - never omit it, never use 0.5 as a default:
- EXTRACTED edges: confidence_score = 1.0 always
- INFERRED edges: reason about each edge individually.
  Direct structural evidence (shared data structure, clear dependency): 0.8-0.9.
  Reasonable inference with some uncertainty: 0.6-0.7.
  Weak or speculative: 0.4-0.5. Most edges should be 0.6-0.9, not 0.5.
- AMBIGUOUS edges: 0.1-0.3

Output exactly this JSON (no other text):
{"nodes":[{"id":"filestem_entityname","label":"Human Readable Name","file_type":"code|document|paper|image","source_file":"relative/path","source_location":null,"source_url":null,"captured_at":null,"author":null,"contributor":null}],"edges":[{"source":"node_id","target":"node_id","relation":"calls|implements|references|cites|conceptually_related_to|shares_data_with|semantically_similar_to|rationale_for","confidence":"EXTRACTED|INFERRED|AMBIGUOUS","confidence_score":1.0,"source_file":"relative/path","source_location":null,"weight":1.0}],"hyperedges":[{"id":"snake_case_id","label":"Human Readable Label","nodes":["node_id1","node_id2","node_id3"],"relation":"participate_in|implement|form","confidence":"EXTRACTED|INFERRED","confidence_score":0.75,"source_file":"relative/path"}],"input_tokens":0,"output_tokens":0}
"""

DEEP_MODE_INSTRUCTION = """\
DEEP_MODE: be aggressive with INFERRED edges - indirect deps,
  shared assumptions, latent couplings. Mark uncertain ones AMBIGUOUS instead of omitting.
"""

def extract_content(file_paths: list[Path]) -> str:
    parts = []
    for f in file_paths:
        try:
            content = f.read_text(encoding="utf-8")
            parts.append(f"=== File: {f.name} ===\n{content}\n")
        except Exception as e:
            logger.warning(f"Failed to read {f}: {e}")
    return "\n".join(parts)


async def process_chunk(driver, files: list[Path], chunk_idx: int, total_chunks: int, deep_mode: bool) -> dict:
    file_list_text = extract_content(files)
    if not file_list_text.strip():
        return {"nodes": [], "edges": [], "hyperedges": []}
    
    prompt = PROMPT_TEMPLATE.format(
        CHUNK_NUM=chunk_idx + 1,
        TOTAL_CHUNKS=total_chunks,
        FILE_LIST=file_list_text,
        DEEP_MODE_PROMPT=DEEP_MODE_INSTRUCTION if deep_mode else ""
    )

    try:
        raw_response = await driver.generate(prompt)
        # Attempt to clean potential markdown fences
        clean_response = raw_response.strip()
        if clean_response.startswith('```json'):
            clean_response = clean_response[7:]
        elif clean_response.startswith('```'):
            clean_response = clean_response[3:]
        if clean_response.endswith('```'):
            clean_response = clean_response[:-3]
            
        data = json.loads(clean_response)
        return {
            "nodes": data.get("nodes", []),
            "edges": data.get("edges", []),
            "hyperedges": data.get("hyperedges", [])
        }
    except Exception as e:
        logger.error(f"Semantic extraction chunk {chunk_idx + 1} failed: {e}")
        return {"nodes": [], "edges": [], "hyperedges": []}


async def _extract_semantic_async(files: list[str], deep_mode: bool) -> dict:
    driver = get_llm_client()
    if not driver.is_available():
        print("[LLM Error] Driver not available. Make sure your local setup is running.", file=sys.stderr)
        return {"nodes": [], "edges": [], "hyperedges": []}
        
    chunk_size = 20
    chunks = [files[i:i + chunk_size] for i in range(0, len(files), chunk_size)]
    
    tasks = []
    for i, c in enumerate(chunks):
        paths = [Path(p) for p in c]
        tasks.append(process_chunk(driver, paths, i, len(chunks), deep_mode))
        
    results = await asyncio.gather(*tasks)
    
    combined = {
        "nodes": [],
        "edges": [],
        "hyperedges": []
    }
    
    # Merge and deduplicate by node id.
    seen_nodes = set()
    for r in results:
        for node in r.get("nodes", []):
            if node["id"] not in seen_nodes:
                seen_nodes.add(node["id"])
                combined["nodes"].append(node)
        combined["edges"].extend(r.get("edges", []))
        combined["hyperedges"].extend(r.get("hyperedges", []))
        
    return combined

def extract_semantic(files: list[str], deep_mode: bool = False) -> dict:
    """Entry point for blocking semantic extraction logic."""
    if not files:
        return {"nodes": [], "edges": [], "hyperedges": []}
    return asyncio.run(_extract_semantic_async(files, deep_mode))
