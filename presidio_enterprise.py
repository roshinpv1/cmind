#!/usr/bin/env python3
"""
Enterprise Microsoft Presidio Guardrail & Scanner.

This script fuses Microsoft Presidio's ML Analyzer and Anonymizer into a single,
self-contained Enterprise utility that strictly uses local/offline NLP models.

Prerequisites:
    pip install presidio-analyzer presidio-anonymizer spacy

Configuration:
    To use your locally provided SpaCy model, set the environment variable:
    export SPACY_MODEL_PATH="/path/to/en_core_web_md"

Usage Options:
    python3 presidio_enterprise.py /path/to/codebase
    python3 presidio_enterprise.py --demo-anonymizer
"""

import os
import re
import sys
import argparse
from pathlib import Path

# --- Enterprise Import Verification ---
try:
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
except ImportError:
    print("❌ Missing Presidio Libraries. Run:\n  pip install presidio-analyzer presidio-anonymizer spacy")
    sys.exit(1)


# Folders and file extensions to ignore
IGNORE_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".idea", "coverage", "vendor", "target", "out", ".next"}
IGNORE_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Gemfile.lock", "poetry.lock"}

# Whitelist of actual source code and config extensions to scan
ALLOWED_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".cs", ".cpp", ".c", ".h", ".rb", ".php", ".swift", ".kt", ".rs", ".m", ".scala",
    ".json", ".yml", ".yaml", ".xml", ".properties", ".ini", ".conf", ".env", ".toml", ".gradle",
    ".sh", ".bash", ".zsh", ".bat", ".ps1", ".sql"
}


class PresidioEnterpriseGuardrail:
    def __init__(self):
        print("[ℹ️ ] Initializing Microsoft Presidio Suite (Offline Enterprise Mode)...")
        
        # 1. Initialize Presidio Anonymizer (Masks Data)
        self.anonymizer = AnonymizerEngine()
        
        # 2. Configure Local Offline spaCy Engine
        model_path = os.getenv("SPACY_MODEL_PATH", "en_core_web_md")
        configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model_path}],
        }
        
        try:
            print(f"[ℹ️ ] Loading NLP Model from: {model_path}")
            provider = NlpEngineProvider(nlp_configuration=configuration)
            nlp_engine = provider.create_engine()
            self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        except Exception as e:
            print(f"\n❌ Failed to load NLP Model at '{model_path}'.")
            print("Ensure the path is correct or run: python3 -m spacy download en_core_web_md")
            print(f"Error: {e}")
            sys.exit(1)
            
        self._inject_secret_recognizers()

    def _inject_secret_recognizers(self):
        """Inject exhaustive custom high-risk security secrets into the Presidio ML Registry."""
        secret_patterns = [
            Pattern("AWS Access Key", r"(?i)\bAKIA[0-9A-Z]{16}\b", 1.0),
            Pattern("AWS Secret Key", r"(?i)aws_secret_access_key\s*={0,1}\s*['\"]*[a-zA-Z0-9/+=]{40}['\"]*", 1.0),
            Pattern("GCP API Key", r"\bAIza[0-9A-Za-z\\-_]{35}\b", 1.0),
            Pattern("GitHub Token", r"(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}", 1.0),
            Pattern("GitLab Token", r"\bglpat-[0-9a-zA-Z\\-_]{20}\b", 1.0),
            Pattern("Slack Token", r"xox[baprs]-[0-9]{12}-[0-9]{12}-[a-zA-Z0-9]{24}", 1.0),
            Pattern("Stripe Key", r"(sk|rk)_(test|live)_[0-9a-zA-Z]{24}", 1.0),
            Pattern("DB Connection URI", r"(postgres|mysql|mongodb\+srv|redis|postgresql):\/\/[^:\s]+:[^@\s]+@[^\s]+\.[a-z]{2,5}", 0.9),
            Pattern("Private Key", r"-----BEGIN (RSA|OPENSSH|EC|PGP|DSA) PRIVATE KEY-----", 1.0),
            Pattern("JWT Token", r"eyJ[a-zA-Z0-9_-]{5,}\.eyJ[a-zA-Z0-9_-]{5,}\.[a-zA-Z0-9_-]{10,}", 0.8),
            Pattern("Generic Password", r"(?i)(password|passwd|pwd|secret|api_key|apikey|token|auth|bearer)\s*[:=]\s*['\"]([^'\"]{8,})['\"]", 0.6),
        ]
        
        recognizer = PatternRecognizer(supported_entity="CODE_SECRET", patterns=secret_patterns)
        self.analyzer.registry.add_recognizer(recognizer)

    def _looks_like_code(self, text: str) -> bool:
        """Filters ML false positives that are standard code syntax."""
        text = text.strip()
        if text.lower() in {"import", "class", "public", "private", "return", "function", "def", "const", "let", "var", "string", "int", "boolean", "logger"}: return True
        if re.match(r'^[a-z]+[A-Z][a-zA-Z0-9]*$', text): return True 
        if re.match(r'^[A-Z][a-zA-Z0-9]*$', text): return True 
        if re.match(r'^[a-z0-9_]+$', text) and '_' in text: return True 
        if re.match(r'^[A-Z0-9]+_[A-Z0-9_]+$', text): return True 
        return False

    # =========================================================================
    # CORE 1: AI GUARDRAIL (PRESIDIO REVERSIBLE ANONYMIZATION)
    # =========================================================================
    def anonymize_prompt(self, text: str) -> tuple[str, dict]:
        """
        Uses Presidio Analyzer to detect entities contextually, and builds a 
        Vault mapping for reversible deanonymization without leaking data to LLMs.
        """
        # Execute ML Analysis
        results = self.analyzer.analyze(text=text, language='en')
        
        # Filter (Confidence >= 0.4, exclude code false-positives)
        filtered_results = []
        for r in results:
            if r.score >= 0.4:
                original_value = text[r.start:r.end]
                if not self._looks_like_code(original_value):
                    filtered_results.append(r)
                    
        # Sort from end to start to safely replace text without offset collisions
        filtered_results.sort(key=lambda x: x.start, reverse=True)
        
        safe_text = text
        mapping_vault = {}
        
        for idx, r in enumerate(filtered_results):
            original_value = text[r.start:r.end]
            # Create a tag like <PERSON_1>, <CODE_SECRET_2>
            placeholder = f"<{r.entity_type}_{len(filtered_results)-idx}>"
            
            # String replacement using precise ML boundaries
            safe_text = safe_text[:r.start] + placeholder + safe_text[r.end:]
            mapping_vault[placeholder] = original_value
            
        return safe_text, mapping_vault

    def deanonymize_response(self, llm_response: str, mapping_vault: dict) -> str:
        """Restores original data from the Vault back into the LLM's response."""
        restored = llm_response
        for placeholder, original in mapping_vault.items():
            restored = restored.replace(placeholder, original)
        return restored

    # =========================================================================
    # CORE 2: VULNERABILITY SCANNER
    # =========================================================================            
    def scan_file(self, filepath: Path):
        try:
            if filepath.stat().st_size > 2_000_000:
                return
                
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            if not content.strip():
                return
                
            results = self.analyzer.analyze(text=content, language='en')
            
            # Only keep results >= 0.4 confidence, and not false-positive code blocks
            valid_results = [r for r in results if r.score >= 0.4 and not self._looks_like_code(content[r.start:r.end])]
            
            if valid_results:
                valid_results.sort(key=lambda x: x.start)
                print(f"\n[🚨] Found vulnerabilities in: {filepath}")
                for issue in valid_results:
                    entity_text = content[issue.start:issue.end].strip()
                    snippet = content[max(0, issue.start - 15) : min(len(content), issue.end + 15)].replace('\n', ' ').strip()
                    
                    print(f"   ↳ 🔴 Type: {issue.entity_type} (Confidence: {issue.score:.2f})")
                    print(f"      Matched String: {entity_text}")
                    print(f"      Context: \"...{snippet}...\"\n")

        except Exception as e:
            print(f"[⚠️ ] Failed to read {filepath}: {e}")

    def scan_directory(self, target_folder: str):
        root_path = Path(target_folder).resolve()
        
        if not root_path.exists() or not root_path.is_dir():
            print(f"❌ Error: Directory '{root_path}' does not exist.")
            sys.exit(1)
            
        print(f"\n[🚀] Beginning comprehensive Presidio ML Codebase Scan on: {root_path}")
        print("---------------------------------------------------------")
        
        scanned_count = 0
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            
            for file in filenames:
                file_path = Path(dirpath) / file
                
                if file in IGNORE_FILES: continue
                if file_path.suffix.lower() not in ALLOWED_EXTS and not file_path.name.startswith(".env"): continue
                    
                scanned_count += 1
                self.scan_file(file_path)
                
        print("---------------------------------------------------------")
        print(f"[✅] Scan Complete. Processed {scanned_count} files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Microsoft Presidio Enterprise Scanner & Anonymizer Guardrail.")
    parser.add_argument("folder", nargs='?', help="The root folder/directory to codebase scan.")
    parser.add_argument("--demo-anonymizer", action="store_true", help="Run a quick demo of the Presidio LLM Guardrail.")
    
    args = parser.parse_args()
    
    # Initialize Engine
    engine = PresidioEnterpriseGuardrail()
    
    if args.demo_anonymizer:
        print("\n=== RUNNING PRESIDIO GUARDRAIL ANONYMIZER DEMO ===")
        user_prompt = "Hello LLM, check this code logic connecting to mongodb+srv://roshin:TopSecretPass99M@cluster0.abc.mongodb.net. Notify Satya Nadella at satya@microsoft.com"
        print(f"\n1️⃣ Original Raw Prompt: \n   {user_prompt}")
        
        safe_payload, vault = engine.anonymize_prompt(user_prompt)
        print(f"\n2️⃣ Extracted Vault Contents: \n   {vault}")
        print(f"\n3️⃣ SAFE Payload (What the LLM Actually Sees): \n   {safe_payload}")
        
        mock_response = f"I evaluated <CODE_SECRET_2> logic. It looks secure. I will send the summary to <EMAIL_ADDRESS_1> as well as <PERSON_1>."
        print(f"\n4️⃣ Mock Raw LLM Output: \n   {mock_response}")
        
        restored = engine.deanonymize_response(mock_response, vault)
        print(f"\n5️⃣ Restored Final Output (What the User Sees): \n   {restored}\n")
        
    elif args.folder:
        engine.scan_directory(args.folder)
    else:
        parser.print_help()
