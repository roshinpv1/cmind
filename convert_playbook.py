import os
import sys
import yaml
from pathlib import Path

def convert_playbook(input_path: str, output_path: str):
    """
    Converts an old playbook (or parses an existing one) into the rigorous new Markdown format.
    Allows for easy adoption of the new schema validations and frontend UI capabilities.
    """
    input_file = Path(input_path)
    output_file = Path(output_path)
    
    if not input_file.exists():
        print(f"Error: {input_path} does not exist.")
        sys.exit(1)
        
    content = input_file.read_text()
    
    # 1. Very basic legacy parsing (adapt as needed for your old files)
    # This assumes the old files had some rough markdown sections.
    # If they were purely JSON/YAML, you'd load them using the json/yaml modules instead.
    
    name = "Converted Playbook"
    description = "Description of the playbook."
    system_prompt = "You are an AI assistant."
    
    # Try to heuristically find name and prompt
    for line in content.split('\n'):
        if line.startswith('name:'):
            name = line.replace('name:', '').strip()
        elif line.startswith('# Playbook:'):
            name = line.replace('# Playbook:', '').strip()
            
    # Try to extract Description
    if '## Description' in content:
        parts = content.split('## Description')[1].split('## ')[0]
        description = parts.strip()
        
    # Try to extract System Prompt
    if '## System Prompt' in content:
        parts = content.split('## System Prompt')[1].split('## ')[0]
        system_prompt = parts.strip()
        
    # 2. Build the new format
    new_format = f"""---
name: "{name}"
version: 1.0
category: analysis
complexity: medium
---

# Playbook: {name}

## Description
{description}

## When to Use
Use this playbook when you need to analyze code and generate structured insights.

## System Prompt
{system_prompt}

## Expected Input
Provide the goal or specific components you want to analyze.

## Search Strategy
```yaml
mode: hybrid
limit: 100
min_score: 0.3
queries:
  - "Find core domain models"
  - "Find main entry points"
```

## Behavior
```yaml
exclude_test_files: true
grounding_fence: true
inject_repo_metadata: true
skip_schema_validation: false
```

## Output Schema
```yaml
type: json_response
description: Analysis results
fields:
  summary:
    type: string
    description: High level summary
  findings:
    type: array
    description: List of technical findings
    items:
      type: string
```

## Anti-Patterns
* Do not hallucinate APIs that do not exist in the context.
* Do not recommend rewriting entire modules without justification.

## Quality Rubric
### Completeness
Must cover all requested components in the user's prompt.
### Accuracy
Must only reference files actual returned by the Search Strategy.

## Evaluation
* Check if the output JSON strictly matches the Output Schema.
* Ensure all `findings` directly quote snippet lines.
"""

    output_file.write_text(new_format)
    print(f"Successfully converted {input_path} -> {output_path}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python convert_playbook.py <input.md> <output.md>")
        sys.exit(1)
        
    convert_playbook(sys.argv[1], sys.argv[2])
