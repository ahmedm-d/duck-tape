"""Claude (Anthropic API) adapter for Duck Tape's structured model calls.

Uses the official `anthropic` SDK. Credentials come only from the environment (`ANTHROPIC_API_KEY`, or any
other credential source the SDK resolves); nothing is stored. Responses are constrained to the task's JSON
schema with structured outputs (`output_config.format`). Claude's constrained decoding does not accept some
JSON Schema keywords (numeric and string-length bounds, array length bounds), so `api_schema` drops only those
from the copy sent to the API; `providers.complete` still validates every reply against the full schema.

Claude Opus 5.5 always thinks; depth is set with `STEWARD_ANTHROPIC_EFFORT` (default `high`). Thinking counts
toward the caller's output reservation. `STEWARD_ANTHROPIC_MAX_TOKENS` (default 16000) can cap that reservation
but never increase it. Streaming requests have an absolute deadline as well as SDK network timeouts;
Duck Tape's own retry loop is the only retry layer (`max_retries=0`).
"""
import asyncio
import contextlib
import os

import anthropic

DEFAULT_ENDPOINT = 'https://api.anthropic.com'
DEFAULT_MODEL = 'claude-opus-5-5'
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
UNSUPPORTED_KEYWORDS = frozenset({'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf',
                                  'minLength', 'maxLength', 'minItems', 'maxItems', 'uniqueItems'})
_FINISH = {'end_turn': 'stop', 'stop_sequence': 'stop', 'max_tokens': 'max_tokens'}


def _error(code):
    from .providers import ProviderError
    return ProviderError(code)


def api_schema(schema):
    """Copy of `schema` without the keywords Claude's constrained decoding rejects (property names are kept)."""
    def clean(node, names=False):
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if names:  # keys of a `properties` mapping are field names, never keywords
            return {name: clean(value) for name, value in node.items()}
        return {key: clean(value, key == 'properties') for key, value in node.items() if key not in UNSUPPORTED_KEYWORDS}
    return clean(schema)


def effort():
    value = os.getenv('STEWARD_ANTHROPIC_EFFORT', 'high').strip().lower()
    if value not in EFFORTS:
        raise _error('invalid_effort')
    return value


def max_tokens(output_limit):
    if type(output_limit) is not int or not 1 <= output_limit <= 128000:
        raise _error('invalid_max_tokens')
    try:
        reserved = int(os.getenv('STEWARD_ANTHROPIC_MAX_TOKENS', '16000'))
    except ValueError:
        raise _error('invalid_max_tokens') from None
    if not 1 <= reserved <= 128000:
        raise _error('invalid_max_tokens')
    return min(output_limit, reserved)


def build_params(model, messages, schema, output_limit):
    """Messages API parameters for one Duck Tape request: system text goes in `system`, the rest are turns."""
    system = '\n\n'.join(item['content'] for item in messages if item['role'] == 'system')
    turns = [{'role': item['role'], 'content': item['content']} for item in messages if item['role'] != 'system']
    if not turns or turns[0]['role'] != 'user' or any(item['role'] not in ('user', 'assistant') for item in turns):
        raise _error('invalid_messages')
    config = {'effort': effort()}
    if schema is not None:
        config['format'] = {'type': 'json_schema', 'schema': api_schema(schema)}
    params = {'model': model, 'max_tokens': max_tokens(output_limit), 'messages': turns, 'output_config': config}
    if system:
        params['system'] = system
    return params


def _client(endpoint, timeout, asynchronous=True):
    kind = anthropic.AsyncAnthropic if asynchronous else anthropic.Anthropic
    return kind(base_url=endpoint or DEFAULT_ENDPOINT, timeout=timeout, max_retries=0)


async def _send(params, endpoint, timeout):
    client = _client(endpoint, timeout)
    try:
        async with client.messages.stream(**params) as stream:
            return await stream.get_final_message()
    finally:
        await client.close()


def _run(params, endpoint, timeout, cancel):
    async def run():
        operation = asyncio.create_task(_send(params, endpoint, timeout))
        try:
            async with asyncio.timeout(timeout):
                while not operation.done():
                    if cancel is not None and cancel.is_set():
                        raise _error('cancelled')
                    await asyncio.wait({operation}, timeout=0.05)
                return await operation
        finally:
            if not operation.done():
                operation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await operation
    if cancel is not None and cancel.is_set():
        raise _error('cancelled')
    try:
        return asyncio.run(run())
    except anthropic.APIStatusError as exc:
        raise _error('http_' + str(exc.status_code)) from None
    except (anthropic.APITimeoutError, TimeoutError):
        raise _error('timeout') from None
    except anthropic.APIConnectionError:
        raise _error('transport_error') from None


def complete(model, messages, schema, output_limit, timeout, cancel, endpoint):
    """Return (text, finish, usage) in the shape `providers.complete` expects."""
    message = _run(build_params(model, messages, schema, output_limit), endpoint, timeout, cancel)
    if message.stop_reason == 'refusal':
        raise _error('refusal')
    text = ''.join(block.text for block in message.content if block.type == 'text')
    usage = {'input_tokens': message.usage.input_tokens, 'output_tokens': message.usage.output_tokens}
    return text, _FINISH.get(message.stop_reason, str(message.stop_reason)), usage


def model_ids(endpoint, timeout=10):
    """Model IDs the caller's credentials can use (the health check's catalogue)."""
    client = _client(endpoint, timeout, asynchronous=False)
    try:
        return [item.id for item in client.models.list()]
    finally:
        client.close()
