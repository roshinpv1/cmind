#!/usr/bin/env python3
"""
Pure spaCy ML-Based PII & Secret Scanner.

This is a single-file utility that scans an entire codebase directory for both:
1. PII (Personally Identifiable Information) natively via pure spaCy NER (Names, Orgs, Locations).
2. Secrets & Regex PII (AWS Keys, GitHub Tokens, Emails, Phone Numbers) via compiled regular expressions.

Prerequisites:
    pip install spacy
    python3 -m spacy download en_core_web_md

Usage:
    python3 pii_secret_scanner.py /path/to/codebase
"""

import os
import re
import sys
import argparse
from pathlib import Path

try:
    import spacy
except ImportError:
    print("❌ Missing spaCy Library. Run:\n  pip install spacy\n  python3 -m spacy download en_core_web_md")
    sys.exit(1)

# Folders and file extensions to ignore to prevent scanning binaries or locked files
IGNORE_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".idea", "coverage"}
IGNORE_EXTS = {".png", ".jpg", ".jpeg", ".pdf", ".zip", ".tar", ".gz", ".mp4", ".woff", ".woff2", ".ttf", ".eot", ".pyc", ".db", ".sqlite3"}

class SpaCyScanner:
    def __init__(self):
        print("[ℹ️ ] Initializing Pure spaCy ML Engine...")
        
        # Pulls from absolute disk path if set, otherwise defaults to the pip package name
        model_name = os.getenv("SPACY_MODEL_PATH", "en_core_web_md")
        
        try:
            self.nlp = spacy.load(model_name)
            # Increase max length to support reasonably sized code files without crashing spaCy
            self.nlp.max_length = 2_000_000 
        except Exception as e:
            print(f"❌ Failed to load spaCy model '{model_name}'. Did you download it?\nError: {e}")
            sys.exit(1)
            
        self._compile_regex_patterns()

    def _compile_regex_patterns(self):
        """
        Since pure spaCy focuses purely on NLP Entity Recognition (Names, Orgs, Locations),
        we must manually compile regex patterns for deterministic PII (Emails, Phones) and Deep Secrets.
        """
        patterns = {
            # --- Traditional Deterministic PII ---
            "Email Address": r"(?i)\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}\b",
            "Credit Card": r"\b(?:\d[ -]*?){13,16}\b",
            "IP Address": r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b",
            "US SSN": r"\b\d{3}-\d{2}-\d{4}\b",
            "Phone Number (Basic)": r"\+?\d{1,3}?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
            
            # --- Cloud & Infra Secrets ---
            "AWS Access Key ID": r"(?i)\bAKIA[0-9A-Z]{16}\b",
            "AWS Secret Access Key": r"(?i)aws_secret_access_key\s*={0,1}\s*['\"]*[a-zA-Z0-9/+=]{40}['\"]*",
            "Google Cloud API Key": r"\bAIza[0-9A-Za-z\\-_]{35}\b",
            "Google OAuth Access Token": r"\bya29\.[0-9A-Za-z\\-_]+\b",
            
            # --- Version Control & CI/CD ---
            "GitHub Token": r"(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}",
            "GitLab Token": r"\bglpat-[0-9a-zA-Z\\-_]{20}\b",
            "Heroku API Key": r"(?i)heroku_api_key\s*=\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            
            # --- Communications & SaaS ---
            "Slack Token": r"xox[baprs]-[0-9]{12}-[0-9]{12}-[a-zA-Z0-9]{24}",
            "Slack Webhook": r"hooks\.slack\.com/services/T[a-zA-Z0-9_]{8}/B[a-zA-Z0-9_]{8}/[a-zA-Z0-9_]{24}",
            "SendGrid API Key": r"SG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}",
            "Mailchimp API Key": r"[0-9a-f]{32}-us[0-9]{1,2}",
            "Stripe API Key": r"(sk|rk)_(test|live)_[0-9a-zA-Z]{24}",
            "Twilio API Key": r"SK[0-9a-fA-F]{32}",
            
            # --- Databases ---
            "Database Connection String URI": r"(postgres|mysql|mongodb\+srv|redis|postgresql):\/\/[^:\s]+:[^@\s]+@[^\s]+\.[a-z]{2,5}",
            
            # --- Cryptography & Generic ---
            "Private Key Block": r"-----BEGIN (RSA|OPENSSH|EC|PGP|DSA) PRIVATE KEY-----",
            "Generic Password/Secret": r"(?i)(password|passwd|pwd|secret|api_key|apikey|token|auth|bearer)\s*[:=]\s*['\"]([^'\"]{8,})['\"]",
            "JSON Web Token": r"eyJ[a-zA-Z0-9_-]{5,}\.eyJ[a-zA-Z0-9_-]{5,}\.[a-zA-Z0-9_-]{10,}"
        }
        
        self.compiled_patterns = {}
        for name, pattern in patterns.items():
            self.compiled_patterns[name] = re.compile(pattern)
            
    def scan_file(self, filepath: Path):
        try:
            # Skip massive blob files (e.g., >2MB) to prevent spaCy ML memory exhaustion
            if filepath.stat().st_size > 2_000_000:
                return
                
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            if not content.strip():
                return
                
            found_issues = []
            
            # 1. Pure SpaCy ML NER Scan (Implicit Context PII)
            doc = self.nlp(content)
            
            # Filter specifically for high-risk PII ML categories
            # PERSON = Names, ORG = Companies, GPE = Geopolitical Locations, LOC = Non-GPE locations
            risky_labels = {"PERSON", "ORG", "GPE", "LOC"}
            
            for ent in doc.ents:
                if ent.label_ in risky_labels:
                    snippet = content[max(0, ent.start_char - 15) : min(len(content), ent.end_char + 15)].replace('\n', ' ').strip()
                    found_issues.append({
                        "type": f"ML_PII ({ent.label_})",
                        "value": ent.text.strip(),
                        "snippet": snippet,
                        "start": ent.start_char
                    })
                    
            # 2. Regex Scan (Deterministic PII and Code Secrets)
            for name, pattern in self.compiled_patterns.items():
                for match in pattern.finditer(content):
                    snippet = content[max(0, match.start() - 15) : min(len(content), match.end() + 15)].replace('\n', ' ').strip()
                    found_issues.append({
                        "type": f"SECRET_OR_PII ({name})",
                        "value": match.group(0).strip(),
                        "snippet": snippet,
                        "start": match.start()
                    })
            
            if found_issues:
                # Sort logically by character position for pretty console output
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
            
        print(f"\n[🚀] Beginning comprehensive Pure spaCy Scan on: {root_path}")
        print("---------------------------------------------------------")
        
        scanned_count = 0
        for dirpath, dirnames, filenames in os.walk(root_path):
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
    parser = argparse.ArgumentParser(description="Scan a codebase for Secrets and PII using Pure spaCy ML.")
    parser.add_argument("folder", help="The root folder/directory to scan")
    
    args = parser.parse_args()
    
    scanner = SpaCyScanner()
    scanner.scan_directory(args.folder)
