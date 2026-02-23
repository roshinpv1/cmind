"""
Pydantic schemas for structured playbook output.

Used with CmindChatModel.with_structured_output() to validate
LLM responses against expected formats, replacing inline schema
hints and manual JSON parsing.
"""

from typing import Any, Optional, Union
from pydantic import BaseModel, Field, model_validator


# ─── Catalog Generator ──────────────────────────────────────────────────────

class CatalogGeneratorOutput(BaseModel):
    """Structured output for catalog_generator playbook."""
    repo_id: str = Field(description="Repository identifier")
    repo_name: str = Field(description="Human-readable project name")
    repo_url: str = Field(default="", description="Repository URL")
    branch: str = Field(default="main", description="Branch name")
    description: str = Field(description="One-line summary")
    summary_high_level: str = Field(description="2-3 sentence overview for catalog browsing")
    summary_detailed: str = Field(description="Comprehensive multi-paragraph analysis")
    category: str = Field(description="Software type (e.g. Web App, API, CLI Tool, Library)")
    quality_score: int = Field(default=50, ge=1, le=100, description="Quality score 1-100")
    architecture: str = Field(default="", description="Architecture description")
    tech_stack: str = Field(default="", description="Languages, frameworks, databases")
    specification: str = Field(default="", description="Key APIs, interfaces, protocols")
    topics: list[str] = Field(default_factory=list, description="Searchable tags")
    pros: list[str] = Field(default_factory=list, description="Strengths")
    cons: list[str] = Field(default_factory=list, description="Weaknesses")
    estimated_cost: int = Field(default=0, description="Estimated cost in USD")
    business_functionalities: list[str] = Field(default_factory=list, description="Core business capabilities")


# ─── Catalog Search ──────────────────────────────────────────────────────────

class CatalogEntry(BaseModel):
    """A catalog entry matched during search."""
    repo_name: str = Field(default="", description="Repository name")
    repo_url: str = Field(default="", description="Repository URL")
    description: str = Field(default="", description="Entry description")
    topics: list[str] = Field(default_factory=list)
    tech_stack: Any = Field(default="")
    architecture: str = Field(default="")
    category: str = Field(default="")
    quality_score: int = Field(default=0, ge=0, le=100)
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    org: str = Field(default="", description="Organization owning this component")
    branch: str = Field(default="", description="Branch name")
    estimated_cost: int = Field(default=0, description="Estimated cost in USD")

    @model_validator(mode="before")
    @classmethod
    def coerce_tech_stack(cls, values):
        ts = values.get("tech_stack")
        if isinstance(ts, list):
            values["tech_stack"] = ", ".join(str(t) for t in ts)
        # Coerce pros/cons from semicolon-separated strings to lists
        for field in ("pros", "cons"):
            val = values.get(field)
            if isinstance(val, str):
                values[field] = [s.strip() for s in val.split(";") if s.strip()]
        return values


class CatalogMatch(BaseModel):
    """A single capability-to-catalog match."""
    capability: str = Field(description="Capability being matched")
    component_name: str = Field(default="", description="Component name")
    match_type: str = Field(default="No Match", description="Full Match | Partial Match | No Match")
    confidence_score: int = Field(default=0, ge=0, le=100)
    reasoning: str = Field(default="", description="Match reasoning")
    catalog_entry: CatalogEntry = Field(default_factory=CatalogEntry)


class CatalogSearchOutput(BaseModel):
    """Structured output for catalog_search playbook."""
    requirement_summary: str = Field(default="", description="Summary of the requirement")
    capabilities: Any = Field(
        default_factory=lambda: {"functional": [], "non_functional": []},
        description="Required capabilities"
    )
    decomposition: Any = Field(
        default_factory=lambda: {"core_modules": [], "supporting_modules": [], "cross_cutting": []},
        description="Module decomposition"
    )
    catalog_matches: list[CatalogMatch] = Field(default_factory=list)
    architecture_composition: str = Field(default="")
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    overall_confidence_score: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def coerce_capabilities_decomposition(cls, values):
        # LLM sometimes returns lists instead of dicts — wrap them
        for field in ("capabilities", "decomposition"):
            val = values.get(field)
            if isinstance(val, list):
                values[field] = {"items": val}
        return values


# ─── Code Analyzer ───────────────────────────────────────────────────────────

class CodeAnalyzerOutput(BaseModel):
    """Structured output for code_analyzer playbook."""
    summary: str = Field(description="Analysis summary")
    analysis: str = Field(description="Detailed analysis")
    key_insights: list[str] = Field(default_factory=list)
    strategic_implications: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


# ─── Code Explorer (ReAct) ──────────────────────────────────────────────────

class CodeExplorerOutput(BaseModel):
    """Structured output for code_explorer ReAct playbook."""
    summary: str = Field(description="Direct answer to the question")
    analysis: str = Field(description="Detailed walkthrough of findings")
    key_files: list[str] = Field(default_factory=list, description="Relevant files with their roles")
    code_flow: str = Field(default="", description="How components connect")
    insights: list[str] = Field(default_factory=list, description="Non-obvious findings or potential issues")


# ─── Solution Architect ──────────────────────────────────────────────────────

class SolutionArchitectOutput(CatalogSearchOutput):
    """Structured output for solution_architect playbook."""
    pass


# ─── SVP Analyzer ────────────────────────────────────────────────────────────

class SvpAnalyzerOutput(BaseModel):
    """Structured output for svp_analyzer playbook."""
    product_name: str = Field(default="", description="Name of the software product")
    domain: str = Field(default="", description="Business domain")
    executive_summary: str = Field(default="", description="Executive overview")
    business_functionalities: list[dict] = Field(default_factory=list, description="Business capabilities with impact")
    modules: list[dict] = Field(default_factory=list, description="Software modules with responsibilities")
    integration_points: dict = Field(default_factory=dict, description="APIs exposed/consumed, events, external systems")
    data_architecture: dict = Field(default_factory=dict, description="Data models, storage, flow patterns")
    change_impact_matrix: list[dict] = Field(default_factory=list, description="Impact entries per business function")
    modernization_assessment: dict = Field(default_factory=dict, description="Tech stack score, dependency health, risks")
    key_metrics: dict = Field(default_factory=dict, description="Quantitative indicators")
    report_markdown: str = Field(default="", description="Complete structured markdown report")


# ─── Schema Registry ────────────────────────────────────────────────────────

PLAYBOOK_SCHEMAS: dict[str, type[BaseModel]] = {
    "catalog_generator": CatalogGeneratorOutput,
    "catalog_search": CatalogSearchOutput,
    "code_analyzer": CodeAnalyzerOutput,
    "code_explorer": CodeExplorerOutput,
    "solution_architect": SolutionArchitectOutput,
    "svp_analyzer": SvpAnalyzerOutput,
}


# ─── Dynamic Schema Builder ─────────────────────────────────────────────────

# Type mapping from YAML schema type names to Python types
_YAML_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": bool,
    "number": float,
}

# Cache for dynamically built models
_dynamic_schema_cache: dict[str, type[BaseModel]] = {}


def build_pydantic_model(name: str, schema_dict: dict) -> type[BaseModel] | None:
    """
    Dynamically create a Pydantic model from a playbook's output schema YAML.
    
    Schema format:
    ```yaml
    type: json_response
    fields:
      summary: {type: string, required: true, description: "Analysis summary"}
      score: {type: integer, min: 0, max: 100, default: 50}
      tags: {type: array, items: string, default: []}
    ```
    
    Args:
        name: Playbook name (used to generate the model class name)
        schema_dict: Parsed YAML schema dict with 'fields' key
        
    Returns:
        Dynamically created Pydantic BaseModel subclass, or None if no fields defined
    """
    fields = schema_dict.get("fields", {})
    if not fields:
        return None
    
    field_definitions: dict = {}
    
    for field_name, field_spec in fields.items():
        if not isinstance(field_spec, dict):
            # Simple type shorthand: field_name: string
            python_type = _YAML_TYPE_MAP.get(str(field_spec), str)
            field_definitions[field_name] = (python_type, Field(default=""))
            continue
        
        type_name = field_spec.get("type", "string")
        is_required = field_spec.get("required", False)
        description = field_spec.get("description", "")
        default = field_spec.get("default")
        
        # Determine Python type
        if type_name == "array":
            items_type = field_spec.get("items", "string")
            item_python_type = _YAML_TYPE_MAP.get(items_type, str)
            python_type = list[item_python_type]
            if default is None:
                default = []
        elif type_name == "dict" or type_name == "object":
            python_type = dict
            if default is None:
                default = {}
        else:
            python_type = _YAML_TYPE_MAP.get(type_name, str)
        
        # Build Field kwargs
        field_kwargs = {}
        if description:
            field_kwargs["description"] = description
        if "min" in field_spec:
            field_kwargs["ge"] = field_spec["min"]
        if "max" in field_spec:
            field_kwargs["le"] = field_spec["max"]
        
        if is_required and default is None:
            field_kwargs["default"] = ...  # Required field
        elif default is not None:
            field_kwargs["default"] = default
        elif type_name == "string":
            field_kwargs["default"] = ""
        elif type_name in ("integer", "number", "float"):
            field_kwargs["default"] = 0
        elif type_name == "boolean":
            field_kwargs["default"] = False
        else:
            field_kwargs["default"] = ""
        
        # Handle Optional for non-required fields
        if not is_required and field_kwargs.get("default") is not ...:
            python_type = Optional[python_type]
        
        field_definitions[field_name] = (python_type, Field(**field_kwargs))
    
    if not field_definitions:
        return None
    
    # Generate a CamelCase model name from the playbook name
    model_name = "".join(
        word.capitalize() for word in name.replace("-", "_").split("_")
    ) + "Output"
    
    # Use Pydantic's create_model to build dynamically
    from pydantic import create_model
    model = create_model(model_name, **field_definitions)
    
    return model


def get_schema_for_playbook(playbook_name: str, playbook_def=None) -> type[BaseModel] | None:
    """
    Get Pydantic schema for a playbook's output.
    
    Resolution order:
    1. Check hardcoded PLAYBOOK_SCHEMAS dict (backward compatibility)
    2. Check dynamic schema cache
    3. Build dynamically from playbook's output_schema definition
    
    Args:
        playbook_name: Name of the playbook
        playbook_def: Optional PlaybookDefinition with output_schema
        
    Returns:
        Pydantic BaseModel subclass, or None if no schema defined
    """
    # 1. Check hardcoded registry first (backward compat)
    if playbook_name in PLAYBOOK_SCHEMAS:
        return PLAYBOOK_SCHEMAS[playbook_name]
    
    # 2. Check dynamic cache
    if playbook_name in _dynamic_schema_cache:
        return _dynamic_schema_cache[playbook_name]
    
    # 3. Build dynamically from playbook definition
    if playbook_def and hasattr(playbook_def, 'output_schema') and playbook_def.output_schema:
        model = build_pydantic_model(playbook_name, playbook_def.output_schema)
        if model:
            _dynamic_schema_cache[playbook_name] = model
            print(f"[SCHEMAS] ✓ Built dynamic schema for '{playbook_name}': "
                  f"{list(model.model_fields.keys())}")
            return model
    
    return None
