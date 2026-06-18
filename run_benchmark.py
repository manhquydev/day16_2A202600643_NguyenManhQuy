from __future__ import annotations
import os
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import typer
from rich import print
from src.reflexion_lab.agents import ReActAgent, ReflexionAgent
from src.reflexion_lab.reporting import build_report, save_report
from src.reflexion_lab.utils import load_dataset, save_jsonl
app = typer.Typer(add_completion=False)

@app.command()
def main(
    dataset: str = "data/hotpot_mini.json",
    out_dir: str = "outputs/sample_run",
    reflexion_attempts: int = 3,
    provider: str = "mock"
) -> None:
    # Set the provider in the environment so that agents/mock_runtime can read it
    os.environ["LLM_PROVIDER"] = provider
    
    examples = load_dataset(dataset)
    react = ReActAgent()
    reflexion = ReflexionAgent(max_attempts=reflexion_attempts)
    
    print(f"Starting parallel benchmark with {len(examples)} examples (Provider: {provider})...")
    
    # Run agent executions in parallel using a ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=10) as executor:
        react_records = list(executor.map(react.run, examples))
        reflexion_records = list(executor.map(reflexion.run, examples))
        
    all_records = react_records + reflexion_records
    out_path = Path(out_dir)
    save_jsonl(out_path / "react_runs.jsonl", react_records)
    save_jsonl(out_path / "reflexion_runs.jsonl", reflexion_records)
    
    report = build_report(all_records, dataset_name=Path(dataset).name, mode=provider)
    json_path, md_path = save_report(report, out_path)
    
    print(f"[green]Saved[/green] {json_path}")
    print(f"[green]Saved[/green] {md_path}")
    print(json.dumps(report.summary, indent=2))

if __name__ == "__main__":
    app()
