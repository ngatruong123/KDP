"""Prompt template system for customizable prompt generation.

Default template is "{prompt}" which passes the prompt through unchanged
(100% backward compatible).
"""


def build_prompt(template: str, fields: dict) -> str:
    """Build a prompt string from a template and field values.

    Args:
        template: A Python format string, e.g. "{prompt}, {style} style"
        fields: Dict of field values, e.g. {"prompt": "a cat", "style": "watercolor"}

    Returns:
        The formatted prompt string.
    """
    try:
        return template.format(**fields)
    except KeyError as e:
        print(f"[prompt_builder] Missing template field: {e}, using raw prompt")
        return fields.get("prompt", "")
