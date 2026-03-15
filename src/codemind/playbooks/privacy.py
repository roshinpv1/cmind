"""
Data Privacy and Redaction Gateway

Provides a RedactionService that uses Microsoft Presidio to detect
and redact Personally Identifiable Information (PII) and application secrets
(API keys, AWS keys, etc.) before sending text to external LLMs.
"""
import re
from typing import List, Dict, Tuple
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_anonymizer import AnonymizerEngine

class RedactionService:
    def __init__(self):
        # Initialize Presidio engines
        self.analyzer = AnalyzerEngine()
        self.anonymizer = AnonymizerEngine()
        
        # Add custom recognizers for developer secrets
        self._add_secret_recognizers()

    def _add_secret_recognizers(self):
        # 1. AWS Keys
        aws_pattern = Pattern(
            name="aws_key_pattern",
            regex=r"(AKIA[0-9A-Z]{16})|((?i)aws_access_key_id\s*=\s*[a-zA-Z0-9]{20})",
            score=0.8
        )
        aws_recognizer = PatternRecognizer(
            supported_entity="AWS_KEY",
            patterns=[aws_pattern]
        )
        self.analyzer.registry.add_recognizer(aws_recognizer)

        # 2. Generic API Keys (high entropy or specific keywords)
        api_pattern = Pattern(
            name="generic_api_key",
            regex=r"(?i)(api_key|apikey|secret_key|secretkey|access_token|bearer_token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{16,})['\"]?",
            score=0.7
        )
        api_recognizer = PatternRecognizer(
            supported_entity="API_KEY",
            patterns=[api_pattern]
        )
        self.analyzer.registry.add_recognizer(api_recognizer)
        
        # 3. JWT Tokens
        jwt_pattern = Pattern(
            name="jwt_token",
            regex=r"eyJ[a-zA-Z0-9_=]+\.[a-zA-Z0-9_=]+\.[a-zA-Z0-9_\-\+=]+",
            score=0.85
        )
        jwt_recognizer = PatternRecognizer(
            supported_entity="JWT_TOKEN",
            patterns=[jwt_pattern]
        )
        self.analyzer.registry.add_recognizer(jwt_recognizer)

    def mask(self, text: str) -> str:
        """
        Scrub sensitive PII and secrets from the text.
        """
        if not text:
            return text
            
        # Supported entities to redact (add or remove based on needs)
        entities = [
            "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "IP_ADDRESS", 
            "CREDIT_CARD", "CRYPTO", "IBAN_CODE", "US_SSN", "US_PASSPORT",
            "AWS_KEY", "API_KEY", "JWT_TOKEN"
        ]
        
        # Analyze the text
        results = self.analyzer.analyze(
            text=text,
            entities=entities,
            language='en'
        )
        
        # Anonymize (Mask)
        anonymized_result = self.anonymizer.anonymize(
            text=text,
            analyzer_results=results
        )
        
        return anonymized_result.text

# Global singleton instance
privacy_filter = RedactionService()
