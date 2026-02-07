"""
LangGraph-based Documentation Generator Agent.

Uses LangGraph for proper state management and workflow orchestration.
"""

from typing import TypedDict, Literal, Optional, Annotated
from langgraph.graph import StateGraph, END
import operator


class DocGenState(TypedDict):
    """State for documentation generation workflow."""
    
    # Input
    repo_id: str
    doc_type: Literal["readme", "api", "module"]
    scope: str
    include_examples: bool
    
    # Intermediate results
    structure: dict
    components: list[dict]
    features: list[dict]
    error: Optional[str]
    
    # Output
    documentation: str
    
    # Metadata
    current_step: str
    progress: Annotated[list[str], operator.add]  # Append-only list


class LangGraphDocAgent:
    """
    LangGraph-based documentation generator.
    
    Workflow:
    1. analyze_structure - Get file counts from graph
    2. identify_components - Search for main components
    3. extract_features - Semantic search for features
    4. generate_documentation - LLM generation
    """
    
    def __init__(self, search_service, graph_service, llm_client, embedder=None):
        self.search = search_service
        self.graph = graph_service
        self.llm = llm_client
        self.embedder = embedder
        
        # Build the workflow graph
        self.workflow = self._build_graph()
    
    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow."""
        
        # Create graph with state type
        graph = StateGraph(DocGenState)
        
        # Add nodes (each step in the workflow)
        graph.add_node("analyze_structure", self._analyze_structure)
        graph.add_node("identify_components", self._identify_components)
        graph.add_node("extract_features", self._extract_features)
        graph.add_node("generate_documentation", self._generate_documentation)
        graph.add_node("handle_error", self._handle_error)
        
        # Set entry point
        graph.set_entry_point("analyze_structure")
        
        # Add edges (workflow flow)
        graph.add_conditional_edges(
            "analyze_structure",
            self._check_structure_success,
            {
                "continue": "identify_components",
                "error": "handle_error"
            }
        )
        
        graph.add_conditional_edges(
            "identify_components",
            self._check_components_success,
            {
                "continue": "extract_features",
                "skip_features": "generate_documentation",
                "error": "handle_error"
            }
        )
        
        graph.add_edge("extract_features", "generate_documentation")
        graph.add_edge("generate_documentation", END)
        graph.add_edge("handle_error", END)
        
        # Compile the graph
        return graph.compile()
    
    # ============ Node Functions ============
    
    def _analyze_structure(self, state: DocGenState) -> DocGenState:
        """Node: Analyze repository structure."""
        print(f"[AGENT] Step 1: Analyzing structure for {state['repo_id']}")
        
        structure = {}
        try:
            if not self.graph:
                state["error"] = "Graph service not available"
                return state
            
            # Get file counts by type
            file_types = [
                ".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
                ".go", ".rs", ".java", ".kt", ".scala", ".cs",
                ".c", ".cpp", ".h", ".hpp",
                ".rb", ".php", ".swift", ".dart", ".ex", ".hs",
                ".html", ".css", ".scss", ".sql",
                ".md", ".json", ".yaml", ".yml", ".toml", ".xml",
                ".sh", ".dockerfile", ".tf",
                ".txt", ".rst", ".proto", ".graphql",
            ]
            for ext in file_types:
                try:
                    files = self.graph.find_files_by_pattern(state["repo_id"], file_type=ext)
                    if files:
                        structure[ext] = len(files)
                except Exception as e:
                    print(f"[AGENT] Warning: Could not count {ext} files: {e}")
            
            state["structure"] = structure
            state["current_step"] = "analyze_structure"
            state["progress"] = [f"✓ Analyzed structure: {len(structure)} file types"]
            
        except Exception as e:
            state["error"] = f"Structure analysis failed: {str(e)}"
            state["progress"] = [f"✗ Structure analysis failed"]
        
        return state
    
    def _identify_components(self, state: DocGenState) -> DocGenState:
        """Node: Identify main components."""
        print(f"[AGENT] Step 2: Identifying components")
        
        components = []
        try:
            # Search for different component types
            searches = [
                ("entry points", {"file_patterns": ["main", "app", "index", "server"]}),
                ("configuration", {"file_patterns": ["config", "settings"]}),
                ("API routes", {"file_patterns": ["api", "routes", "endpoints"]}),
            ]
            
            for name, filters in searches:
                try:
                    results = self._search_codebase(
                        query=f"main {name}",
                        repo_id=state["repo_id"],
                        filters=filters,
                        limit=5
                    )
                    
                    if results:
                        components.append({
                            "name": name,
                            "files": [r["file_path"] for r in results],
                            "count": len(results)
                        })
                except Exception as e:
                    print(f"[AGENT] Warning: Component search '{name}' failed: {e}")
            
            state["components"] = components
            state["current_step"] = "identify_components"
            state["progress"] = [f"✓ Found {len(components)} component types"]
            
        except Exception as e:
            state["error"] = f"Component identification failed: {str(e)}"
            state["progress"] = [f"✗ Component identification failed"]
        
        return state
    
    def _extract_features(self, state: DocGenState) -> DocGenState:
        """Node: Extract key features."""
        print(f"[AGENT] Step 3: Extracting features")
        
        features = []
        try:
            feature_searches = [
                "authentication and authorization",
                "database and storage",
                "API endpoints",
                "testing and validation",
            ]
            
            for search_query in feature_searches:
                try:
                    results = self._search_codebase(
                        query=search_query,
                        repo_id=state["repo_id"],
                        limit=3
                    )
                    
                    if results:
                        features.append({
                            "name": search_query,
                            "evidence": [r["file_path"] for r in results[:2]]
                        })
                except Exception as e:
                    print(f"[AGENT] Warning: Feature search '{search_query}' failed: {e}")
            
            state["features"] = features
            state["current_step"] = "extract_features"
            state["progress"] = [f"✓ Extracted {len(features)} features"]
            
        except Exception as e:
            print(f"[AGENT] Warning: Feature extraction failed: {e}")
            state["features"] = []
            state["progress"] = [f"⚠ Feature extraction had issues, continuing..."]
        
        return state
    
    async def _generate_documentation(self, state: DocGenState) -> DocGenState:
        """Node: Generate documentation using LLM."""
        print(f"[AGENT] Step 4: Generating {state['doc_type']}")
        
        try:
            # Build context prompt
            context = self._build_llm_context(state)
            
            # Generate with LLM
            documentation = await self.llm.generate(
                context,
                temperature=0.7,
                max_tokens=2000
            )
            
            state["documentation"] = documentation
            state["current_step"] = "complete"
            state["progress"] = [f"✓ Generated {state['doc_type']}"]
            
        except Exception as e:
            state["error"] = f"Documentation generation failed: {str(e)}"
            state["progress"] = [f"✗ Generation failed"]
        
        return state
    
    def _handle_error(self, state: DocGenState) -> DocGenState:
        """Node: Handle errors."""
        print(f"[AGENT] Error: {state.get('error', 'Unknown error')}")
        
        state["documentation"] = f"# Error\n\nFailed to generate documentation: {state.get('error')}"
        state["current_step"] = "failed"
        
        return state
    
    # ============ Conditional Edge Functions ============
    
    def _check_structure_success(self, state: DocGenState) -> Literal["continue", "error"]:
        """Check if structure analysis succeeded."""
        if state.get("error"):
            return "error"
        return "continue"
    
    def _check_components_success(self, state: DocGenState) -> Literal["continue", "skip_features", "error"]:
        """Check if component identification succeeded."""
        if state.get("error"):
            return "error"
        
        # If no components found, skip feature extraction
        if not state.get("components"):
            return "skip_features"
        
        return "continue"
    
    # ============ Helper Functions ============
    
    def _search_codebase(self, query: str, repo_id: str, filters: Optional[dict] = None, limit: int = 10) -> list[dict]:
        """Search codebase with hybrid search."""
        if not self.search:
            return []
        
        if not self.embedder:
            print("[AGENT] No embedder available, skipping search")
            return []
        
        # Generate query embedding
        query_embedding = self.embedder.model.encode([query])[0].tolist()
        
        results = self.search.search(query_embedding, repo_id=repo_id, limit=limit)
        
        # Apply filters with path normalization
        if filters and self.graph:
            try:
                candidate_files = self.graph.filter_by_structure(repo_id, filters)
                
                if candidate_files and results:
                    normalized = set()
                    for result in results:
                        lance_path = result['file_path']
                        for candidate in candidate_files:
                            if lance_path.endswith(candidate):
                                normalized.add(lance_path)
                                break
                    
                    results = [r for r in results if r['file_path'] in normalized]
            except Exception as e:
                print(f"[AGENT] Filter error: {e}")
        
        return results
    
    def _build_llm_context(self, state: DocGenState) -> str:
        """Build context for LLM generation."""
        
        structure_text = "\n".join([f"- {ext} files: {count}" for ext, count in state.get("structure", {}).items()])
        
        components_text = ""
        for comp in state.get("components", []):
            components_text += f"- **{comp['name']}**: {comp['count']} files\n"
            for f in comp.get('files', [])[:2]:
                components_text += f"  - `{f}`\n"
        
        features_text = ""
        for feat in state.get("features", []):
            features_text += f"- {feat['name']}\n"
            if feat.get('evidence'):
                features_text += f"  Evidence: {', '.join(feat['evidence'][:2])}\n"
        
        context = f"""
You are generating a {state['doc_type'].upper()} file for a codebase.

## Repository Structure
{structure_text or 'No structure information'}

## Main Components
{components_text or 'No components identified'}

## Features Found
{features_text or 'No features identified'}

Generate a comprehensive {state['doc_type'].upper()} that includes:
1. Project title and brief description
2. Key features (based on the analysis above)
3. Installation instructions
4. Usage examples {"(include code examples)" if state.get('include_examples') else ""}
5. Project structure overview
6. Contributing guidelines
7. License information

Write in a clear, professional style. Use proper markdown formatting.
"""
        
        return context
    
    # ============ Public API ============
    
    async def execute(
        self,
        repo_id: str,
        doc_type: Literal["readme", "api", "module"] = "readme",
        scope: str = "entire_repo",
        include_examples: bool = True,
        **kwargs
    ) -> dict:
        """
        Execute the documentation generation workflow.
        
        Returns:
            Dict with result and state information
        """
        
        # Initialize state
        initial_state: DocGenState = {
            "repo_id": repo_id,
            "doc_type": doc_type,
            "scope": scope,
            "include_examples": include_examples,
            "structure": {},
            "components": [],
            "features": [],
            "error": None,
            "documentation": "",
            "current_step": "initializing",
            "progress": []
        }
        
        # Run the workflow
        final_state = await self.workflow.ainvoke(initial_state)
        
        return {
            "documentation": final_state.get("documentation", ""),
            "progress": final_state.get("progress", []),
            "error": final_state.get("error"),
            "status": "completed" if not final_state.get("error") else "failed"
        }
