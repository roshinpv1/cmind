"""
Parsers for playbook markdown files.

Parses markdown playbook definitions into PlaybookDefinition objects.
Supports YAML frontmatter, few-shot examples, anti-patterns, quality rubrics,
evaluation rules, and dependency declarations.
"""

from pathlib import Path
from typing import Optional
import yaml

from .schema import PlaybookDefinition, SearchStrategy


def parse_playbook_markdown(file_path: Path) -> Optional[PlaybookDefinition]:
    """
    Parse a markdown playbook file into a PlaybookDefinition.
    
    Supports optional YAML frontmatter (--- delimited) and these sections:
    - ## Description, ## When to Use, ## System Prompt
    - ## Output Schema, ## Behavior, ## Search Strategy
    - ## Examples (few-shot), ## Anti-Patterns, ## Quality Rubric
    - ## Evaluation, ## Dependencies, ## Deterministic
    
    Args:
        file_path: Path to markdown file
        
    Returns:
        PlaybookDefinition or None if parsing fails
    """
    try:
        content = file_path.read_text()
        
        # --- Parse optional YAML frontmatter ---
        frontmatter = _extract_frontmatter(content)
        # Strip frontmatter from content for section parsing
        if frontmatter:
            content = _strip_frontmatter(content)
        
        lines = content.split('\n')
        
        # Extract playbook name from header or frontmatter
        name = frontmatter.get("name") if frontmatter else None
        if not name:
            for line in lines:
                if line.startswith('# Playbook:'):
                    name = line.replace('# Playbook:', '').strip()
                    break
                # Also check for name: line outside frontmatter (legacy format)
                if line.startswith('name: '):
                    name = line.replace('name:', '').strip()
                    break
        
        if not name:
            print(f"[PARSER] No playbook name found in {file_path}")
            return None
        
        # --- Extract standard sections ---
        description = _extract_section(content, "## Description")
        when_to_use = _extract_section(content, "## When to Use")
        system_prompt = _extract_section(content, "## System Prompt")
        default_prompt = _extract_section(content, "## Default Prompt")
        search_strategy_yaml = _extract_code_block(content, "## Search Strategy")
        deterministic_str = _extract_section(content, "## Deterministic")
        output_schema_yaml = _extract_code_block(content, "## Output Schema")
        behavior_yaml = _extract_code_block(content, "## Behavior")
        
        # --- Extract new best-practice sections ---
        examples = _extract_examples(content)
        anti_patterns = _extract_bullet_list(content, "## Anti-Patterns")
        quality_rubric = _extract_quality_rubric(content)
        evaluation_rules = _extract_bullet_list(content, "## Evaluation")
        dependencies_yaml = _extract_code_block(content, "## Dependencies")
        
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
        skip_schema_validation = False
        if behavior_yaml:
            try:
                behavior_dict = yaml.safe_load(behavior_yaml)
                if behavior_dict and isinstance(behavior_dict, dict):
                    exclude_test_files = behavior_dict.get("exclude_test_files", False)
                    grounding_fence = behavior_dict.get("grounding_fence", False)
                    inject_repo_metadata = behavior_dict.get("inject_repo_metadata", False)
                    skip_schema_validation = behavior_dict.get("skip_schema_validation", False)
                    print(f"[PARSER] ✓ Parsed behavior for {name}: "
                          f"exclude_test={exclude_test_files}, grounding={grounding_fence}, "
                          f"inject_meta={inject_repo_metadata}, skip_validation={skip_schema_validation}")
            except Exception as e:
                print(f"[PARSER] Failed to parse behavior YAML for {name}: {e}")
        
        # Parse dependencies (YAML)
        dependencies = {}
        if dependencies_yaml:
            try:
                deps_dict = yaml.safe_load(dependencies_yaml)
                if deps_dict and isinstance(deps_dict, dict):
                    dependencies = deps_dict
            except Exception:
                pass
        
        # Frontmatter overrides
        version = str(frontmatter.get("version", "1.0")) if frontmatter else "1.0"
        category = frontmatter.get("category", "analysis") if frontmatter else "analysis"
        complexity_level = frontmatter.get("complexity", "medium") if frontmatter else "medium"
        max_iterations = frontmatter.get("max_iterations", 5) if frontmatter else 5
        
        # Log new sections if present
        extras = []
        if examples:
            extras.append(f"examples={len(examples)}")
        if anti_patterns:
            extras.append(f"anti_patterns={len(anti_patterns)}")
        if quality_rubric:
            extras.append(f"rubric={len(quality_rubric)}")
        if evaluation_rules:
            extras.append(f"eval_rules={len(evaluation_rules)}")
        if dependencies:
            extras.append(f"deps={list(dependencies.keys())}")
        if extras:
            print(f"[PARSER] ✓ Best practices for {name}: {', '.join(extras)}")
        
        playbook = PlaybookDefinition(
            name=name,
            description=frontmatter.get("description", description or "No description provided") if frontmatter else (description or "No description provided"),
            when_to_use=when_to_use or "Not specified",
            version=version,
            category=category,
            complexity_level=complexity_level,
            max_iterations=max_iterations,
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
            skip_schema_validation=skip_schema_validation,
            examples=examples,
            anti_patterns=anti_patterns,
            quality_rubric=quality_rubric,
            evaluation_rules=evaluation_rules,
            dependencies=dependencies,
        )
        
        return playbook
    
    except Exception as e:
        print(f"[PARSER] Error parsing {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def _extract_frontmatter(content: str) -> Optional[dict]:
    """Extract YAML frontmatter from --- delimited block at start of file."""
    stripped = content.lstrip()
    if not stripped.startswith('---'):
        return None
    
    # Find closing ---
    lines = stripped.split('\n')
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == '---':
            end_idx = i
            break
    
    if end_idx is None:
        return None
    
    yaml_text = '\n'.join(lines[1:end_idx])
    try:
        return yaml.safe_load(yaml_text) or {}
    except Exception:
        return None


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from content."""
    stripped = content.lstrip()
    if not stripped.startswith('---'):
        return content
    
    lines = stripped.split('\n')
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == '---':
            return '\n'.join(lines[i + 1:])
    return content


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------

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
            # Stop at next header (level 1 or 2, but NOT level 3+)
            if line.startswith('# ') or line.startswith('## '):
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


def _extract_bullet_list(content: str, header: str) -> list[str]:
    """Extract a bullet-point list from a section as a list of strings."""
    section = _extract_section(content, header)
    if not section:
        return []
    
    items = []
    for line in section.split('\n'):
        line = line.strip()
        if line.startswith('- '):
            items.append(line[2:].strip())
        elif line.startswith('* '):
            items.append(line[2:].strip())
    return items


def _extract_examples(content: str) -> list[dict]:
    """
    Extract few-shot examples from ## Examples section.
    
    Expected format:
    ## Examples
    ### Example: "description"
    **Input goal**: "query text"
    **Expected output**:
    ```json
    { ... }
    ```
    """
    section = _extract_section(content, "## Examples")
    if not section:
        return []
    
    examples = []
    lines = section.split('\n')
    current_input = None
    in_output_block = False
    output_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # Extract input goal
        if stripped.startswith('**Input goal**:') or stripped.startswith('**Input**:'):
            current_input = stripped.split(':', 1)[1].strip().strip('"\'')
        
        # Detect output code block
        if stripped.startswith('```') and in_output_block:
            # End of output block
            if current_input and output_lines:
                examples.append({
                    "input": current_input,
                    "output": '\n'.join(output_lines).strip()
                })
            current_input = None
            output_lines = []
            in_output_block = False
            continue
        
        if stripped.startswith('```') and not in_output_block:
            in_output_block = True
            continue
        
        if in_output_block:
            output_lines.append(line)
    
    return examples


def _extract_quality_rubric(content: str) -> list[dict]:
    """
    Extract quality rubric from ## Quality Rubric section.
    
    Supports both YAML code blocks and markdown tables:
    | Criterion | Weight | Pass Condition |
    """
    # Try YAML first
    yaml_block = _extract_code_block(content, "## Quality Rubric")
    if yaml_block:
        try:
            parsed = yaml.safe_load(yaml_block)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    
    # Try markdown table
    section = _extract_section(content, "## Quality Rubric")
    if not section:
        return []
    
    rubric = []
    for line in section.split('\n'):
        line = line.strip()
        # Skip table header and separator
        if line.startswith('|') and '---' not in line and 'Criterion' not in line:
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if len(cells) >= 3:
                rubric.append({
                    "criterion": cells[0],
                    "weight": cells[1],
                    "pass_condition": cells[2],
                })
    return rubric
