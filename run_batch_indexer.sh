#!/bin/bash
# Helper script to run the CodeMind batch indexer

# Ensure we are running from the project root
export PYTHONPATH=$PYTHONPATH:$(pwd)/src

# Check if input file is provided
if [ -z "$1" ]; then
    echo "Usage: ./run_batch_indexer.sh <input_json_file> [options]"
    echo ""
    echo "Options:"
    echo "  --url TEXT       CodeMind API URL (default: http://localhost:8000)"
    echo "  --wait           Wait for indexing compilation"
    echo "  --output TEXT    Output file for results"
    echo ""
    echo "Example:"
    echo "  ./run_batch_indexer.sh batch_repos.json --wait --output results.json"
    exit 1
fi

python3 -m codemind.batch.cli "$@"
