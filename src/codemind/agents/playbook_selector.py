
"""
Playbook Selector - Intelligently picks the right playbook for a user request.
"""
from typing import Optional
import re

class PlaybookSelector:
    """
    Selects the best playbook for a user query using an LLM.
    """
    
    def __init__(self, registry, llm_client):
        self.registry = registry
        self.llm = llm_client
        
    async def select_playbook(self, query: str) -> str:
        """
        Analyze query and select the best playbook.
        
        Args:
            query: User's natural language request
            
        Returns:
            Name of the selected playbook
        """
        # Get all available playbooks with descriptions
        playbooks_info = []
        for name in self.registry.list_playbooks():
            playbook = self.registry.get_playbook(name)
            # Format: - name: description (when to use)
            info = f"- {name}: {playbook.description}. Use when: {playbook.when_to_use}"
            playbooks_info.append(info)
            
        playbooks_text = "\n".join(playbooks_info)
        
        # Construct prompt
        system_prompt = (
            "You are an intelligent agent orchestrator. Your job is to select the single best playbook "
            "to handle a user's request.\n\n"
            f"AVAILABLE PLAYBOOKS:\n{playbooks_text}\n\n"
            "INSTRUCTIONS:\n"
            "1. Analyze the user's request carefully.\n"
            "2. Select the ONE playbook that best matches the intent.\n"
            "3. Return ONLY the playbook name. No markdown, no explanation.\n"
            "4. If unsure, default to 'code_analyzer'."
        )
        
        user_prompt = f"USER REQUEST: {query}\n\nOver to you. Select the playbook:"
        
        try:
            # Call LLM
            response = await self.llm.generate(
                user_prompt,
                system_prompt=system_prompt,
                max_tokens=50,
                temperature=0.1
            )
            
            # Clean response
            selected_playbook = response.strip().replace('"', '').replace("'", "").split('\n')[0]
            
            # Validate
            if self.registry.get_playbook(selected_playbook):
                return selected_playbook
            
            print(f"[SELECTOR] Invalid playbook selected: '{selected_playbook}', defaulting to code_analyzer")
            return "code_analyzer"
            
        except Exception as e:
            print(f"[SELECTOR] Error selecting playbook: {e}")
            return "code_analyzer"
