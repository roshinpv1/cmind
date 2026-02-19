"""
Parsers for playbook markdown files.

Parses markdown playbook definitions into PlaybookDefinition objects.
New format focuses on system_prompt and search_strategy.
"""

from pathlib import Path
from typing import Optional
import yaml

from .schema import PlaybookDefinition, SearchStrategy


def parse_playbook_markdown(file_path: Path) -> Optional[PlaybookDefinition]:
    """
    Parse a markdown playbook file into a PlaybookDefinition.
    
    Expected format:
    ```markdown
    # Playbook: Playbook Name
    
    ## Description
    What this playbook does
    
    ## When to Use
    When to select this playbook
    
    ## System Prompt
    System prompt for LLM
    
    ## Output Schema
    ```yaml
    type: json_response       # or "tool_call"
    tool_name: save_catalog_entry  # only if type is tool_call
    fields:
      field_name: {type: string, required: true}
      score: {type: integer, min: 0, max: 100, default: 50}
    ```
    
    ## Behavior
    ```yaml
    exclude_test_files: true
    grounding_fence: false
    inject_repo_metadata: true
    ```
    
    ## Search Strategy
    ```yaml
    queries:
      - "search query 1"
      - "search query 2"
    file_types:
      - ".py"
      - ".js"
    limit: 10
    mode: semantic
    min_score: 0.7
    max_batches: 5
    ```
    
    ## Deterministic
    true/false
    ```
    
    Args:
        file_path: Path to markdown file
        
    Returns:
        PlaybookDefinition or None if parsing fails
    """
    try:
        content = file_path.read_text()
        lines = content.split('\n')
        
        # Extract playbook name from header
        name = None
        for line in lines:
            if line.startswith('# Playbook:'):
                name = line.replace('# Playbook:', '').strip()
                break
        
        if not name:
            print(f"[PARSER] No playbook name found in {file_path}")
            return None
        
        # Extract sections
        description = _extract_section(content, "## Description")
        when_to_use = _extract_section(content, "## When to Use")
        system_prompt = _extract_section(content, "## System Prompt")
        default_prompt = _extract_section(content, "## Default Prompt")
        search_strategy_yaml = _extract_code_block(content, "## Search Strategy")
        deterministic_str = _extract_section(content, "## Deterministic")
        output_schema_yaml = _extract_code_block(content, "## Output Schema")
        behavior_yaml = _extract_code_block(content, "## Behavior")
        
        # Parse search strategy (YAML)
        search_strategy = SearchStrategy()
        if search_strategy_yaml:
            try:
                strategy_dict = yaml.safe_load(search_strategy_yaml)
                if strategy_dict:
                    search_strategy = SearchStrategy(**strategy_dict)
            except Exception as e:
                print(f"[PARSER] Failed to parse search strategy YAML: {e}")
        
        # Parse deterministic
        deterministic = deterministic_str.lower().strip() == "true" if deterministic_str else False
        
        # Parse output schema (YAML)
        output_schema = {}
        output_type = "json_response"
        tool_name = None
        if output_schema_yaml:
            try:
                schema_dict = yaml.safe_load(output_schema_yaml)
                if schema_dict and isinstance(schema_dict, dict):
                    output_type = schema_dict.get("type", "json_response")
                    tool_name = schema_dict.get("tool_name")
                    output_schema = schema_dict
                    print(f"[PARSER] ✓ Parsed output schema for {name}: "
                          f"type={output_type}, fields={list(schema_dict.get('fields', {}).keys())}")
            except Exception as e:
                print(f"[PARSER] Failed to parse output schema YAML for {name}: {e}")
        
        # Parse behavior flags (YAML)
        exclude_test_files = False
        grounding_fence = False
        inject_repo_metadata = False
        if behavior_yaml:
            try:
                behavior_dict = yaml.safe_load(behavior_yaml)
                if behavior_dict and isinstance(behavior_dict, dict):
                    exclude_test_files = behavior_dict.get("exclude_test_files", False)
                    grounding_fence = behavior_dict.get("grounding_fence", False)
                    inject_repo_metadata = behavior_dict.get("inject_repo_metadata", False)
                    print(f"[PARSER] ✓ Parsed behavior for {name}: "
                          f"exclude_test={exclude_test_files}, grounding={grounding_fence}, "
                          f"inject_meta={inject_repo_metadata}")
            except Exception as e:
                print(f"[PARSER] Failed to parse behavior YAML for {name}: {e}")
        
        playbook = PlaybookDefinition(
            name=name,
            description=description or "No description provided",
            when_to_use=when_to_use or "Not specified",
            system_prompt=system_prompt or "You are a helpful coding assistant.",
            default_prompt=default_prompt,
            search_strategy=search_strategy,
            deterministic=deterministic,
            output_schema=output_schema,
            output_type=output_type,
            tool_name=tool_name,
            exclude_test_files=exclude_test_files,
            grounding_fence=grounding_fence,
            inject_repo_metadata=inject_repo_metadata,
        )
        
        return playbook
    
    except Exception as e:
        print(f"[PARSER] Error parsing {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return None


def _extract_section(content: str, header: str) -> Optional[str]:
    """
    Extract content from a markdown section.
    
    Args:
        content: Full markdown content
        header: Section header (e.g., "## Description")
        
    Returns:
        Section content or None
    """
    lines = content.split('\n')
    in_section = False
    section_lines = []
    
    for line in lines:
        if line.strip() == header:
            in_section = True
            continue
        
        if in_section:
            # Stop at next header
            if line.startswith('#'):
                break
            section_lines.append(line)
    
    if section_lines:
        return '\n'.join(section_lines).strip()
    return None


def _extract_code_block(content: str, header: str) -> Optional[str]:
    """
    Extract code block content from a markdown section.
    
    Args:
        content: Full markdown content
        header: Section header before code block
        
    Returns:
        Code block content or None
    """
    lines = content.split('\n')
    in_section = False
    in_code_block = False
    code_lines = []
    
    for line in lines:
        if line.strip() == header:
            in_section = True
            continue
        
        if in_section:
            # Check for code block start
            if line.strip().startswith('```'):
                if not in_code_block:
                    in_code_block = True
                    continue
                else:
                    # End of code block
                    break
            
            if in_code_block:
                code_lines.append(line)
    
    if code_lines:
        return '\n'.join(code_lines).strip()
    return None
