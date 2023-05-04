import TagScriptEngine as tse
from typing import Optional, TypedDict
import discord

blocks = [
    tse.LooseVariableGetterBlock(),
    tse.AssignmentBlock(),
    tse.IfBlock(),
    tse.EmbedBlock(),
    tse.ReplaceBlock(),
    tse.StrictVariableGetterBlock(),
    tse.PythonBlock(),
    tse.BreakBlock(),
    tse.StopBlock(),
]
tagscript_engine = tse.Interpreter(blocks)


class OutputDict(TypedDict):
    content: Optional[str]
    embed: Optional[discord.Embed]


def process_tagscript(content: str, seed_variables: dict = {}) -> OutputDict:
    output = tagscript_engine.process(content, seed_variables)
    kwargs = {}
    if output.body:
        kwargs["content"] = output.body[:2000]
    if embed := output.actions.get("embed"):
        kwargs["embed"] = embed

    return kwargs
