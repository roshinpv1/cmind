"""
Code Analyzer Agent for AutoGen Codebase Understanding Agent.

ThCRITICACRITICAL: COLLABORATIVE KNOWLEDGE BASE APPROACH
You will maintain a "key_findings" list that serves as a collaborative knowledge base:
1. REVIEW the existing key_findings from previous iterations
2. ADD your new important discoveries to the list
3. UPDATE or REFINE existing findings if you have new insights
4. REMOVE findings that are no longer relevant or were incorrectLABORATIVE KNOWLEDGE BASE APPROACH
You will maintain a "key_findings" list that serves as a collaborative knowledge base:
1. 📝 REVIEW the existing key_findings from previous iterations
2. 🔍 ADD your new important discoveries to the list
3. 🔄 UPDATE or REFINE existing findings if you have new insights
4. 🗑️ REMOVE findings that are no longer relevant or were incorrect

This shared knowledge base ensures all critical information is preserved across iterations.e implements the Code Analyzer agent responsible for technical analysis
of codebases using multi-round self-iteration and shell command execution.
"""

import logging

from autogen_agentchat.agents import AssistantAgent

from ..utils.autogen_utils import extract_text_from_autogen_response


class CodeAnalyzer:
    """
    Technical expert agent responsible for codebase analysis using shell commands
    and iterative exploration.

    The Code Analyzer performs multi-round self-iteration to progressively analyze
    codebases, building knowledge through targeted shell command execution and
    self-assessment of analysis completeness.
    """

    def __init__(self, config: dict, fs_tool, repo_id: str = None):
        """
        Initialize the Code Analyzer agent.

        Args:
            config: Configuration dict containing model settings
            shell_tool: Shell execution tool for codebase exploration
            repo_id: Active repository ID for graph analysis tool context
        """
        self.config = config
        self.fs_tool = fs_tool
        self.repo_id = repo_id
        self.logger = logging.getLogger(__name__)

        # Initialize AutoGen agent with shell tool capability
        self._agent = self._create_autogen_agent()

    def _create_autogen_agent(self) -> AssistantAgent:
        """Create and configure the AutoGen AssistantAgent without shell tool capability."""
        system_message = self._get_system_message()

        # Create the agent without tools (LLM will provide commands via JSON response)
        agent = AssistantAgent(
            name="code_analyzer",
            system_message=system_message,
            model_client=self.config,
        )

        return agent

    def _get_system_message(self) -> str:
        """Get the system message for the Code Analyzer agent."""
        return r"""You are a Code Analyzer, a technical expert responsible for comprehensive codebase analysis.

CRITICAL: You MUST always start by exploring the codebase with your file system tools before providing any analysis.

Your capabilities:
- Multi-round iterative analysis with self-assessment
- Requesting file system tool execution for codebase exploration
- Progressive knowledge building and confidence assessment
- Strategic tool selection based on task context

🔍 DISCOVERY-DRIVEN ANALYSIS PHILOSOPHY:
Be a detective, not a robot. Let curiosity and discovery drive your analysis rather than following rigid checklists.
Trust your pattern recognition abilities and adapt your exploration strategy based on what the codebase reveals to you.

**Core Principles**:
- **Curiosity over Checklists**: Explore what's interesting rather than what's expected
- **Content-Driven Discovery**: Let what you find guide what you look for next
- **Adaptive Exploration**: Change strategy based on what the codebase reveals
- **Universal Understanding**: Focus on concepts rather than language-specific patterns

🎯 ACTIVE CONTEXT:
- Repository ID: {self.repo_id or "Unknown/Default"}
- All graph tools (get_map, search_symbol, etc.) will use this repository context by default.
- You can override this by providing a different "repo_id" in tool arguments if necessary.

🎯 ACTIVE CONTEXT:
- Repository ID: {self.repo_id or "Unknown/Default"} (Inferred automatically if omitted in tool calls)

🚀 SMART ANALYSIS STRATEGY:

1. **Discovery & Mapping Phase**: Start with high-level structural discovery
   - Use `get_map` to see high-degree architectural hubs.
   - Use `list_repo_directory` or `list_files` to understand the physical and logical file structure.

2. **Surgical Search Phase**: Find specific code locations based on your goal
   - Use `search_code` for semantic/hybrid searches (e.g., "find authentication logic").
   - Use `grep_search` for pattern-based scanning (e.g., "grep for vulnerable functions like 'eval'").
   - Use `search_symbol` to jump directly to class or function definitions.

3. **In-Depth Analysis Phase**: Read and trace logic flow
   - Use `read_file` to examine source code. Always check `get_file_info` first for large files.
   - Use `get_file_outline` to quickly see classes and methods without reading the whole file.
   - Use `get_callers`, `get_callees`, or `trace_path` to map how features interact across files.

🛠️ UNIFIED REPOSITORY TOOLS:

📂 EXPLORATION & DISCOVERY:
- `get_map` (args: {"limit": int}) - Architecture map showing high-impact files/nodes.
- `list_repo_directory` (args: {"relative_path": str, "recursive": bool}) - List physical directories/files on disk.
- `list_files` (args: {"pattern": str, "file_type": str}) - Find files in the indexed graph matching a glob pattern.
- `get_file_info` (args: {"path": str}) - Get file size, type, and modified time (Safe check before reading).

🔍 CODE SEARCH:
- `search_code` (args: {"queries": [str], "limit": int, "mode": "hybrid"}) - Semantic and lexical search. Best for concept-based discovery.
- `grep_search` (args: {"query": str, "includes": [str], "limit": int}) - Surgical regex search over actual source text.
- `search_symbol` (args: {"name": str, "symbol_type": str}) - Find a class or function by its exact name.

📖 ANALYSIS & TRACING:
- `read_file` (args: {"file_path": str, "start_line": int, "end_line": int}) - Read file content (slice or full).
- `get_file_outline` (args: {"file_path": str}) - Get AST outline (classes, methods, functions) of a file.
- `get_callers` / `get_callees` (args: {"function_name": str}) - Find what calls a function, or what it calls.
- `get_dependencies` (args: {"file_path": str, "direction": "imports"/"imported_by"}) - File import relationship analysis.
- `trace_path` (args: {"start": str, "end": str}) - Find connectivity between two symbols/files.

🌐 ADVANCED GRAPH TRAVERSAL:
- `graphify_query` (args: {"question": str, "mode": str, "depth": int}) - Natural language graph traversal.

📁 BINARY FILE HANDLING:
Do NOT try to `read_file` on binaries. Check `get_file_info` first.

📋 FILE READING BEST PRACTICES:
1. **Targeted Reading**: Use `start_line` and `end_line` for large files.
2. **Structural First**: Use `get_file_outline` before reading full implementation.

🧠 COLLABORATIVE KNOWLEDGE BASE:
Maintain a "key_findings" list that serves as shared memory across iterations:
1. 📝 REVIEW existing key_findings
2. 🔍 ADD new important discoveries
3. 🔄 UPDATE or REFINE insights
4. 🗑️ REMOVE irrelevant findings

📤 RESPONSE FORMAT:
You MUST respond in valid JSON format with these exact fields:
{
    "need_tool_execution": true/false,
    "tool_calls": [
        {"tool": "search_code", "args": {"queries": ["vulnerability entry points"]}},
        {"tool": "get_map", "args": {"limit": 20}}
    ],
    "key_findings": ["Finding 1", "Finding 2", "..."],
    "current_analysis": "Your analysis of this iteration",
    "confidence_level": 1-10,
    "next_focus_areas": "What you plan to focus on next"
}

💡 FIRST ITERATION STARTER TOOLS:
- [{"tool": "get_map", "args": {"limit": 20}}, {"tool": "list_repo_directory", "args": {"relative_path": "."}}]

🎯 YOUR GOAL: UNDERSTANDING through discovery, not checklist completion.
Adapt your reading strategy based on:
- File size and complexity
- The specific question being asked
- The type of content (code, config, docs, etc.)
- What the codebase reveals to you

Always explain your findings with specific examples, line numbers, and evidence from the code."""

    def analyze_codebase(
        self, query: str, codebase_path: str, specialist_feedback: str | None = None
    ) -> str:
        """
        Analyze codebase with multi-round self-iteration for progressive analysis.

        Args:
            query: User's analysis request
            codebase_path: Path to the codebase to analyze
            specialist_feedback: Optional feedback from Task Specialist to guide analysis focus

        Returns:
            Comprehensive analysis result
        """
        # Initialize iteration state
        max_iterations = 10
        current_iteration = 0
        analysis_context = []
        tool_execution_history = []
        shared_key_findings = []  # Collaborative knowledge base
        convergence_indicators = {
            "sufficient_code_coverage": False,
            "question_answered": False,
            "confidence_threshold_met": False,
        }

        while current_iteration < max_iterations:
            current_iteration += 1

            # Prepare iteration-specific prompt
            iteration_prompt = self._build_iteration_prompt(
                query,
                codebase_path,
                current_iteration,
                analysis_context,
                tool_execution_history,
                shared_key_findings,
                convergence_indicators,
                specialist_feedback,
            )

            # Execute analysis step with agent (LLM decision phase)
            def run_step(prompt):
                import asyncio

                async def async_step():
                    result = await self.agent.run(task=prompt)
                    return result

                return asyncio.run(async_step())

            step_response = run_step(iteration_prompt)

            # Extract text from TaskResult object
            response_text = extract_text_from_autogen_response(step_response)

            # Parse JSON response from LLM
            try:
                import json

                self.logger.debug(f"Raw LLM response: {response_text[:500]}...")

                # Extract JSON from markdown code blocks if present
                json_text = self._extract_json_from_response(response_text)

                llm_decision = json.loads(json_text)
                self.logger.debug(f"Parsed LLM decision: {llm_decision}")

                # Update shared key findings from LLM response
                if "key_findings" in llm_decision:
                    shared_key_findings = llm_decision["key_findings"]

            except json.JSONDecodeError as e:
                # Fallback: treat as plain text analysis without shell commands
                self.logger.warning(f"JSON parsing failed: {e}")
                self.logger.warning(f"Raw response was: {response_text[:200]}...")
                llm_decision = {
                    "need_tool_execution": False,
                    "tool_calls": [],
                    "key_findings": shared_key_findings,  # Preserve existing findings
                    "current_analysis": response_text,
                    "confidence_level": 5,
                    "next_focus_areas": "Continue analysis",
                }

            # Execute shell commands if needed (execution phase)
            tool_results = []
            if llm_decision.get("need_tool_execution", False):
                tool_calls = llm_decision.get("tool_calls", [])
                tool_results = self._execute_tool_calls(tool_calls)
                tool_execution_history.append(
                    {
                        "iteration": current_iteration,
                        "commands": tool_calls,
                        "results": tool_results,
                        "timestamp": self._get_timestamp(),
                    }
                )

            # Store analysis step
            analysis_context.append(
                {
                    "iteration": current_iteration,
                    "llm_decision": llm_decision,
                    "tool_results": tool_results,
                    "timestamp": self._get_timestamp(),
                }
            )

            # Assess convergence based on LLM's confidence and analysis
            convergence_indicators = self._assess_convergence_from_json(
                llm_decision, analysis_context
            )

            # Generate milestone summary at regular intervals
            milestone_interval = max_iterations // 2  # Two summaries per cycle
            if milestone_interval > 0 and current_iteration % milestone_interval == 0:
                try:
                    self.logger.info(
                        f"Generating milestone summary at iteration {current_iteration}"
                    )
                    milestone_summary = self._generate_milestone_summary(
                        query,
                        tool_execution_history,
                        analysis_context,
                        current_iteration,
                        milestone_interval,
                    )

                    # Add milestone summary to shared knowledge base
                    milestone_number = current_iteration // milestone_interval
                    milestone_finding = f"🔄 MILESTONE {milestone_number} SUMMARY (Iterations {current_iteration-milestone_interval+1}-{current_iteration}): {milestone_summary}"
                    shared_key_findings.append(milestone_finding)

                    self.logger.info(
                        f"Added milestone {milestone_number} summary to knowledge base"
                    )

                except Exception as e:
                    self.logger.warning(f"Failed to generate milestone summary: {e}")

            # Check if analysis is complete
            if self._should_terminate(convergence_indicators) or not llm_decision.get(
                "need_tool_execution", True
            ):
                break

        # Synthesize final response
        return self._synthesize_final_response(
            query, analysis_context, shared_key_findings, convergence_indicators
        )

    def _execute_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """Execute a list of tool calls and return results."""
        results = []
        for call in tool_calls:
            tool_name = call.get("tool")
            args = call.get("args", {})
            try:
                success, stdout, stderr = self.fs_tool.execute_tool(tool_name, args)
                result = {
                    "command": f"{tool_name}(**{args})",  # Keep 'command' key for backwards compatibility in logs
                    "success": success,
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                    "error": None,
                }
            except Exception as e:
                result = {
                    "command": f"{tool_name}(**{args})",
                    "success": False,
                    "stdout": "",
                    "stderr": "",
                    "error": str(e),
                }
            results.append(result)

        return results

    def _assess_convergence_from_json(self, llm_decision: dict, context: list) -> dict:
        """Assess convergence based on LLM's JSON response."""
        convergence = {
            "sufficient_code_coverage": False,
            "question_answered": False,
            "confidence_threshold_met": False,
        }

        # Check confidence level from LLM
        confidence = llm_decision.get("confidence_level", 0)
        if confidence >= 8:
            convergence["confidence_threshold_met"] = True

        # Check if LLM indicates no need for more shell execution
        if not llm_decision.get("need_tool_execution", True):
            convergence["question_answered"] = True

        # Check for code coverage based on number of iterations and shell commands executed
        total_commands = sum(len(ctx.get("tool_results", [])) for ctx in context)
        if total_commands >= 3 or len(context) >= 2:
            convergence["sufficient_code_coverage"] = True

        return convergence

    def _generate_milestone_summary(
        self,
        query: str,
        tool_history: list,
        analysis_context: list,
        current_iteration: int,
        milestone_interval: int,
    ) -> str:
        """
        Generate a comprehensive milestone summary of recent iterations.

        Args:
            query: The original user query
            tool_history: Complete shell execution history
            analysis_context: Complete analysis context
            current_iteration: Current iteration number
            milestone_interval: Interval between milestones

        Returns:
            Comprehensive summary string
        """
        # Calculate the range of iterations to summarize
        start_iteration = max(1, current_iteration - milestone_interval + 1)
        end_iteration = current_iteration

        # Filter relevant history for this milestone period
        relevant_tool_history = [
            sh
            for sh in tool_history
            if start_iteration <= sh["iteration"] <= end_iteration
        ]

        relevant_analysis_context = [
            ctx
            for ctx in analysis_context
            if start_iteration <= ctx["iteration"] <= end_iteration
        ]

        # Build comprehensive summary prompt
        summary_prompt = f"""
        You are tasked with creating a MILESTONE SUMMARY for codebase analysis iterations {start_iteration}-{end_iteration}.

        Original Query: {query}

        Your goal is to synthesize ALL discoveries, patterns, and insights from these {milestone_interval} iterations into a comprehensive summary that preserves critical knowledge for future iterations.

        === SHELL EXECUTION HISTORY FOR THIS MILESTONE ===
        """

        for tool_exec in relevant_tool_history:
            summary_prompt += f"\nIteration {tool_exec['iteration']}:\n"
            for result in tool_exec["results"]:
                summary_prompt += f"Command: {result['command']}\n"
                if result["success"] and result.get("stdout"):
                    # Include more complete output for summary purposes
                    stdout_sample = result["stdout"][:800]  # More context for summary
                    summary_prompt += f"Output: {stdout_sample}...\n"
                else:
                    summary_prompt += f"Error: {result['stderr'] or result.get('error', 'Unknown error')}\n"
            summary_prompt += "\n"

        summary_prompt += "\n=== ANALYSIS INSIGHTS FOR THIS MILESTONE ===\n"

        for ctx in relevant_analysis_context:
            iteration = ctx["iteration"]
            llm_decision = ctx.get("llm_decision", {})

            summary_prompt += f"\nIteration {iteration}:\n"

            current_analysis = llm_decision.get("current_analysis", "")
            if current_analysis:
                summary_prompt += f"Analysis: {current_analysis}\n"

            focus_areas = llm_decision.get("next_focus_areas", "")
            if focus_areas:
                summary_prompt += f"Focus Areas: {focus_areas}\n"

            confidence = llm_decision.get("confidence_level", "N/A")
            summary_prompt += f"Confidence: {confidence}\n"

        summary_prompt += """

        === SUMMARY REQUIREMENTS ===
        Create a comprehensive milestone summary that captures:

        1. **Key Technical Discoveries**: What specific technical details were uncovered?
        2. **Architectural Patterns**: What structural or design patterns were identified?
        3. **Important Files/Components**: Which files or components are most significant?
        4. **Relationships & Dependencies**: How do different parts connect or depend on each other?
        5. **Configuration & Setup**: Any important configuration or setup insights?
        6. **Progress Assessment**: What has been thoroughly understood vs. what needs more investigation?
        7. **Critical Insights**: Any breakthrough understanding or important realizations?

        Format as a dense, information-rich summary that future iterations can build upon.
        Focus on concrete technical findings rather than process descriptions.

        Keep it concise but comprehensive - aim for 3-5 sentences that capture the essence of all discoveries.
        """

        try:
            # Use the agent to generate the summary
            import asyncio

            async def generate_summary():
                result = await self._agent.run(task=summary_prompt)
                return extract_text_from_autogen_response(result)

            summary = asyncio.run(generate_summary())

            # Clean and validate the summary
            if summary and len(summary.strip()) > 20:
                return summary.strip()
            else:
                # Fallback summary if LLM fails
                return f"Milestone {current_iteration//milestone_interval} completed iterations {start_iteration}-{end_iteration}. Executed {len(relevant_tool_history)} shell sessions with focus on {query}."

        except Exception as e:
            self.logger.warning(f"LLM summary generation failed: {e}")
            # Simple fallback summary
            return f"Milestone summary for iterations {start_iteration}-{end_iteration}: Executed {len(relevant_tool_history)} shell sessions analyzing {query}."

    def _build_iteration_prompt(
        self,
        query: str,
        codebase_path: str,
        iteration: int,
        context: list,
        tool_history: list,
        shared_key_findings: list,
        convergence: dict,
        specialist_feedback: str | None = None,
    ) -> str:
        """Build unified prompt with shared knowledge base for progressive analysis."""

        base_prompt = f"""
        CODEBASE ANALYSIS - ITERATION {iteration}

        Target: {codebase_path}
        User Query: {query}

        ULTIMATE GOAL: Create a comprehensive, detailed report that thoroughly addresses the user's query.
        Your final deliverable should be a well-structured analysis that provides actionable insights and complete answers.

        ANALYSIS STRATEGY GUIDANCE:
        You are encouraged to follow a progressive analysis approach, but you have full autonomy to decide your exploration strategy based on the specific query and context:

        🎯 SUGGESTED PROGRESSION (adapt as needed):
        1. TARGETED EXPLORATION: Start with specific, query-related areas (find relevant files, grep keywords)
        2. CONTEXTUAL EXPANSION: Explore related files, dependencies, configurations around your findings
        3. DEEPER ANALYSIS: Read actual code content, understand implementation details and algorithms
        4. COMPREHENSIVE COVERAGE: Fill gaps, check alternatives, verify understanding
        5. VALIDATION & SYNTHESIS: Double-check findings, resolve inconsistencies, provide final analysis

        💡 STRATEGIC CONSIDERATIONS:
        - For simple queries: You might jump directly to targeted searches and provide quick answers
        - For complex queries: Follow the full progression to ensure comprehensive coverage
        - For architectural questions: Focus on structure, relationships, and high-level patterns
        - For implementation questions: Dive deep into specific code logic and details

        🔧 RECOMMENDED SHELL COMMANDS:
        - Exploration: ls, find, tree (understand structure)
        - Search: grep -r, grep -n (find specific content)
        - Content: cat, head, tail (read file contents)
        - Analysis: wc, file, stat (get file information)

        Remember: You must respond in valid JSON format with the exact structure specified in your system message.

        """

        # Add specialist feedback if provided
        if specialist_feedback:
            base_prompt += f"""
        🎯 TASK SPECIALIST FEEDBACK - PRIORITY FOCUS AREAS:
        {specialist_feedback}

        IMPORTANT: Address the above feedback areas as your primary focus. The Task Specialist has identified
        these as critical gaps in the previous analysis. Make sure to specifically target these areas in your
        exploration strategy.

        """

        # Add shared knowledge base (collaborative key findings)
        if shared_key_findings:
            base_prompt += (
                "\n🧠 SHARED KNOWLEDGE BASE (Key Findings from All Iterations):\n"
            )
            for i, finding in enumerate(shared_key_findings, 1):
                base_prompt += f"{i}. {finding}\n"
            base_prompt += (
                "\nYou can ADD, UPDATE, REFINE, or REMOVE findings in your response.\n"
            )
        else:
            base_prompt += "\n🧠 SHARED KNOWLEDGE BASE: Empty (you'll create the first key findings)\n"

        # Add recent shell execution results for context
        if tool_history:
            base_prompt += "\n📋 RECENT SHELL EXECUTION RESULTS:\n"
            for tool_exec in tool_history[-2:]:  # Show last 2 executions
                base_prompt += f"\nIteration {tool_exec['iteration']}:\n"
                for result in tool_exec["results"]:
                    base_prompt += f"Command: {result['command']}\n"
                    if result["success"]:
                        stdout_preview = (
                            result["stdout"][:300] + "..."
                            if len(result["stdout"]) > 300
                            else result["stdout"]
                        )
                        base_prompt += f"Output: {stdout_preview}\n"
                    else:
                        base_prompt += f"Error: {result['stderr'] or result.get('error', 'Unknown error')}\n"
                base_prompt += "\n"

        # Add brief recent analysis context (not full history)
        if context:
            base_prompt += "\n📊 RECENT ANALYSIS CONTEXT:\n"
            for ctx in context[-1:]:  # Show only last context
                llm_decision = ctx.get("llm_decision", {})
                base_prompt += f"Previous iteration {ctx['iteration']} focused on: {llm_decision.get('next_focus_areas', 'N/A')}\n"
                base_prompt += f"Previous confidence: {llm_decision.get('confidence_level', 'N/A')}\n"

        # Add current iteration context and convergence status
        base_prompt += f"""

        📈 CURRENT ANALYSIS STATUS:
        - Iteration: {iteration}/10
        - Code coverage sufficient: {convergence['sufficient_code_coverage']}
        - Question answered: {convergence['question_answered']}
        - Confidence threshold met: {convergence['confidence_threshold_met']}

        🎯 DECISION POINTS FOR THIS ITERATION:
        Based on the shared knowledge base and your analysis so far, decide:
        1. Do you need more information via shell commands? (set need_tool_execution: true/false)
        2. What specific commands would help you gather the missing information?
        3. What is your current confidence level (1-10) in providing a comprehensive answer?
        4. If confidence >= 8, consider providing your final comprehensive analysis

        ⚠️ CRITICAL: Update the "key_findings" list:
        - ADD new important discoveries from this iteration
        - UPDATE or REFINE existing findings with new insights
        - REMOVE findings that are no longer relevant or incorrect
        - Keep findings concise but informative (1-2 sentences each)

        This shared knowledge base is the collective memory of all iterations.

        RESPONSE FORMAT: You MUST respond in valid JSON format with these exact fields:
        {{
            "need_tool_execution": true/false,
            "tool_calls": ["command1", "command2", ...],
            "key_findings": ["Updated list of key findings from all iterations"],
            "current_analysis": "Your analysis of this iteration and current understanding",
            "confidence_level": 1-10,
            "next_focus_areas": "What you plan to focus on next (or 'Final analysis complete' if done)"
        }}
        """

        return base_prompt

    def _should_terminate(self, convergence: dict) -> bool:
        """Determine if analysis should terminate based on convergence indicators."""
        # Terminate if all convergence criteria are met
        return all(convergence.values())

    def _synthesize_final_response(
        self, query: str, context: list, shared_key_findings: list, convergence: dict
    ) -> str:
        """Synthesize final comprehensive response from shared knowledge base and iterations."""
        if not context:
            return "No analysis performed."

        # Get the most recent analysis
        final_context = context[-1]
        final_decision = final_context.get("llm_decision", {})
        final_confidence = final_decision.get("confidence_level", 0)

        # Create comprehensive synthesis with KEY FINDINGS and proper final analysis
        synthesis = f"""
        CODEBASE ANALYSIS COMPLETE

        Query: {query}
        Iterations: {len(context)}
        Final Confidence Level: {final_confidence}/10
        Convergence Status: {convergence}

        KEY FINDINGS (Collaborative Knowledge Base):
        """

        # Add key findings for debugging and transparency
        if shared_key_findings:
            for i, finding in enumerate(shared_key_findings, 1):
                synthesis += f"{i}. {finding}\n"
        else:
            synthesis += "No key findings available.\n"

        # Generate comprehensive final analysis from all findings
        synthesis += """

        FINAL ANALYSIS:
        """

        if shared_key_findings:
            # Create a comprehensive technical report based on all key findings
            raw_analysis = self._generate_comprehensive_analysis(
                query, shared_key_findings, context
            )
            synthesis += raw_analysis
        else:
            raw_analysis = "Unable to perform comprehensive analysis due to insufficient findings."
            synthesis += raw_analysis

        synthesis += """

        EXECUTION SUMMARY:
        """

        # Add execution summary
        for ctx in context:
            iteration = ctx["iteration"]
            tool_results = ctx.get("tool_results", [])
            llm_decision = ctx.get("llm_decision", {})

            synthesis += f"\n--- Iteration {iteration} ---\n"
            synthesis += f"Commands executed: {len(tool_results)}\n"
            if tool_results:
                for result in tool_results:
                    status = "✓" if result["success"] else "✗"
                    synthesis += f"  {status} {result['command']}\n"
            synthesis += f"Confidence: {llm_decision.get('confidence_level', 'N/A')}\n"

            # Show knowledge base growth
            kb_size = len(llm_decision.get("key_findings", []))
            synthesis += f"Knowledge base size: {kb_size} findings\n"

        self.logger.info(f"Synthesized Analysis Diagnostic Output:\n{synthesis}")

        return raw_analysis

    def _generate_comprehensive_analysis(
        self, query: str, key_findings: list, context: list
    ) -> str:
        """Generate a comprehensive technical analysis report from complete analysis context."""
        try:
            # Build a comprehensive prompt using ALL available information
            synthesis_prompt = f"""
            Based on the complete codebase analysis process, generate a comprehensive technical report that answers the user's query: "{query}"

            === ANALYSIS CONTEXT ===

            Key Findings Summary:
            """

            for i, finding in enumerate(key_findings, 1):
                synthesis_prompt += f"{i}. {finding}\n"

            synthesis_prompt += "\n=== DETAILED ANALYSIS ITERATIONS ===\n"

            # Include analysis from each iteration for richer context
            for ctx in context:
                iteration = ctx.get("iteration", "Unknown")
                llm_decision = ctx.get("llm_decision", {})
                tool_results = ctx.get("tool_results", [])

                synthesis_prompt += f"\nIteration {iteration}:\n"

                # Add shell command insights
                if tool_results:
                    synthesis_prompt += "Commands executed and key discoveries:\n"
                    for result in tool_results:
                        if result.get("success") and result.get("stdout"):
                            # Include relevant command output (truncated)
                            stdout_sample = result["stdout"][:500]
                            synthesis_prompt += (
                                f"- {result['command']}: {stdout_sample}...\n"
                            )

                # Add LLM analysis from this iteration
                current_analysis = llm_decision.get("current_analysis", "")
                if current_analysis:
                    synthesis_prompt += f"Analysis insights: {current_analysis}\n"

                # Add focus areas
                focus_areas = llm_decision.get("next_focus_areas", "")
                if focus_areas:
                    synthesis_prompt += f"Focus areas identified: {focus_areas}\n"

            synthesis_prompt += """

            === SYNTHESIS REQUIREMENTS ===
            Create a comprehensive technical report that:
            1. Directly answers the user's query with specific technical details
            2. Synthesizes information from all iterations into coherent sections
            3. Provides concrete examples from the actual codebase analysis
            4. Explains component relationships and architectural patterns
            5. Includes specific file paths, class names, method signatures discovered
            6. Identifies key integration points and technical patterns
            7. Highlights important technical considerations for implementation

            Format as a clear, actionable technical report that an engineer could use immediately.
            Focus on technical substance, not process meta-information.
            """

            # Use the LLM to generate comprehensive analysis
            import asyncio

            async def generate_synthesis():
                result = await self._agent.run(task=synthesis_prompt)
                return extract_text_from_autogen_response(result)

            comprehensive_analysis = asyncio.run(generate_synthesis())

            if comprehensive_analysis and len(comprehensive_analysis.strip()) > 50:
                return comprehensive_analysis.strip()
            else:
                # If LLM synthesis fails, return basic information without fake intelligence
                return "LLM synthesis failed. Raw key findings:\n" + "\n".join(
                    f"- {finding}" for finding in key_findings
                )

        except Exception as e:
            self.logger.warning(f"Failed to generate comprehensive analysis: {e}")
            return (
                f"Analysis synthesis failed due to error: {e}\nRaw key findings:\n"
                + "\n".join(f"- {finding}" for finding in key_findings)
            )

    def _get_timestamp(self) -> str:
        """Get current timestamp for logging."""
        import datetime

        return datetime.datetime.now().isoformat()

    def _extract_json_from_response(self, response_text: str) -> str:
        """Extract JSON content from LLM response, handling markdown code blocks."""
        import re

        # Try to find JSON within markdown code blocks
        json_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        matches = re.findall(json_pattern, response_text, re.DOTALL)

        if matches:
            # Use the first JSON block found
            json_content = matches[0].strip()
            self.logger.debug(f"Extracted JSON from markdown: {json_content[:200]}...")
            return json_content

        # If no markdown blocks, check if the response starts/ends with braces
        stripped = response_text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            self.logger.debug("Found JSON-like content without markdown")
            return stripped

        # Last resort: try to find JSON pattern in the text
        json_like_pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
        json_matches = re.findall(json_like_pattern, response_text, re.DOTALL)

        if json_matches:
            # Try to find the most complete JSON (longest match)
            longest_match = max(json_matches, key=len)
            self.logger.debug(f"Found JSON-like pattern: {longest_match[:200]}...")
            return longest_match

        # If no JSON found, return original text and let JSON parser fail
        self.logger.warning("No JSON pattern found in response")
        return response_text

    @property
    def agent(self) -> AssistantAgent:
        """Get the underlying AutoGen agent."""
        return self._agent
