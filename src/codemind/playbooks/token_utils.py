"""
Token estimation and chunking utilities.

Helps prevent context limit errors by:
- Estimating token count for text
- Splitting large inputs into chunks
- Merging chunked outputs
"""
import re
from typing import List, Optional


def estimate_tokens(text: str) -> int:
    """
    Count tokens in text using tiktoken (accurate) or char-based fallback.

    Delegates to the centralized token counter in codemind.llm.token_counter.

    Args:
        text: Input text

    Returns:
        Token count
    """
    from codemind.llm.token_counter import count_tokens
    return count_tokens(text)


def split_into_chunks(
    items: List[dict],
    max_tokens_per_chunk: int = 2000,
    overlap_tokens: int = 200
) -> List[List[dict]]:
    """
    Split code chunks into batches that fit within token limit.
    
    Args:
        items: List of code chunks (each has 'chunk_text' field)
        max_tokens_per_chunk: Max tokens per batch
        overlap_tokens: Overlap between batches for context
        
    Returns:
        List of batches, where each batch is a list of items
    """
    batches = []
    current_batch = []
    current_tokens = 0
    
    for item in items:
        text = item.get('chunk_text', '')
        item_tokens = estimate_tokens(text)
        
        # If this item alone exceeds limit, truncate it
        if item_tokens > max_tokens_per_chunk:
            # Add current batch if not empty
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            
            # Truncate large item
            truncated_text = text[:max_tokens_per_chunk * 3]  # ~3 chars per token
            truncated_item = {**item, 'chunk_text': truncated_text}
            batches.append([truncated_item])
            continue
        
        # Check if adding this item would exceed limit
        if current_tokens + item_tokens > max_tokens_per_chunk and current_batch:
            # Save current batch
            batches.append(current_batch)
            
            # Start new batch with overlap (last item from previous batch)
            if overlap_tokens > 0 and current_batch:
                current_batch = [current_batch[-1]]
                current_tokens = estimate_tokens(current_batch[-1].get('chunk_text', ''))
            else:
                current_batch = []
                current_tokens = 0
        
        # Add item to current batch
        current_batch.append(item)
        current_tokens += item_tokens
    
    # Add final batch
    if current_batch:
        batches.append(current_batch)
    
    return batches


def format_code_chunks_for_llm(chunks: List[dict], max_tokens: int = 25000) -> str:
    """
    Format code chunks for LLM, staying within token limit.
    
    Args:
        chunks: Code chunks with 'file_path' and 'chunk_text'
        max_tokens: Maximum tokens to include
        
    Returns:
        Formatted string for LLM
    """
    result = []
    total_tokens = 0
    
    for chunk in chunks:
        file_path = chunk.get('file_path', 'unknown')
        code = chunk.get('chunk_text', '')
        
        # Format chunk
        formatted = f"## {file_path}\n```\n{code}\n```\n"
        chunk_tokens = estimate_tokens(formatted)
        
        # Check if we're at limit
        if total_tokens + chunk_tokens > max_tokens:
            remaining = max_tokens - total_tokens
            if remaining > 100:  # Only add if meaningful space remains
                truncated = code[:remaining * 3]
                result.append(f"## {file_path}\n```\n{truncated}...\n```\n")
            break
        
        result.append(formatted)
        total_tokens += chunk_tokens
    
    return "\n".join(result)
