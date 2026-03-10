"""
Playbook schema for prompt-based autonomous agents.

Playbooks are now defined as:
- System prompts (define LLM behavior)
- Search strategies (how to fetch code)

The executor uses: search_codebase → LLM with prompt → output
"""

from pydantic import BaseModel, Field
from typing import Optional


class SearchStrategy(BaseModel):
    """
    Defines how a playbook searches for code.
    """
    queries: list[str] = Field(default_factory=list, description="Search queries to try")
    phases: list[dict] = Field(default_factory=list, description="Multi-phase search strategy")
    file_types: list[str] = Field(default_factory=list, description="File extensions to filter (.py, .js, etc)")
    limit: int = Field(default=10, description="Max results to return")
    mode: str = Field(default="semantic", description="Search mode: semantic or hybrid")
    graph_filters: dict = Field(default_factory=dict, description="Additional graph-based filters")
    max_context_tokens: int = Field(default=3000, description="Max tokens for LLM context (triggers map-reduce if exceeded)")
    min_score: float = Field(default=0.0, description="Minimum relevance score to include in context")
    max_batches: int = Field(default=5, description="Maximum number of batches to process in map-reduce")


class PlaybookDefinition(BaseModel):
    """
    Prompt-based playbook definition.
    
    A playbook is:
    - A system prompt that defines how the LLM should behave
    - A search strategy for fetching relevant code
    - An output schema that defines the expected JSON structure
    - Behavioral flags that control execution
    - Metadata about when to use it
    """
    name: str = Field(..., description="Unique playbook name")
    description: str = Field(..., description="What this playbook does")
    when_to_use: str = Field(..., description="When to select this playbook (intent-based)")
    
    # Versioning & metadata
    version: str = Field(default="1.0", description="Playbook version (major.minor)")
    category: str = Field(default="analysis", description="Category: analysis, generation, evaluation, exploration")
    complexity_level: str = Field(default="medium", description="Complexity: low, medium, high")
    max_iterations: int = Field(default=5, description="Max iterations for ReAct-mode playbooks")
    
    # Prompt-based architecture
    system_prompt: str = Field(..., description="System prompt that defines LLM behavior")
    search_strategy: SearchStrategy = Field(..., description="How to search for code")
    
    # Output schema (parsed from ## Output Schema section in .md)
    output_schema: dict = Field(
        default_factory=dict,
        description="Parsed output schema definition with field names, types, defaults"
    )
    output_type: str = Field(
        default="json_response",
        description="Output type: 'tool_call' (LLM invokes a tool) or 'json_response' (LLM returns JSON)"
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="Tool to invoke if output_type is 'tool_call' (e.g. 'save_catalog_entry')"
    )
    
    # Behavioral flags (parsed from ## Behavior section in .md)
    exclude_test_files: bool = Field(
        default=False,
        description="Whether to filter test files from search results"
    )
    grounding_fence: bool = Field(
        default=False,
        description="Whether to wrap context in grounding fence markers to prevent hallucination"
    )
    inject_repo_metadata: bool = Field(
        default=False,
        description="Whether to inject repo metadata (name, url, branch) into the prompt"
    )
    skip_schema_validation: bool = Field(
        default=False,
        description="Whether to skip Pydantic schema validation of LLM output (e.g. for tool_call playbooks)"
    )
    
    # Best practice fields
    examples: list[dict] = Field(
        default_factory=list,
        description="Few-shot examples. Each dict has 'input' (goal text) and 'output' (expected JSON string)"
    )
    anti_patterns: list[str] = Field(
        default_factory=list,
        description="Things the LLM should NOT do"
    )
    quality_rubric: list[dict] = Field(
        default_factory=list,
        description="Quality criteria. Each dict has 'criterion', 'weight', 'pass_condition'"
    )
    evaluation_rules: list[str] = Field(
        default_factory=list,
        description="Post-output validation rules (e.g. 'output must contain >= 3 key_components')"
    )
    dependencies: dict = Field(
        default_factory=dict,
        description="Playbook dependency declarations: 'requires' and 'feeds_into' lists"
    )
    
    # Metadata
    deterministic: bool = Field(default=False, description="Whether output is deterministic")
    default_prompt: Optional[str] = Field(None, description="Default prompt if user provides none")
    
    @property
    def behavioral_flags(self) -> dict:
        """Return behavioral flags as a dict for easy access."""
        return {
            "exclude_test_files": self.exclude_test_files,
            "grounding_fence": self.grounding_fence,
            "inject_repo_metadata": self.inject_repo_metadata,
            "skip_schema_validation": self.skip_schema_validation,
        }
    
    def __str__(self):
        return f"Playbook({self.name} v{self.version})"

    def to_prompt_description(self) -> str:
        """Format playbook description for LLM system prompt."""
        return (
            f"Playbook: {self.name}\n"
            f"Description: {self.description}\n"
            f"When to use: {self.when_to_use}"
        )

