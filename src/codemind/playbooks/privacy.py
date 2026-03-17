"""
Data Privacy and Redaction Gateway

Provides a zero-dependency RedactionService that uses Regex templates to detect
and redact Personally Identifiable Information (PII) and application secrets
(API keys, AWS keys, etc.) before sending text to external LLMs.
"""
import re
from collections import OrderedDict

class RedactionService:
    def __init__(self):
        # Ordered dict: IP_ADDRESS before EMAIL_ADDRESS to avoid false positives
        # on connection strings like "user:pass@10.0.1.5" where the email regex
        # would otherwise consume the IP as part of a domain.
        self.patterns = OrderedDict([
            # --- Network & Infrastructure ---
            ("IP_ADDRESS", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),

            # --- PII ---
            # Email: TLD must be >= 2 alphabetic chars so "user@10.0.1.5" is NOT matched
            ("EMAIL_ADDRESS", r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})*"),
            ("PHONE_NUMBER", r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
            ("SSN", r"\b\d{3}-\d{2}-\d{4}\b"),
            ("CREDIT_CARD", r"\b(?:\d{4}[ -]?){3}\d{4}\b"),

            # --- Application Secrets ---
            ("AWS_KEY", r"(?i)(AKIA[0-9A-Z]{16})|(aws_access_key_id\s*=\s*[a-zA-Z0-9]{20})"),
            ("JWT_TOKEN", r"eyJ[a-zA-Z0-9_=]+\.[a-zA-Z0-9_=]+\.[a-zA-Z0-9_\-\+=]+"),
            ("PRIVATE_KEY", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),

            # --- Connection String Passwords ---
            # Matches password in JDBC/URI style: ://user:PASSWORD@host
            ("CONNECTION_PASSWORD", r"(?<=:\/\/[^:]{1,40}:)[^@\s]{4,}(?=@)"),
        ])
        
        # API Keys require slightly more nuanced matching to keep context
        self.api_key_regex = r"(?i)((api_key|apikey|secret_key|secretkey|access_token|bearer_token|auth_token|private_key|client_secret)\s*[:=]\s*['\"]?)([a-zA-Z0-9_\-\.]{16,})(['\"]?)"

    def mask(self, text: str) -> str:
        """
        Scrub sensitive PII and secrets from the text.
        """
        if not text:
            return text
            
        masked_text = text
        
        # 1. Standard pattern matching (order matters — IP before Email)
        for entity_name, pattern in self.patterns.items():
            masked_text = re.sub(pattern, f"<{entity_name}>", masked_text)
            
        # 2. Specific key matching (preserves the prefix so context is retained)
        # e.g., 'api_key: abcdef...' -> 'api_key: <API_KEY>'
        masked_text = re.sub(
            self.api_key_regex, 
            r"\g<1><API_KEY>\g<4>", 
            masked_text
        )
        
        return masked_text

# Global singleton instance
privacy_filter = RedactionService()
