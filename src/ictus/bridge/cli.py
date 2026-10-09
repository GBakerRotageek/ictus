"""``ictus-bridge`` — answer gates, and start runs, from a chat service.

Its own command rather than a verb on ``ictus``: every ``ictus`` verb
finishes, and this one holds a socket open and waits.

Nothing in ``ictus`` imports this module, so the edge only runs one way.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

# Runtime, not a type-checking block: typer resolves a command's annotations
# at import to build the parser.
from pathlib import Path  # noqa: TC003
from typing import Annotated

import typer

from ictus.bridge.slack.listen import (
    Click,
    Note,
    SlackError,
    open_form,
    presses,
    retire,
    say,
    verdict,
)
from ictus.notify.slack.send import reply
from ictus.runs.answer import resolve, submit
from ictus.runs.launch import Asked, start
from ictus.runs.triggers import triggers_in

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Drive ictus runs from a chat service: answer gates, and start runs.",
    # Typer prints every local of every frame on an uncaught exception, and
    # every frame below here is holding both Slack tokens.
    pretty_exceptions_show_locals=False,
)


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


APP_TOKEN_ENV = "SLACK_APP_TOKEN"
#: Replies under the question, asks for a choice's text, and retires buttons.
BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"

#: Presses handled at once. The socket thread only acknowledges and hands on.
LISTEN_WORKERS = 4


@app.callback()
def main() -> None:
    """Typer collapses a single-command app into a bare one, which made
    `ictus-bridge listen` parse `listen` as the folder to watch rather than as a
    verb — identical output to `ictus-bridge` with no arguments, and six places
    in the documentation saying otherwise. A callback keeps the subcommand."""


@app.command()
def listen(
    where: Annotated[
        Path | None,
        typer.Argument(help="A built pipeline folder, or a directory of them, to start runs from"),
    ] = None,
    allow: Annotated[
        list[str] | None,
        typer.Option("--allow", help="Slack user id that may answer; repeatable"),
    ] = None,
) -> None:
    """Answer gates from Slack, by listening for button presses.

    Opens a websocket outward to Slack, so nothing here has to be publicly
    reachable. Reads $SLACK_APP_TOKEN and $SLACK_BOT_TOKEN.

    A press is answered on the run that posted the button, and only if that
    message is the newest time the question was asked. A choice that asks for
    text opens a form for it. What happened is posted back where the button
    was, including when nothing happened, and an answered question loses its
    buttons.

    The connection is redialled whenever it drops; only Slack refusing the
    token stops it.

    Without `--allow`, anyone who can see the button may answer.
    """
    token = os.environ.get(APP_TOKEN_ENV)
    bot = os.environ.get(BOT_TOKEN_ENV, "")
    if not token:
        _fail(
            f"${APP_TOKEN_ENV} is not set. Enable Socket Mode on the Slack app, generate "
            "an app-level token with connections:write, and export it."
        )
        return
    if not bot:
        _fail(
            f"${BOT_TOKEN_ENV} is not set. Without it a choice that needs text cannot ask "
            "for it, nothing can be said in the thread, and an answered question keeps "
            "its buttons. Export the same bot token the pipeline posts with."
        )
        return
    permitted = frozenset(allow or ())
    watching = triggers_in(where) if where is not None else []
    if where is not None and not watching:
        _fail(
            f"no manifests under {where}. A pipeline is startable from a channel once it "
            "declares `listen_on(...)` and has been emitted; without that this would "
            "listen for a prefix nothing claims."
        )
    typer.secho(
        "listening for button presses"
        + (f"; only {', '.join(sorted(permitted))} may answer" if permitted else ""),
        fg=typer.colors.CYAN,
    )
    for trigger in watching:
        typer.secho(
            f'starting {trigger.pipeline or trigger.workflow.name} on "{trigger.prefix} ..."',
            fg=typer.colors.CYAN,
        )
        # At startup, not at the first message.
        for gap in trigger.missing():
            typer.secho(f"  warn  {gap}", fg=typer.colors.YELLOW)
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=LISTEN_WORKERS) as pool:
        try:
            for event in presses(token, triggers=watching):
                if isinstance(event, Asked):
                    # Slack redelivers what it thinks was not acknowledged,
                    # which reads exactly like somebody asking twice.
                    if event.thread in seen or event.trigger is None:
                        continue
                    seen.add(event.thread)
                    pool.submit(_handle_ask, event, bot)
                    continue
                pool.submit(_handle_press, event, permitted, bot)
        except KeyboardInterrupt:
            typer.secho("\nstopped listening; the runs are untouched", fg=typer.colors.BRIGHT_BLACK)
        except SlackError as exc:
            _fail(str(exc))


def _handle_ask(request: Asked, bot: str) -> None:
    """Start a run for one request, and say in its thread what became of it."""
    typer.echo(f"  ask from {request.who}: {request.question[:60]}")
    if request.trigger is None:  # pragma: no cover - the caller already checked
        return
    started = start(request, request.trigger)
    line = (
        f"Working on it — <@{request.who}> asked about *{request.question[:120]}*"
        if started.ok
        else f"Could not start: {started.why}"
    )
    if started.dashboard:
        # The only moment anybody can learn it: the port is assigned when the
        # run binds.
        line += f"\n{started.dashboard}"
    typer.secho(
        f"    -> {line.splitlines()[0]}",
        fg=typer.colors.BRIGHT_BLACK if started.ok else typer.colors.RED,
    )
    if started.dashboard:
        typer.secho(f"    -> {started.dashboard}", fg=typer.colors.CYAN)
    said = reply(token=bot, channel=request.channel, thread_ts=request.thread, text=line)
    if said:
        typer.secho(f"    -> could not say so in the thread: {said}", fg=typer.colors.RED)


def _handle_press(event: Click | Note, permitted: frozenset[str], bot: str) -> None:
    """Answer one press or one submitted form, and say what became of it.

    Runs on a worker thread, so every failure is printed rather than raised.
    """
    click = event.click if isinstance(event, Note) else event
    try:
        if isinstance(event, Note):
            outcome = submit(event.click.press, event.text, allowed=permitted)
        else:
            outcome = resolve(event.press, allowed=permitted)
            if outcome.needs_note:
                why = open_form(bot, event)
                if why:
                    say(
                        event,
                        verdict(
                            event, answered=False, reason=f"could not ask for {event.ask}: {why}"
                        ),
                        token=bot,
                    )
                return
        line = verdict(
            click, answered=outcome.answered, reason=outcome.reason, run_id=outcome.run_id
        )
        typer.echo(f"  {outcome.run_id or '-'} {click.gate}={click.choice} -> {line}")
        say(click, line, token=bot)
        if outcome.answered:
            why = retire(bot, click, line)
            if why:
                typer.secho(f"  could not take the buttons off: {why}", fg=typer.colors.YELLOW)
    except Exception as exc:
        typer.secho(
            f"  {click.gate}={click.choice}: {type(exc).__name__} while answering: {exc}",
            fg=typer.colors.RED,
            err=True,
        )


if __name__ == "__main__":
    app()
