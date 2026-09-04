from __future__ import annotations

import sys
from pathlib import Path

import typer
from ruamel.yaml import YAML

app = typer.Typer()


@app.command("emit")
def emit_command(
    pipelines_dir: str = typer.Argument(
        "./pipelines", help="Directory containing pipeline modules"
    ),
    out: str = typer.Option("./build/pipelines", help="Output directory for YAML files"),
) -> None:
    """Emit Conductor YAML from typed pipeline definitions."""
    import importlib.util

    from ictus.pipeline import Pipeline

    pipelines_path = Path(pipelines_dir)
    out_path = Path(out)

    if not pipelines_path.is_dir():
        typer.echo(f"Error: {pipelines_path} is not a directory", err=True)
        sys.exit(1)

    out_path.mkdir(parents=True, exist_ok=True)

    # Import pipeline modules dynamically
    pipeline_modules = sorted(pipelines_path.glob("*.py"))
    if not pipeline_modules:
        typer.echo(f"Warning: no pipeline modules found in {pipelines_path}", err=True)
        return

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.preserve_quotes = True

    for module_path in pipeline_modules:
        if module_path.name.startswith("_"):
            continue

        spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
        if spec is None or spec.loader is None:
            typer.echo(f"Warning: could not load {module_path}", err=True)
            continue

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            typer.echo(f"Error loading {module_path}: {e}", err=True)
            sys.exit(1)

        # Find all Pipeline objects
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, Pipeline):
                pipeline_dict = attr.to_dict()
                out_file = out_path / f"{attr.pipeline_id}.yaml"

                with out_file.open("w") as f:
                    yaml.dump(pipeline_dict, f)

                typer.echo(f"Emitted {attr.name} -> {out_file}")

    typer.echo(f"✓ Emitted pipelines to {out_path}")


if __name__ == "__main__":
    app()
