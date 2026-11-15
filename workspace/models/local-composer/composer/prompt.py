"""The text the model sees: fixed instructions, the catalog's components, the selected context and the request.

Kept short on purpose: prefill time on a phone grows with every token, and the grammar (not the prompt) is what
keeps the output inside the contract. Chat templates are the runtime's job; this is the plain message content.
"""

from __future__ import annotations

from .context import Context

SYSTEM = (
    'You arrange a phone page from the items listed below. Reply with one JSON object only: '
    '{"title": short page title, "sections": [{"component", "title", "items": [ids], "actions": [ids]}]}. '
    'Use only the listed ids. Prefer few, relevant sections. Put the most useful section first.'
)

COMPONENT_HELP = {
    'grid': 'grid: 2-8 apps or actions',
    'carousel': 'carousel: 2-8 content, notifications or apps',
    'list': 'list: 1-8 notifications or content',
    'card': 'card: 1 notification or content, plus up to 2 actions marked "-> its id"',
    'button': 'button: a row of 1-4 actions',
}

BUTTON_ASK = 'No request was typed. Make the best page for {time} from what is listed.'


def render(ctx: Context, catalog: dict, prompt: str | None, time_of_day: str | None) -> tuple[str, str]:
    """(system, user) message text for one request."""
    comps = '\n'.join(COMPONENT_HELP.get(c, c) for c in catalog['components'])
    ask = prompt.strip() if prompt else BUTTON_ASK.format(time=time_of_day or 'now')
    user = f'Components:\n{comps}\n\nItems:\n' + '\n'.join(ctx.lines) + f'\n\nRequest: {ask}'
    return SYSTEM, user
