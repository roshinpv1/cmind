
"""
Skill Selector - Intelligently picks the right skill for a user request.
"""
from typing import Optional
import re

class SkillSelector:
    """
    Selects the best skill for a user query using an LLM.
    """
    
    def __init__(self, registry, llm_client):
        self.registry = registry
        self.llm = llm_client
        
    async def select_skill(self, query: str) -> str:
        """
        Analyze query and select the best skill.
        
        Args:
            query: User's natural language request
            
        Returns:
            Name of the selected skill
        """
        # Get all available skills with descriptions
        skills_info = []
        for name in self.registry.list_skills():
            skill = self.registry.get_skill(name)
            # Format: - name: description (when to use)
            info = f"- {name}: {skill.description}. Use when: {skill.when_to_use}"
            skills_info.append(info)
            
        skills_text = "\n".join(skills_info)
        
        # Construct prompt
        system_prompt = (
            "You are an intelligent agent orchestrator. Your job is to select the single best skill "
            "to handle a user's request.\n\n"
            f"AVAILABLE SKILLS:\n{skills_text}\n\n"
            "INSTRUCTIONS:\n"
            "1. Analyze the user's request carefully.\n"
            "2. Select the ONE skill that best matches the intent.\n"
            "3. Return ONLY the skill name. No markdown, no explanation.\n"
            "4. If unsure, default to 'code_analyzer'."
        )
        
        user_prompt = f"USER REQUEST: {query}\n\nOver to you. Select the skill:"
        
        try:
            # Call LLM
            response = await self.llm.generate(
                user_prompt,
                system_prompt=system_prompt,
                max_tokens=50,
                temperature=0.1
            )
            
            # Clean response
            selected_skill = response.strip().replace('"', '').replace("'", "").split('\n')[0]
            
            # Validate
            if self.registry.get_skill(selected_skill):
                return selected_skill
            
            print(f"[SELECTOR] Invalid skill selected: '{selected_skill}', defaulting to code_analyzer")
            return "code_analyzer"
            
        except Exception as e:
            print(f"[SELECTOR] Error selecting skill: {e}")
            return "code_analyzer"
