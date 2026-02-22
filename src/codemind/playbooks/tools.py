"""
Playbook execution tools - Universal code retrieval.

In the new prompt-based architecture, there's only ONE tool: search_codebase.
Playbooks define HOW to process the retrieved code via their system prompts.
"""

from datetime import UTC, datetime
import traceback
from codemind.storage.database import CatalogStore


class PlaybookTools:
    """
    Universal code retrieval tool for playbooks.
    
    All playbooks use search_codebase to fetch code, then the LLM processes it
    according to the playbook's system prompt.
    
    READ-ONLY operations on:
    - LanceDB (semantic search)
    - Kùzu (graph filters)
    - SQLite (catalog full content)
    """
    
    def __init__(self, lance_storage, graph_service, embedder, db=None):
        """
        Initialize playbook tools.
        
        Args:
            lance_storage: LanceDBStorage instance
            graph_service: GraphQueryService instance
            embedder: Embedder for query encoding
            db: Database instance (for SQLite access)
        """
        self.lance = lance_storage
        self.graph = graph_service
        self.embedder = embedder
        self.db = db
    
    async def search_codebase(self, params: dict) -> dict:
        """
        Universal code retrieval: semantic search + graph filters.
        
        This is the ONLY tool. All playbooks use this to fetch code.
        
        Args:
            params: {
                queries: list[str] - Search queries to try
                repo_id: str | list[str] - Repository ID or IDs
                limit: int - Max results (default: 10)
                mode: str - "semantic" or "hybrid" (default: semantic)
                file_types: list[str] - File extensions to filter (.py, .js)
                graph_filters: dict - Additional graph filters
            }
            
        Returns:
            {
                results: list[{file_path, chunk_text, score, line_start, line_end}],
                count: int,
                queries_used: list[str]
            }
        """
        try:
            repo_id = params["repo_id"]
            queries = params.get("queries", [])
            limit = params.get("limit", 10)
            mode = params.get("mode", "semantic")
            file_types = params.get("file_types", [])
            graph_filters = params.get("graph_filters", {})
            min_score = params.get("min_score", 0.0)
            
            # Fallback: if queries is empty but there's a single "query" param
            if not queries and "query" in params:
                queries = [params["query"]]
            
            # Validate
            limit = max(1, min(1000, limit))
            
            if not queries:
                return {
                    "error": "No queries provided",
                    "results": [],
                    "count": 0,
                    "queries_used": []
                }
            
            if not self.embedder:
                return {
                    "error": "No embedder available",
                    "results": [],
                    "count": 0,
                    "queries_used": []
                }
            
            # Special handle for catalog mode
            if mode == "catalog":
                final_results = await self._search_catalogs_internal(
                    queries=queries,
                    repo_id=repo_id,
                    limit=limit,
                    min_score=min_score
                )
                
                return {
                    "success": True, 
                    "results": final_results, 
                    "count": len(final_results),
                    "queries_used": queries,
                    "min_score": min_score
                }

            # Execute all queries and combine results
            all_results = []
            dedupe_set = set()
            
            for query in queries:
                # Encode query with proper task prefix
                query_emb = self.embedder.encode_query(query)
                
                # Semantic search
                results = self.lance.search(query_emb, repo_id=repo_id, limit=limit * 2, min_score=min_score)
                
                # Apply filters
                if mode == "hybrid" and (file_types or graph_filters):
                    filtered_results = []
                    
                    for r in results:
                        file_path = r.get('file_path', '')
                        
                        # File type filter
                        if file_types:
                            if not any(file_path.endswith(ft) for ft in file_types):
                                continue
                        
                        # Dedupe by file_path + chunk_text
                        dedupe_key = f"{file_path}:{r.get('chunk_text', '')[:50]}"
                        if dedupe_key not in dedupe_set:
                            filtered_results.append(r)
                            dedupe_set.add(dedupe_key)
                    
                    results = filtered_results
                else:
                    # Still dedupe
                    results_to_add = []
                    for r in results:
                        file_path = r.get('file_path', '')
                        dedupe_key = f"{file_path}:{r.get('chunk_text', '')[:50]}"
                        if dedupe_key not in dedupe_set:
                            results_to_add.append(r)
                            dedupe_set.add(dedupe_key)
                    results = results_to_add
                
                # Map _distance to score (1 - distance)
                for r in results:
                    if "_distance" in r and "score" not in r:
                        r["score"] = 1.0 - r["_distance"]
                
                all_results.extend(results)
            
            # Filter by min_score
            if min_score > 0:
                all_results = [r for r in all_results if r.get('score', 0) >= min_score]

            # Sort by score and limit
            all_results.sort(key=lambda x: x.get('score', 0), reverse=True)
            final_results = all_results[:limit]
            
            return {
                "success": True,
                "results": final_results,
                "count": len(final_results),
                "queries_used": queries,
                "min_score": min_score
            }
        
        except Exception as e:
            print(f"[TOOLS] search_codebase error: {e}")
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "results": [],
                "count": 0,
                "queries_used": queries if 'queries' in locals() else []
            }

    async def read_file(self, params: dict) -> dict:
        """Read a specific file's content (or a line range).
        
        Args:
            params: {
                repo_id: str,
                file_path: str,
                start_line: int (optional),
                end_line: int (optional)
            }
        """
        try:
            file_path = params["file_path"]
            start_line = params.get("start_line")
            end_line = params.get("end_line")

            # Find file using graph to get full path
            repo_id = params["repo_id"]
            files = self.graph.find_files_by_pattern(repo_id, pattern=file_path)
            if not files:
                return {"error": f"File not found: {file_path}", "content": ""}

            # Read from LanceDB chunks
            query_emb = self.embedder.encode_query(f"file content {file_path}")
            results = self.lance.search(query_emb, repo_id=repo_id, limit=50)

            # Filter to this file
            file_chunks = [r for r in results if file_path in r.get("file_path", "")]
            file_chunks.sort(key=lambda r: r.get("start_line", 0))

            # Apply line range
            if start_line and end_line:
                file_chunks = [
                    r for r in file_chunks
                    if r.get("start_line", 0) <= end_line and r.get("end_line", 0) >= start_line
                ]

            content = "\n".join(r.get("chunk_text", "") for r in file_chunks)
            return {
                "success": True,
                "file_path": file_path,
                "content": content,
                "chunks": len(file_chunks),
            }
        except Exception as e:
            return {"error": str(e), "content": ""}

    async def search_symbol(self, params: dict) -> dict:
        """Find a symbol (class/function) by name.
        
        Args:
            params: {
                repo_id: str,
                name: str,
                symbol_type: str (optional: "Class" or "Function")
            }
        """
        try:
            repo_id = params["repo_id"]
            name = params["name"]
            symbol_type = params.get("symbol_type")

            results = self.graph.find_symbol_by_name(repo_id, name, symbol_type)
            return {"success": True, "symbols": results, "count": len(results)}
        except Exception as e:
            return {"error": str(e), "symbols": [], "count": 0}

    async def get_callers(self, params: dict) -> dict:
        """Find all functions that call a given function.
        
        Args:
            params: {repo_id: str, function_name: str}
        """
        try:
            repo_id = params["repo_id"]
            func_name = params["function_name"]
            callers = self.graph.get_callers(repo_id, func_name)
            return {"success": True, "callers": callers, "count": len(callers)}
        except Exception as e:
            return {"error": str(e), "callers": [], "count": 0}

    async def get_callees(self, params: dict) -> dict:
        """Find all functions called by a given function.
        
        Args:
            params: {repo_id: str, function_name: str}
        """
        try:
            repo_id = params["repo_id"]
            func_name = params["function_name"]
            callees = self.graph.get_callees(repo_id, func_name)
            return {"success": True, "callees": callees, "count": len(callees)}
        except Exception as e:
            return {"error": str(e), "callees": [], "count": 0}

    async def get_dependencies(self, params: dict) -> dict:
        """Get files imported by a file, or files that import it.
        
        Args:
            params: {
                repo_id: str,
                file_path: str,
                direction: str ("imports" or "imported_by")
            }
        """
        try:
            repo_id = params["repo_id"]
            file_path = params["file_path"]
            direction = params.get("direction", "imports")

            if direction == "imported_by":
                deps = self.graph.get_dependents(repo_id, file_path)
            else:
                deps = self.graph.get_dependency_chain(repo_id, file_path)

            return {"success": True, "dependencies": deps, "count": len(deps)}
        except Exception as e:
            return {"error": str(e), "dependencies": [], "count": 0}

    async def list_files(self, params: dict) -> dict:
        """List files in a repository matching a pattern.
        
        Args:
            params: {
                repo_id: str,
                pattern: str (optional),
                file_type: str (optional, e.g. ".py")
            }
        """
        try:
            repo_id = params["repo_id"]
            pattern = params.get("pattern")
            file_type = params.get("file_type")

            files = self.graph.find_files_by_pattern(repo_id, pattern=pattern, file_type=file_type)
            return {"success": True, "files": files, "count": len(files)}
        except Exception as e:
            return {"error": str(e), "files": [], "count": 0}

    async def _search_catalogs_internal(
        self, 
        queries: list[str], 
        repo_id: str | list[str] | None = None, 
        limit: int = 10,
        min_score: float = 0.0
    ) -> list[dict]:
        """
        Internal high-quality catalog search:
        1. Process all queries
        2. Maximize score per repository
        3. Fetch full context from SQLite for top N repositories
        """
        repo_candidates = {} # repo_id -> {score, chunk_item}
        
        for query in queries:
            q_emb = self.embedder.encode_query(query)
            # Use a high enough candidate limit to ensure we find diverse results
            candidate_chunks = self.lance.search_catalogs(
                q_emb, 
                repo_id=repo_id, 
                limit=limit * 5, 
                columns=["repo_id", "repo_name", "chunk_text", "metadata"]
            )
            
            for item in candidate_chunks:
                rid = item['repo_id']
                score = max(0.0, 1.0 - item.get("_distance", 1.0))
                
                if score < min_score:
                    continue
                    
                if rid not in repo_candidates or score > repo_candidates[rid]['score']:
                    repo_candidates[rid] = {
                        "score": score,
                        "item": item
                    }

        # Sort and take top N
        sorted_repos = sorted(
            repo_candidates.items(), 
            key=lambda x: x[1]['score'], 
            reverse=True
        )
        final_repos = sorted_repos[:limit]
        
        results = []
        for rid, candidate in final_repos:
            score = candidate['score']
            item = candidate['item']
            
            # Fetch FULL content from SQLite
            full_content = ""
            metadata = {}
            if self.db:
                try:
                    with self.db.get_session() as session:
                        cat_entry = session.query(CatalogStore).filter_by(repo_id=rid).first()
                        if cat_entry:
                            full_content = cat_entry.content
                            metadata = cat_entry.metadata_json or {}
                        else:
                            full_content = item.get("chunk_text") or item.get("result", "")
                except Exception as e:
                    print(f"[ERROR] SQLite fetch failed for {rid}: {e}")
                    full_content = item.get("chunk_text") or item.get("result", "")
            else:
                full_content = item.get("chunk_text") or item.get("result", "")

            import json
            try:
                content_obj = json.loads(full_content)
            except:
                content_obj = {}

            # Build rich text with all fields explicitly surfaced
            repo_name = content_obj.get("repo_name", metadata.get("repo_name", item.get("repo_name", rid)))
            parts = [
                f"CATALOG ENTRY: {repo_name}",
                f"Relevance Score: {score:.2f}",
            ]

            # Identity & Metadata
            if metadata.get("repo_url"): parts.append(f"Repository URL: {metadata['repo_url']}")
            if metadata.get("branch"): parts.append(f"Branch: {metadata['branch']}")
            if metadata.get("category"): parts.append(f"Category: {metadata['category']}")

            # Summaries
            desc = content_obj.get("description", "")
            if desc: parts.append(f"Description: {desc}")
            high_level = metadata.get("summary_high_level", "")
            if high_level: parts.append(f"High-Level Summary: {high_level}")
            detailed = content_obj.get("summary_detailed", "")
            if detailed: parts.append(f"Detailed Summary: {detailed}")

            # Tech Detail
            if metadata.get("architecture"): parts.append(f"Architecture: {metadata['architecture']}")
            if metadata.get("tech_stack"): parts.append(f"Tech Stack: {metadata['tech_stack']}")
            if metadata.get("specification"): parts.append(f"Specification: {metadata['specification']}")
            
            topics = metadata.get("topics", [])
            if topics: parts.append(f"Topics: {', '.join(topics)}")

            # Quality
            quality = metadata.get("quality_score", 0)
            if quality: parts.append(f"Quality Score: {quality}/100")
            pros = metadata.get("pros", [])
            if pros: parts.append(f"Pros: {'; '.join(pros)}")
            cons = metadata.get("cons", [])
            if cons: parts.append(f"Cons: {'; '.join(cons)}")

            rich_text = "\n".join(parts)

            results.append({
                "file_path": f"catalog://{rid}",
                "chunk_text": rich_text,
                "score": score,
                "start_line": 0,
                "end_line": 0,
                "repo_id": rid,
                "repo_name": repo_name,
                "metadata": json.dumps(metadata)
            })
            
        return results

    async def search_catalogs(self, params: dict) -> dict:
        """Search across repository catalogs.
        
        Args:
            params: {
                query: str | list[str],
                repo_id: str (optional),
                limit: int (optional),
                min_score: float (optional)
            }
        """
        try:
            query = params.get("query")
            if not query and "queries" in params:
                query = params["queries"]
            
            if not query:
                return {"error": "No query provided", "results": [], "count": 0}
            
            queries = [query] if isinstance(query, str) else query
            repo_id = params.get("repo_id")
            limit = params.get("limit", 10)
            min_score = params.get("min_score", 0.0)
            
            if not self.embedder:
                return {"error": "No embedder available", "results": [], "count": 0}
            
            results = await self._search_catalogs_internal(
                queries=queries,
                repo_id=repo_id,
                limit=limit,
                min_score=min_score
            )
            
            return {
                "success": True, 
                "results": results, 
                "count": len(results),
                "queries_used": queries
            }
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e), "results": [], "count": 0}

    @staticmethod
    def _normalize_catalog_params(params: dict) -> dict:
        """Normalize nested LLM output to flat save_catalog_entry format.
        
        The LLM may output nested structures like:
            {name, url, purpose: {short_summary, detailed_explanation},
             architecture: {layers, design_patterns, data_flow},
             tech_stack: {backend_languages, frameworks, ...},
             quality_assessment: {score, pros, cons},
             specification: {api_base_path, endpoints, models}}
             
        This normalizes to flat format:
            {repo_name, repo_url, description, summary_detailed,
             architecture (str), tech_stack (str), quality_score (int),
             pros (list), cons (list), specification (str), topics (list)}
        """
        import json
        
        normalized = dict(params)  # shallow copy
        
        # Unwrap catalog_entry wrapper — LLM often nests all fields here
        catalog_entry = normalized.pop("catalog_entry", None)
        if isinstance(catalog_entry, dict):
            # Merge catalog_entry fields into top level (don't overwrite existing top-level keys)
            for k, v in catalog_entry.items():
                if k not in normalized or normalized[k] is None or normalized[k] == "" or normalized[k] == []:
                    normalized[k] = v
        
        # Unwrap identity wrapper — LLM sometimes nests name/url/branch here
        identity = normalized.pop("identity", None)
        if isinstance(identity, dict):
            if "name" in identity and "repo_name" not in normalized:
                normalized["repo_name"] = identity["name"]
            if "url" in identity and "repo_url" not in normalized:
                normalized["repo_url"] = identity["url"]
            if "branch" in identity and "branch" not in normalized:
                normalized["branch"] = identity["branch"]
        
        # name → repo_name
        if "name" in normalized and "repo_name" not in normalized:
            normalized["repo_name"] = normalized.pop("name")
        
        # url → repo_url 
        if "url" in normalized and "repo_url" not in normalized:
            normalized["repo_url"] = normalized.pop("url")
        
        # purpose → description + summary_detailed
        purpose = normalized.pop("purpose", None)
        if isinstance(purpose, dict):
            if "description" not in normalized:
                normalized["description"] = purpose.get("short_summary", "")
            if "summary_detailed" not in normalized:
                normalized["summary_detailed"] = purpose.get("detailed_explanation", "")
            if "summary_high_level" not in normalized:
                normalized["summary_high_level"] = purpose.get("short_summary", "")
        
        # architecture → stringify if nested
        arch = normalized.get("architecture")
        if isinstance(arch, dict):
            parts = []
            if arch.get("layers"):
                val = arch["layers"]
                parts.append("Layers: " + (", ".join(val) if isinstance(val, list) else str(val)))
            if arch.get("design_patterns"):
                val = arch["design_patterns"]
                parts.append("Patterns: " + (", ".join(val) if isinstance(val, list) else str(val)))
            if arch.get("data_flow"):
                val = arch["data_flow"]
                parts.append("Data Flow: " + (", ".join(val) if isinstance(val, list) else str(val)))
            normalized["architecture"] = "\n".join(parts) if parts else json.dumps(arch)
        
        # tech_stack → stringify if nested
        ts = normalized.get("tech_stack")
        if isinstance(ts, dict):
            all_tech = []
            for key, val in ts.items():
                if isinstance(val, list):
                    all_tech.extend(val)
                elif isinstance(val, dict):
                    for sub_key, sub_val in val.items():
                        if isinstance(sub_val, list):
                            all_tech.extend(sub_val)
                        elif isinstance(sub_val, str):
                            all_tech.append(sub_val)
                elif isinstance(val, str):
                    all_tech.append(val)
            normalized["tech_stack"] = ", ".join(all_tech) if all_tech else json.dumps(ts)
        elif isinstance(ts, list):
            normalized["tech_stack"] = ", ".join(ts)
        
        # quality_assessment → quality_score, pros, cons
        qa = normalized.pop("quality_assessment", None)
        if isinstance(qa, dict):
            if "quality_score" not in normalized:
                normalized["quality_score"] = qa.get("score", 0)
            if "pros" not in normalized and qa.get("pros"):
                normalized["pros"] = qa["pros"]
            if "cons" not in normalized and qa.get("cons"):
                normalized["cons"] = qa["cons"]
        elif isinstance(qa, (int, float)):
            # LLM sometimes returns quality_assessment as a plain number
            if "quality_score" not in normalized:
                normalized["quality_score"] = int(qa)
        
        # specification → stringify if nested
        spec = normalized.get("specification")
        if isinstance(spec, dict):
            normalized["specification"] = json.dumps(spec, indent=2)
        
        # Ensure description exists
        if "description" not in normalized:
            normalized["description"] = normalized.get("summary_detailed", normalized.get("summary_high_level", ""))
            
        # Extract estimated_cost specifically if LLM wrapped it under strange names
        if "estimated_cost" not in normalized:
            if isinstance(catalog_entry, dict) and "estimated_cost" in catalog_entry:
                normalized["estimated_cost"] = catalog_entry["estimated_cost"]
            elif "quality_assessment" in normalized and isinstance(normalized["quality_assessment"], dict):
                qb = normalized["quality_assessment"]
                if "estimated_cost" in qb:
                     normalized["estimated_cost"] = qb["estimated_cost"]
                     
        if "business_functionalities" not in normalized:
            if isinstance(catalog_entry, dict) and "business_functionalities" in catalog_entry:
                normalized["business_functionalities"] = catalog_entry["business_functionalities"]
        
        return normalized

    async def save_catalog_entry(self, params: dict) -> dict:
        """Save or update a catalog entry for a repository.
        
        Persists full content to SQLite and searchable chunks to LanceDB.
        Handles both flat and nested LLM output formats.
        """
        try:
            # Normalize nested LLM output to flat format
            params = self._normalize_catalog_params(params)
            print(f"[TOOLS] Normalized params keys: {list(params.keys())}")
            
            repo_id = params["repo_id"]
            description = params["description"]
            # Use detailed summary as the main content if available, else description
            main_content = params.get("summary_detailed", description)
            
            if not self.embedder:
                 return {"error": "No embedder available"}

            # --- 1. Construct Metadata & Content ---
            import json
            import uuid
            
            metadata_dict = {
                "architecture": params.get("architecture", ""),
                "tech_stack": params.get("tech_stack", ""),
                "topics": params.get("topics", []),
                "repo_name": params.get("repo_name", ""),
                "repo_url": params.get("repo_url", ""),
                "branch": params.get("branch", ""),
                "summary_high_level": params.get("summary_high_level", ""),
                "category": params.get("category", "Uncategorized"),
                "quality_score": params.get("quality_score", 0),
                "specification": params.get("specification", ""),
                "pros": params.get("pros", []),
                "cons": params.get("cons", []),
                "first_author": params.get("first_author", ""),
                "total_commits": params.get("total_commits", 0),
                "last_pr_title": params.get("last_pr_title", ""),
                "estimated_cost": params.get("estimated_cost", 0),
                "estimated_dev_months": params.get("estimated_dev_months", 0),
                "team_size_estimate": params.get("team_size_estimate", 0),
                "complexity_tier": params.get("complexity_tier", "medium"),
                "business_functionalities": params.get("business_functionalities", [])
            }
            
            # Full content includes everything for the LLM to read
            full_entry = {
                "description": description,
                "summary_detailed": main_content,
                **metadata_dict
            }
            full_content_str = json.dumps(full_entry, indent=2)
            
            # --- 2. Persist to SQLite (Full Content) ---
            if self.db:
                try:
                    with self.db.get_session() as session:
                        # Check existence
                        existing = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
                        if existing:
                            existing.content = full_content_str
                            existing.metadata_json = metadata_dict
                            existing.repo_name = params.get("repo_name")
                            existing.updated_at = int(datetime.now(UTC).timestamp())
                        else:
                            new_entry = CatalogStore(
                                repo_id=repo_id,
                                repo_name=params.get("repo_name"),
                                content=full_content_str,
                                metadata_json=metadata_dict,
                                created_at=int(datetime.now(UTC).timestamp()),
                                updated_at=int(datetime.now(UTC).timestamp())
                            )
                            session.add(new_entry)
                        session.commit()
                        print(f"[TOOLS] Saved full catalog entry to SQLite for {repo_id}")
                except Exception as e:
                    print(f"[TOOLS] SQLite save failed: {e}")
                    # Continue to LanceDB? Yes, partial success is better than fail.
            
            # --- 3. Chunk & Embed for LanceDB ---
            # Chunking strategy: 
            # 1. Metadata chunk (high priority)
            # 2. Description chunks (sliding window)
            
            chunks = []
            
            # Chunk 1: Metadata + High Level Summary
            meta_text = (
                f"Repo: {params.get('repo_name', repo_id)}\n"
                f"Topics: {', '.join(metadata_dict['topics'])}\n"
                f"Stack: {metadata_dict['tech_stack']}\n"
                f"Summary: {metadata_dict['summary_high_level']}\n"
                f"Category: {metadata_dict['category']}"
            )
            chunks.append(meta_text)
            
            # Chunk 2+: Split main content into ~1000 char chunks with overlap
            # Simple text splitter
            text_to_split = main_content
            chunk_size = 1000
            overlap = 200
            
            start = 0
            while start < len(text_to_split):
                end = start + chunk_size
                chunk_text = text_to_split[start:end]
                chunks.append(chunk_text)
                start += (chunk_size - overlap)
                
            # Embed all chunks
            embeddings = self.embedder.provider.encode_batch(chunks)
            
            # Prepare LanceDB rows
            lance_rows = []
            for i, (txt, emb) in enumerate(zip(chunks, embeddings)):
                lance_rows.append({
                    "catalog_id": str(uuid.uuid4()),
                    "chunk_id": f"{repo_id}_chunk_{i}",
                    "repo_id": repo_id,
                    "repo_name": params.get("repo_name", repo_id),
                    "chunk_text": txt,
                    "metadata": json.dumps(metadata_dict), # Store metadata in every chunk for filtering
                    "created_at": datetime.now(UTC),
                    "embedding": emb.tolist() if hasattr(emb, "tolist") else emb
                })
                
            self.lance.store_catalog_chunks(lance_rows)
            
            return {"success": True, "message": f"Catalog entry saved for {repo_id} (SQLite + {len(lance_rows)} chunks)"}
            
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    async def execute_tool(self, tool_name: str, params: dict) -> dict:
        """Dispatch a tool call by name.
        
        Args:
            tool_name: One of the registered tool names
            params: Tool-specific parameters
            
        Returns:
            Tool result dict
        """
        tools = {
            "search_codebase": self.search_codebase,
            "read_file": self.read_file,
            "search_symbol": self.search_symbol,
            "get_callers": self.get_callers,
            "get_callees": self.get_callees,
            "get_dependencies": self.get_dependencies,
            "list_files": self.list_files,
            "search_catalogs": self.search_catalogs,
            "save_catalog_entry": self.save_catalog_entry,
        }

        if tool_name not in tools:
            return {"error": f"Unknown tool: {tool_name}. Available: {list(tools.keys())}"}

        return await tools[tool_name](params)

    @staticmethod
    def get_tool_descriptions() -> list[dict]:
        """Return tool descriptions for LLM prompting."""
        return [
            {
                "name": "search_codebase",
                "description": "Semantic search across the codebase. Best for finding code related to a concept or feature.",
                "parameters": "queries (list[str]), repo_id (str), limit (int), mode ('semantic'|'hybrid'), file_types (list[str])"
            },
            {
                "name": "read_file",
                "description": "Read content of a specific file. Use when you know the file path.",
                "parameters": "file_path (str), repo_id (str), start_line (int, optional), end_line (int, optional)"
            },
            {
                "name": "search_symbol",
                "description": "Find a class or function by name. Returns file locations.",
                "parameters": "name (str), repo_id (str), symbol_type ('Class'|'Function', optional)"
            },
            {
                "name": "get_callers",
                "description": "Find all functions that call a given function. Shows who depends on it.",
                "parameters": "function_name (str), repo_id (str)"
            },
            {
                "name": "get_callees",
                "description": "Find all functions called by a given function. Shows what it depends on.",
                "parameters": "function_name (str), repo_id (str)"
            },
            {
                "name": "get_dependencies",
                "description": "Get file-level import dependencies. direction='imports' for what this file uses, 'imported_by' for what uses this file.",
                "parameters": "file_path (str), repo_id (str), direction ('imports'|'imported_by')"
            },
            {
                "name": "list_files",
                "description": "List files in the repository matching a pattern or file type.",
                "parameters": "repo_id (str), pattern (str, optional), file_type (str, optional)"
            },
            {
                "name": "search_catalogs",
                "description": "Search across high-level documentation catalogs. Use to find relevant repositories or architectural summaries.",
                "parameters": "query (str), repo_id (str, optional), limit (int, optional)"
            },
            {
                "name": "save_catalog_entry",
                "description": "Save a comprehensive catalog entry documenting a repository's purpose, architecture, quality, and metadata.",
                "parameters": "repo_id (str), repo_name (str), repo_url (str), branch (str), description (str), summary_high_level (str), summary_detailed (str), category (str), quality_score (int 1-100), architecture (str), tech_stack (str), specification (str), topics (list[str]), pros (list[str]), cons (list[str])"
            },
        ]
