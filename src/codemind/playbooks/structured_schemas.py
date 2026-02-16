"""
Pydantic schemas for structured playbook output.

Used with CmindChatModel.with_structured_output() to validate
LLM responses against expected formats, replacing inline schema
hints and manual JSON parsing.
"""

from typing import Optional
from pydantic import BaseModel, Field


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


# ─── Catalog Search ──────────────────────────────────────────────────────────

class CatalogEntry(BaseModel):
    """A catalog entry matched during search."""
    repo_name: str = Field(default="", description="Repository name")
    repo_url: str = Field(default="", description="Repository URL")
    description: str = Field(default="", description="Entry description")
    topics: list[str] = Field(default_factory=list)
    tech_stack: str = Field(default="")
    architecture: str = Field(default="")
    category: str = Field(default="")
    quality_score: int = Field(default=0, ge=0, le=100)
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)


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
    capabilities: dict = Field(
        default_factory=lambda: {"functional": [], "non_functional": []},
        description="Required capabilities"
    )
    decomposition: dict = Field(
        default_factory=lambda: {"core_modules": [], "supporting_modules": [], "cross_cutting": []},
        description="Module decomposition"
    )
    catalog_matches: list[CatalogMatch] = Field(default_factory=list)
    architecture_composition: str = Field(default="")
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    overall_confidence_score: int = Field(default=0, ge=0, le=100)


# ─── Code Analyzer ───────────────────────────────────────────────────────────

class CodeAnalyzerOutput(BaseModel):
    """Structured output for code_analyzer playbook."""
    summary: str = Field(description="Analysis summary")
    analysis: str = Field(description="Detailed analysis")
    key_insights: list[str] = Field(default_factory=list)
    strategic_implications: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


# ─── Schema Registry ────────────────────────────────────────────────────────

PLAYBOOK_SCHEMAS: dict[str, type[BaseModel]] = {
    "catalog_generator": CatalogGeneratorOutput,
    "catalog_search": CatalogSearchOutput,
    "code_analyzer": CodeAnalyzerOutput,
}


def get_schema_for_playbook(playbook_name: str) -> type[BaseModel] | None:
    """Get Pydantic schema for a playbook's output, if defined."""
    return PLAYBOOK_SCHEMAS.get(playbook_name)
