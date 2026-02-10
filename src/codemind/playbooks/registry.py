"""
Playbook Registry - Auto-discover and manage playbooks from Markdown files.

The registry:
- Loads playbooks from the playbooks/ directory
- Provides access to playbook definitions
- Supports hot-reloading
- Formats playbooks for LLM prompts
"""

from pathlib import Path
from typing import Dict, Optional, List
from .schema import PlaybookDefinition


class PlaybookRegistry:
    """
    Central registry for all available playbooks.
    
    Playbooks are auto-discovered from Markdown files in the playbooks directory.
    """
    
    def __init__(self, playbooks_dir: Path | str = "playbooks"):
        """
        Initialize playbook registry.
        
        Args:
            playbooks_dir: Directory containing playbook MD files
        """
        self.playbooks_dir = Path(playbooks_dir)
        self.playbooks: Dict[str, PlaybookDefinition] = {}
        self._load_playbooks()
    
    def _load_playbooks(self) -> None:
        """Load all playbooks from markdown files."""
        from .parsers import parse_playbook_markdown
        
        # Create directory if it doesn't exist
        if not self.playbooks_dir.exists():
            print(f"[PLAYBOOKS] Creating playbooks directory: {self.playbooks_dir}")
            self.playbooks_dir.mkdir(parents=True, exist_ok=True)
            return
        
        # Load each .md file
        playbook_files = list(self.playbooks_dir.glob("*.md"))
        
        if not playbook_files:
            print(f"[PLAYBOOKS] No playbook files found in {self.playbooks_dir}")
            return
        
        loaded = 0
        failed = 0
        
        for playbook_file in playbook_files:
            try:
                playbook = parse_playbook_markdown(playbook_file)
                if playbook:
                    self.playbooks[playbook.name] = playbook
                    print(f"[PLAYBOOKS] ✓ Loaded: {playbook.name}")
                    loaded += 1
                else:
                    print(f"[PLAYBOOKS] ✗ Failed to parse {playbook_file.name}")
                    failed += 1
            except Exception as e:
                print(f"[PLAYBOOKS] ✗ Failed to load {playbook_file.name}: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
        
        print(f"[PLAYBOOKS] Registry initialized: {loaded} playbooks loaded, {failed} failed")
    
    def get_playbook(self, name: str) -> Optional[PlaybookDefinition]:
        """
        Get playbook definition by name.
        
        Args:
            name: Playbook identifier
            
        Returns:
            PlaybookDefinition if found, None otherwise
        """
        return self.playbooks.get(name)
    
    def list_playbooks(self) -> List[str]:
        """
        Get list of all available playbook names.
        
        Returns:
            List of playbook names sorted alphabetically
        """
        return sorted(self.playbooks.keys())
    
    def get_playbooks_description(self) -> str:
        """
        Format all playbooks for LLM prompt.
        
        Returns:
            Formatted string describing all playbooks
        """
        if not self.playbooks:
            return "No playbooks available."
        
        descriptions = []
        for name in sorted(self.playbooks.keys()):
            playbook = self.playbooks[name]
            descriptions.append(playbook.to_prompt_description())
        
        return "\n\n---\n\n".join(descriptions)
    
    def get_deterministic_playbooks(self) -> List[str]:
        """
        Get list of deterministic playbooks.
        
        Returns:
            List of deterministic playbook names
        """
        return [
            name for name, playbook in self.playbooks.items()
            if playbook.deterministic
        ]
    
    def get_non_deterministic_playbooks(self) -> List[str]:
        """
        Get list of non-deterministic (LLM-based) playbooks.
        
        Returns:
            List of non-deterministic playbook names
        """
        return [
            name for name, playbook in self.playbooks.items()
            if not playbook.deterministic
        ]
    
    def reload(self) -> None:
        """
        Reload all playbooks from disk.
        
        Useful for hot-reloading during development.
        """
        print("[PLAYBOOKS] Reloading playbooks...")
        self.playbooks.clear()
        self._load_playbooks()
    
    def __len__(self) -> int:
        """Number of loaded playbooks."""
        return len(self.playbooks)
    
    def __contains__(self, name: str) -> bool:
        """Check if playbook exists."""
        return name in self.playbooks
    
    def __repr__(self) -> str:
        return f"PlaybookRegistry({len(self.playbooks)} playbooks loaded from {self.playbooks_dir})"
