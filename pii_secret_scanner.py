#!/usr/bin/env python3
"""
Microsoft Presidio ML-Based PII & Secret Scanner.

This is a single-file utility that scans an entire codebase directory for both:
1. PII (Personally Identifiable Information) natively via Microsoft's Presidio ML (spaCy) NLP models.
2. Secrets (AWS Keys, GitHub Tokens, Private Keys, Passwords) via injected custom patterns.

Prerequisites:
    pip install presidio-analyzer spacy
    # (Assuming you already have the local en_core_web_md model downloaded)

Usage:
    python3 pii_secret_scanner.py /path/to/codebase
"""

import os
import sys
import argparse
from pathlib import Path

# Microsoft Presidio Imports
try:
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
    from presidio_analyzer.nlp_engine import NlpEngineProvider
except ImportError:
    print("❌ Missing Microsoft Presidio Library. Run:\n  pip install presidio-analyzer spacy")
    sys.exit(1)

# Folders and file extensions to ignore to prevent scanning binaries or locked files
IGNORE_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".idea", "coverage"}
IGNORE_EXTS = {".png", ".jpg", ".jpeg", ".pdf", ".zip", ".tar", ".gz", ".mp4", ".woff", ".woff2", ".ttf", ".eot", ".pyc", ".db", ".sqlite3"}

class PiiSecretScanner:
    def __init__(self):
        print("[ℹ️ ] Initializing Microsoft Presidio ML Analyzer Engine with 'en_core_web_md'...")
        
        # If your local model is just a folder (not installed via pip), you can provide its absolute path here.
        # It defaults to 'en_core_web_md' standard python package, but respects the ENV variable.
        model_path_or_name = os.getenv("SPACY_MODEL_PATH", "en_core_web_md")
        
        # Configure Presidio to use the local spaCy model
        configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model_path_or_name}],
        }
        provider = NlpEngineProvider(nlp_configuration=configuration)
        nlp_engine = provider.create_engine()
        
        # Initialize the Analyzer engine with the explicit NLP config
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        self._inject_secret_recognizers()

    def _inject_secret_recognizers(self):
        """
        Presidio is amazing at NLP PII (Names, Locations) but doesn't do deep Secrets by default.
        We inject an exhaustive catalog of high-risk security secrets into its ML recognition engine here.
        """
        secret_patterns = [
            # --- Cloud & Infra ---
            Pattern("AWS Access Key ID", r"(?i)\bAKIA[0-9A-Z]{16}\b", 1.0),
            Pattern("AWS Secret Access Key", r"(?i)aws_secret_access_key\s*={0,1}\s*['\"]*[a-zA-Z0-9/+=]{40}['\"]*", 1.0),
            Pattern("Google Cloud API Key", r"\bAIza[0-9A-Za-z\\-_]{35}\b", 1.0),
            Pattern("Google OAuth Access Token", r"\bya29\.[0-9A-Za-z\\-_]+\b", 1.0),
            
            # --- Version Control & CI/CD ---
            Pattern("GitHub Token", r"(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}", 1.0),
            Pattern("GitLab Token", r"\bglpat-[0-9a-zA-Z\\-_]{20}\b", 1.0),
            Pattern("Heroku API Key", r"(?i)heroku_api_key\s*=\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", 0.8),
            
            # --- Communications & SaaS ---
            Pattern("Slack Token", r"xox[baprs]-[0-9]{12}-[0-9]{12}-[a-zA-Z0-9]{24}", 1.0),
            Pattern("Slack Webhook", r"hooks\.slack\.com/services/T[a-zA-Z0-9_]{8}/B[a-zA-Z0-9_]{8}/[a-zA-Z0-9_]{24}", 1.0),
            Pattern("SendGrid API Key", r"SG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}", 1.0),
            Pattern("Mailchimp API Key", r"[0-9a-f]{32}-us[0-9]{1,2}", 1.0),
            Pattern("Stripe API Key", r"(sk|rk)_(test|live)_[0-9a-zA-Z]{24}", 1.0),
            Pattern("Twilio API Key", r"SK[0-9a-fA-F]{32}", 1.0),
            
            # --- Databases ---
            Pattern("Database Connection String URI", r"(postgres|mysql|mongodb\+srv|redis|postgresql):\/\/[^:\s]+:[^@\s]+@[^\s]+\.[a-z]{2,5}", 0.9),
            
            # --- Cryptography & Generic ---
            Pattern("Private Key Block", r"-----BEGIN (RSA|OPENSSH|EC|PGP|DSA) PRIVATE KEY-----", 1.0),
            Pattern("Generic Password/Secret", r"(?i)(password|passwd|pwd|secret|api_key|apikey|token|auth|bearer)\s*[:=]\s*['\"]([^'\"]{8,})['\"]", 0.6),
            Pattern("JSON Web Token", r"eyJ[a-zA-Z0-9_-]{5,}\.eyJ[a-zA-Z0-9_-]{5,}\.[a-zA-Z0-9_-]{10,}", 0.8),
        ]
        
        # We group all of these under a single high-level entity called "CODE_SECRET"
        secret_recognizer = PatternRecognizer(supported_entity="CODE_SECRET", patterns=secret_patterns)
        
        # Register it into the Microsoft ML Registry
        self.analyzer.registry.add_recognizer(secret_recognizer)
        print("[ℹ️ ] Custom Secret / Pattern Recognizers Injected Successfully.")

    def scan_file(self, filepath: Path):
        """Read a single file and execute the ML detection against its text."""
        try:
            # We enforce utf-8, ignoring errors on weird binary blobs that bypassed filters
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            if not content.strip():
                return
                
            # Run the ML Analysis
            # language="en" triggers the en_core_web_lg model
            results = self.analyzer.analyze(text=content, language='en')
            
            # Sort results by start position
            results_sorted = sorted(results, key=lambda x: x.start)
            
            found_issues = []
            for result in results_sorted:
                # We lower the threshold to 0.4 to catch ALL "potential/probable" entries
                # (Codebases lack standard sentence structures, so ML NLP confidences like "PERSON" often score lower).
                if result.score >= 0.4:
                    snippet = content[max(0, result.start - 10) : min(len(content), result.end + 10)]
                    snippet = snippet.replace('\n', ' ').strip()
                    
                    found_issues.append({
                        "entity": result.entity_type,
                        "confidence": result.score,
                        "snippet": snippet
                    })
                    
            if found_issues:
                print(f"\n[🚨] Found vulnerabilities in: {filepath}")
                for issue in found_issues:
                    print(f"   ↳ 🔴 Type: {issue['entity']} (Confidence: {issue['confidence']:.2f})")
                    print(f"      Context: \"...{issue['snippet']}...\"")

        except Exception as e:
            print(f"[⚠️ ] Failed to read file {filepath}: {e}")

    def scan_directory(self, target_folder: str):
        """Walk the directory recursively and scan all eligible files."""
        root_path = Path(target_folder).resolve()
        
        if not root_path.exists() or not root_path.is_dir():
            print(f"❌ Error: Directory '{root_path}' does not exist.")
            sys.exit(1)
            
        print(f"\n[🚀] Beginning comprehensive PII/Secret Scan on: {root_path}")
        print("---------------------------------------------------------")
        
        scanned_count = 0
        for dirpath, dirnames, filenames in os.walk(root_path):
            # In-place directory ignore filter
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            
            for file in filenames:
                file_path = Path(dirpath) / file
                
                if file_path.suffix.lower() in IGNORE_EXTS:
                    continue
                    
                scanned_count += 1
                self.scan_file(file_path)
                
        print("---------------------------------------------------------")
        print(f"[✅] Scan Complete. Processed {scanned_count} files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan a codebase for Secrets and PII using Microsoft Presidio.")
    parser.add_argument("folder", help="The root folder/directory to scan")
    
    args = parser.parse_args()
    
    scanner = PiiSecretScanner()
    scanner.scan_directory(args.folder)
