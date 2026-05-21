import os

DEFAULT_MODEL = "gpt-4.1-mini"


def get_openai_api_key() -> str:
    """
    Retrieve the OpenAI API key from the OPENAI_API_KEY environment variable.

    Returns:
        The API key string.

    Raises:
        RuntimeError: If the key is not set.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Add it to your .env file (see .env.example)."
        )
    return key


def validate_openai_key() -> bool:
    """
    Check whether an OpenAI API key is configured and well-formed.
    Does NOT make any API calls — only structural validation.

    Returns:
        True if a plausible key is present, False otherwise.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return False

    # Basic structural check — OpenAI keys start with "sk-" and have length
    if not key.startswith("sk-"):
        return False

    if len(key) < 20:
        return False

    return True


def get_default_model() -> str:
    """
    Return the default OpenAI model identifier.
    """
    return DEFAULT_MODEL
