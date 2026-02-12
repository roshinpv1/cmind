
import asyncio
import json
import click
from pathlib import Path
from .indexer import BatchIndexer

@click.command()
@click.argument("input_file", type=click.Path(exists=True))
@click.option("--url", default="http://localhost:8000", help="CodeMind API URL")
@click.option("--wait/--no-wait", default=False, help="Wait for indexing completion")
@click.option("--output", "-o", default="batch_results.json", help="Output file for results")
def main(input_file, url, wait, output):
    """
    Batch index repositories from a JSON file.
    
    INPUT_FILE should be a JSON array of objects:
    [
      {"url": "https://github.com/user/repo", "branch": "main"}
    ]
    """
    input_path = Path(input_file)
    
    try:
        with open(input_path, "r") as f:
            repos = json.load(f)
            if not isinstance(repos, list):
                raise ValueError("Input file must be a JSON array")
    except Exception as e:
        click.echo(f"Error reading input file: {e}", err=True)
        return

    async def run():
        indexer = BatchIndexer(api_base_url=url)
        try:
            results = await indexer.process_batch(repos)
            
            # Save initial results
            save_results(results, output)
            
            if wait:
                await indexer.wait_for_jobs(results, repos_config=repos)
                # Save final results
                save_results(results, output) 

                
        finally:
            await indexer.close()

    asyncio.run(run())

def save_results(results, output_file):
    """Save results to JSON file."""
    data = [
        {
            "url": r.url,
            "branch": r.branch,
            "job_id": r.job_id,
            "status": r.status,
            "error": r.error,
            "catalog_status": r.catalog_status,
            "repo_id": r.repo_id
        }
        for r in results
    ]
    
    try:
        with open(output_file, "w") as f:
            json.dump(data, f, indent=2)
        print(f"\n📄 Results saved to {output_file}")
    except Exception as e:
        print(f"Error saving results: {e}")

if __name__ == "__main__":
    main()
