from redbot.core import commands
import emoji
import discord
from typing import Union

__all__ = ("timedelta_converter", "EmojiConverter", "group_embeds_by_fields")

timedelta_converter = commands.get_timedelta_converter(
    allowed_units=["minutes", "weeks", "days", "hours"]
)


class EmojiConverter(commands.EmojiConverter):
    async def convert(self, ctx: commands.Context, arg: str):
        arg = arg.strip()

        try:
            emoji.EMOJI_DATA[arg]

        except KeyError:
            return str(await super().convert(ctx, arg))

        else:
            return arg


async def group_embeds_by_fields(
    *fields: dict[str, Union[str, bool]],
    per_embed: int = 3,
    page_in_footer: Union[str, bool] = True,
    **kwargs,
) -> list[discord.Embed]:
    """
    This was the result of a big brain moment i had

    This method takes dicts of fields and groups them into separate embeds
    keeping `per_embed` number of fields per embed.

    page_in_footer can be passed either as a boolen value ( True to enable, False to disable. in which case the footer will look like `Page {index of page}/{total pages}` )
    Or it can be passed as a string template to format. The allowed variables are: `page` and `total_pages`

    Extra kwargs can be passed to create embeds off of.
    """

    fix_kwargs = lambda kwargs: {
        next(x): (fix_kwargs({next(x): v}) if "__" in k else v)
        for k, v in kwargs.copy().items()
        if (x := iter(k.split("__", 1)))
    }

    kwargs = fix_kwargs(kwargs)
    # yea idk man.

    groups: list[discord.Embed] = []
    page_format = ""
    if page_in_footer:
        kwargs.get("footer", {}).pop("text", None)  # to prevent being overridden
        page_format = (
            page_in_footer if isinstance(page_in_footer, str) else "Page {page}/{total_pages}"
        )

    ran = list(range(0, len(fields), per_embed))

    for ind, i in enumerate(ran):
        groups.append(
            discord.Embed.from_dict(kwargs)
        )  # append embeds in the loop to prevent incorrect embed count
        fields_to_add = fields[i : i + per_embed]
        for field in fields_to_add:
            groups[ind].add_field(**field)

        if page_format:
            groups[ind].set_footer(text=page_format.format(page=ind + 1, total_pages=len(ran)))
    return groups
