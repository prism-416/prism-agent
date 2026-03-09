from pathlib import Path

from src.prompts.loader import YamlPromptLoader


def test_yaml_prompt_loader_renders_variables(tmp_path: Path) -> None:
    prompt_file = tmp_path / "synthesis.yaml"
    prompt_file.write_text("prompt: 'Hello ${name}!'", encoding="utf-8")

    loader = YamlPromptLoader(prompts_dir=tmp_path)
    rendered = loader.render("synthesis", {"name": "World"})

    assert rendered == "Hello World!"

