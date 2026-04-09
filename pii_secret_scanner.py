#!/usr/bin/env python3
"""
Native Enterprise PII & Secret Scanner and Anonymizer.

This is a single-file utility that scans an entire codebase directory for both:
1. PII (Personally Identifiable Information) natively via pure spaCy NER (Names, Orgs, Locations).
2. Secrets & Regex PII (AWS Keys, GitHub Tokens, Emails, Phone Numbers) via compiled regular expressions.

Dual Purpose:
1. Directory Scanner: Recursively scans a codebase for exposed Secrets and PII.
2. AI Guardrail Proxy: Provides reversible anonymization (masking/unmasking) for LLM prompts.

Prerequisites: None (Uses purely standard Python 3 `re` and `os` libraries)

Usage Options:
    python3 pii_secret_scanner.py /path/to/codebase
    python3 pii_secret_scanner.py --demo-anonymizer
"""

import os
import re
import sys
import argparse
from pathlib import Path

# Folders and file extensions to ignore
IGNORE_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".idea", "coverage", "vendor", "target", "out", ".next"}
IGNORE_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Gemfile.lock", "poetry.lock"}

# Whitelist of actual source code and config extensions to scan
ALLOWED_EXTS = {
    # Application Logic
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".cs", ".cpp", ".c", ".h", ".rb", ".php", ".swift", ".kt", ".rs", ".m", ".scala",
    # Configuration & Env
    ".json", ".yml", ".yaml", ".xml", ".properties", ".ini", ".conf", ".env", ".toml", ".gradle",
    # Scripts
    ".sh", ".bash", ".zsh", ".bat", ".ps1", ".sql"
}


class NativeEnterpriseGuardrail:
    def __init__(self):
        print("[ℹ️ ] Initializing Native Enterprise Regex Engine (Zero Dependencies)...")
        self._compile_regex_patterns()

    def _compile_regex_patterns(self):
        """
        Comprehensive dictionary of deterministic PII and High-Risk Secrets.
        Because we have no ML dependencies, this relies entirely on highly rigid regular expressions.
        """
        patterns = {
            # --- Traditional Deterministic PII ---
            "EMAIL_ADDRESS": r"(?i)\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}\b",
            "CREDIT_CARD": r"\b(?:\d[ -]*?){13,16}\b",
            "IP_ADDRESS": r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b",
            "US_SSN": r"\b\d{3}-\d{2}-\d{4}\b",
            "PHONE_NUMBER": r"\+?\d{1,3}?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
            
            # --- Cloud & Infra Secrets ---
            "AWS_ACCESS_KEY": r"(?i)\bAKIA[0-9A-Z]{16}\b",
            "AWS_SECRET_KEY": r"(?i)aws_secret_access_key\s*={0,1}\s*['\"]*[a-zA-Z0-9/+=]{40}['\"]*",
            "GCP_API_KEY": r"\bAIza[0-9A-Za-z\\-_]{35}\b",
            "GCP_OAUTH_TOKEN": r"\bya29\.[0-9A-Za-z\\-_]+\b",
            
            # --- Version Control & CI/CD ---
            "GITHUB_TOKEN": r"(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}",
            "GITLAB_TOKEN": r"\bglpat-[0-9a-zA-Z\\-_]{20}\b",
            "HEROKU_API_KEY": r"(?i)heroku_api_key\s*=\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            
            # --- Communications & SaaS ---
            "SLACK_TOKEN": r"xox[baprs]-[0-9]{12}-[0-9]{12}-[a-zA-Z0-9]{24}",
            "SLACK_WEBHOOK": r"hooks\.slack\.com/services/T[a-zA-Z0-9_]{8}/B[a-zA-Z0-9_]{8}/[a-zA-Z0-9_]{24}",
            "SENDGRID_KEY": r"SG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}",
            "MAILCHIMP_KEY": r"[0-9a-f]{32}-us[0-9]{1,2}",
            "STRIPE_KEY": r"(sk|rk)_(test|live)_[0-9a-zA-Z]{24}",
            "TWILIO_KEY": r"SK[0-9a-fA-F]{32}",
            
            # --- Databases ---
            "DB_CONNECTION_STRING": r"(postgres|mysql|mongodb\+srv|redis|postgresql):\/\/[^:\s]+:[^@\s]+@[^\s]+\.[a-z]{2,5}",
            
            # --- Cryptography & Generic ---
            "PRIVATE_KEY_BLOCK": r"-----BEGIN (RSA|OPENSSH|EC|PGP|DSA) PRIVATE KEY-----",
            "GENERIC_PASSWORD": r"(?i)(password|passwd|pwd|secret|api_key|apikey|token|auth|bearer)\s*[:=]\s*['\"]([^'\"]{8,})['\"]",
            "JWT_TOKEN": r"eyJ[a-zA-Z0-9_-]{5,}\.eyJ[a-zA-Z0-9_-]{5,}\.[a-zA-Z0-9_-]{10,}"
        }
        
        self.compiled_patterns = {}
        for name, pattern in patterns.items():
            self.compiled_patterns[name] = re.compile(pattern)


    # =========================================================================
    # MODULE 1: AI GUARDRAIL (REVERSIBLE ANONYMIZATION)
    # =========================================================================
    def anonymize_prompt(self, text: str) -> tuple[str, dict]:
        """
        Scans LLM prompt text, replaces sensitive real data with <TAG_ID>, 
        and returns the SAFE text alongside a tracking dictionary vault.
        """
        safe_text = text
        mapping_vault = {}
        counter = 1
        
        for entity_type, pattern in self.compiled_patterns.items():
            # Find all unique occurrences to prevent duplicated tags logic recursion
            matches = set(m.group(0) for m in pattern.finditer(safe_text))
            
            for original_value in matches:
                placeholder = f"<{entity_type}_{counter}>"
                safe_text = safe_text.replace(original_value, placeholder)
                mapping_vault[placeholder] = original_value
                counter += 1
                
        return safe_text, mapping_vault

    def deanonymize_response(self, llm_response: str, mapping_vault: dict) -> str:
        """Restores the original confidential data back into the LLM's response."""
        restored_response = llm_response
        for placeholder, original_value in mapping_vault.items():
            restored_response = restored_response.replace(placeholder, original_value)
        return restored_response


    # =========================================================================
    # MODULE 2: CODEBASE VULNERABILITY SCANNER
    # =========================================================================            
    def scan_file(self, filepath: Path):
        try:
            # Skip massive blob files
            if filepath.stat().st_size > 2_000_000:
                return
                
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            if not content.strip():
                return
                
            found_issues = []
            
            for name, pattern in self.compiled_patterns.items():
                for match in pattern.finditer(content):
                    snippet = content[max(0, match.start() - 15) : min(len(content), match.end() + 15)].replace('\n', ' ').strip()
                    found_issues.append({
                        "type": name,
                        "value": match.group(0).strip(),
                        "snippet": snippet,
                        "start": match.start()
                    })
            
            if found_issues:
                found_issues.sort(key=lambda x: x['start'])
                print(f"\n[🚨] Found vulnerabilities in: {filepath}")
                for issue in found_issues:
                    print(f"   ↳ 🔴 Type: {issue['type']}")
                    print(f"      Matched String: {issue['value']}")
                    print(f"      Context: \"...{issue['snippet']}...\"\n")

        except Exception as e:
            print(f"[⚠️ ] Failed to read {filepath}: {e}")

    def scan_directory(self, target_folder: str):
        root_path = Path(target_folder).resolve()
        
        if not root_path.exists() or not root_path.is_dir():
            print(f"❌ Error: Directory '{root_path}' does not exist.")
            sys.exit(1)
            
        print(f"\n[🚀] Beginning comprehensive Native Regex Codebase Scan on: {root_path}")
        print("---------------------------------------------------------")
        
        scanned_count = 0
        for dirpath, dirnames, filenames in os.walk(root_path):
            # In-Place Filter ignores
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            
            for file in filenames:
                file_path = Path(dirpath) / file
                
                # Filter by File Name and Allow-Listed Extensions
                if file in IGNORE_FILES:
                    continue
                if file_path.suffix.lower() not in ALLOWED_EXTS and not file_path.name.startswith(".env"):
                    continue
                    
                scanned_count += 1
                self.scan_file(file_path)
                
        print("---------------------------------------------------------")
        print(f"[✅] Scan Complete. Processed {scanned_count} files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool for Scanning Codebases and Guarding LLM Prompts against PII/Secret Leaks.")
    parser.add_argument("folder", nargs='?', help="The root folder/directory to scan.")
    parser.add_argument("--demo-anonymizer", action="store_true", help="Run a quick demo of the LLM prompt anonymizer functionality.")
    
    args = parser.parse_args()
    
    engine = NativeEnterpriseGuardrail()
    
    if args.demo_anonymizer:
        print("\n=== RUNNING GUARDRAIL ANONYMIZER DEMO ===")
        user_prompt = "Hello LLM, please examine this connection string: postgres://admin:superSecretPWD99@db.company.com/prod and send the output strictly to my private email roshin@enterprise.com"
        print(f"\n1️⃣ Original Raw Prompt: \n   {user_prompt}")
        
        safe_payload, vault = engine.anonymize_prompt(user_prompt)
        print(f"\n2️⃣ Extracted Vault Contents: \n   {vault}")
        print(f"\n3️⃣ SAFE Payload (What the LLM Actually Sees): \n   {safe_payload}")
        
        mock_response = f"I have successfully analyzed <DB_CONNECTION_STRING_1>. I will forward the resulting report to <EMAIL_ADDRESS_2> as requested."
        print(f"\n4️⃣ Mock Raw LLM Output: \n   {mock_response}")
        
        restored = engine.deanonymize_response(mock_response, vault)
        print(f"\n5️⃣ Restored Final Output (What the User Sees): \n   {restored}\n")
        
    elif args.folder:
        engine.scan_directory(args.folder)
    else:
        parser.print_help()
