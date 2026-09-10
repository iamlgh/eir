import ast
import os
import pytest

# Adjust these paths to match your project structure
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__)))
TEMPLATE_DIR = os.path.join(SRC_DIR, 'templates')


def get_all_rendered_templates(root_dir):
    """
    Scans all .py files under root_dir and extracts template names passed to render_template().
    """
    templates = set()

    for dirpath, _, filenames in os.walk(root_dir):
        # Skip virtual environments, cache, or external libraries
        if any(part in dirpath for part in ['venv', '.venv', 'env', '__pycache__', 'tests']):
            continue

        for filename in filenames:
            if filename.endswith('.py'):
                file_path = os.path.join(dirpath, filename)
                templates.update(extract_templates_from_file(file_path))

    return sorted(list(templates))


def extract_templates_from_file(file_path):
    """Uses AST to parse the python file and find 'render_template' calls."""
    templates = set()

    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read(), filename=file_path)
        except SyntaxError:
            return templates  # Skip files that don't parse (e.g., syntax errors)

    # Walk through every node in the AST
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if the function being called is named 'render_template'
            if isinstance(node.func, ast.Name) and node.func.id == 'render_template':
                # Ensure there is at least one positional argument (the template name)
                if node.args:
                    first_arg = node.args[0]
                    # We can only statically analyze literal strings (e.g., "index.html")
                    if isinstance(first_arg, ast.Constant):  # Python 3.8+
                        templates.add(first_arg.value)
                    elif isinstance(first_arg, ast.Str):  # Python < 3.8 fallback
                        templates.add(first_arg.s)

    return templates


# 1. Dynamically discover all templates referenced in the code
REFERENCED_TEMPLATES = get_all_rendered_templates(SRC_DIR)


@pytest.mark.parametrize('template_name', REFERENCED_TEMPLATES)
def test_template_exists(template_name):
    """Verifies that each dynamically discovered template actually exists."""
    template_path = os.path.join(TEMPLATE_DIR, template_name)

    assert os.path.exists(template_path), f"Template '{template_name}' is referenced in your code but was not found in '{TEMPLATE_DIR}'."
