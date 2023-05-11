from discord.interactions import Interaction
from discord.ui import Button, Select, View, button, select
from redbot.core import commands
from typing import List, Optional, Union, Callable, Coroutine, Any, TYPE_CHECKING
import discord
from redbot.core.bot import Red
from .models import Infraction
from .utils import timedelta_converter
import asyncio

if TYPE_CHECKING:
    from .main import ModPlus


class ViewDisableOnTimeout(View):
    # I was too lazy to copypaste id rather have a mother class that implements this
    def __init__(self, **kwargs):
        self.message: discord.Message = None
        self.ctx: commands.Context = kwargs.pop("ctx", None)
        self.timeout_message: str = kwargs.pop("timeout_message", None)
        super().__init__(**kwargs)

    async def on_timeout(self):
        if self.message:
            disable_items(self)
            await self.message.edit(view=self)
            if self.timeout_message and self.ctx:
                await self.ctx.send(self.timeout_message)

        self.stop()


def disable_items(self: View):
    for i in self.children:
        i.disabled = True


async def interaction_check(ctx: commands.Context, interaction: discord.Interaction):
    if not ctx.author.id == interaction.user.id:
        await interaction.response.send_message(
            "You aren't allowed to interact with this bruh. Back Off!", ephemeral=True
        )
        return False

    return True


class CloseButton(Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.red, label="Close", emoji="❎")

    async def callback(self, interaction: discord.Interaction):
        await self.view.message.delete()
        self.view.stop()


class YesOrNoView(ViewDisableOnTimeout):
    def __init__(
        self,
        ctx: commands.Context,
        yes_response: Union[
            str, Callable[[discord.Interaction], Coroutine[None, None, Any]]
        ] = "you have chosen yes.",
        no_response: Union[
            str, Callable[[discord.Interaction], Coroutine[None, None, Any]]
        ] = "you have chosen no.",
        *,
        timeout=180,
    ):
        self.yes_response = yes_response
        self.no_response = no_response
        self.value = None
        self.message = None
        super().__init__(timeout=timeout, ctx=ctx)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await interaction_check(self.ctx, interaction)

    @button(label="Yes", custom_id="_yes", style=discord.ButtonStyle.green)
    async def yes_button(self, interaction: discord.Interaction, button: Button):
        disable_items(self)
        await interaction.response.edit_message(view=self)
        if isinstance(self.yes_response, str) and self.yes_response:
            await self.ctx.send(self.yes_response)
        elif isinstance(self.yes_response, Callable):
            await self.yes_response(interaction)
        self.value = True
        self.stop()

    @button(label="No", custom_id="_no", style=discord.ButtonStyle.red)
    async def no_button(self, interaction: discord.Interaction, button: Button):
        disable_items(self)
        await interaction.response.edit_message(view=self)
        if isinstance(self.no_response, str) and self.no_response:
            await self.ctx.send(self.no_response)

        elif isinstance(self.no_response, Callable):
            await self.no_response(interaction)
        self.value = False
        self.stop()


# <-------------------Paginaion Stuff Below------------------->


class PaginatorButton(Button):
    def __init__(self, *, emoji=None, label=None):
        super().__init__(style=discord.ButtonStyle.green, label=label, emoji=emoji)


class ForwardButton(PaginatorButton):
    def __init__(self):
        super().__init__(emoji="\N{BLACK RIGHT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}")

    async def callback(self, interaction: discord.Interaction):
        if self.view.index == len(self.view.contents) - 1:
            self.view.index = 0
        else:
            self.view.index += 1

        await self.view.edit_message(interaction)


class BackwardButton(PaginatorButton):
    def __init__(self):
        super().__init__(emoji="\N{BLACK LEFT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}")

    async def callback(self, interaction: discord.Interaction):
        if self.view.index == 0:
            self.view.index = len(self.view.contents) - 1
        else:
            self.view.index -= 1

        await self.view.edit_message(interaction)


class LastItemButton(PaginatorButton):
    def __init__(self):
        super().__init__(
            emoji="\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}"
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.index = len(self.view.contents) - 1

        await self.view.edit_message(interaction)


class FirstItemButton(PaginatorButton):
    def __init__(self):
        super().__init__(
            emoji="\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}"
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.index = 0

        await self.view.edit_message(interaction)


class PageButton(Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.gray, disabled=True)

    def _change_label(self):
        self.label = f"Page {self.view.index + 1}/{len(self.view.contents)}"


class PaginatorSelect(Select):
    def __init__(self, *, placeholder: str = "Select an item:", length: int):
        options = [
            discord.SelectOption(label=f"{i+1}", value=i, description=f"Go to page {i+1}")
            for i in range(length)
        ]
        super().__init__(options=options, placeholder=placeholder)

    async def callback(self, interaction: discord.Interaction):
        self.view.index = int(self.values[0])

        await self.view.edit_message(interaction)


class PaginationView(ViewDisableOnTimeout):
    def __init__(
        self,
        context: commands.Context,
        contents: Union[List[str], List[discord.Embed]],
        timeout: int = 30,
        use_select: bool = False,
    ):
        super().__init__(timeout=timeout, ctx=context, timeout_message=None)

        self.ctx = context
        self.contents = contents
        self.use_select = use_select
        self.index = 0
        if not all(isinstance(x, discord.Embed) for x in contents) and not all(
            isinstance(x, str) for x in contents
        ):
            raise TypeError("All pages must be of the same type. Either a string or an embed.")

        if self.use_select and len(self.contents) > 1:
            self.add_item(PaginatorSelect(placeholder="Select a page:", length=len(contents)))

        buttons_to_add = (
            [FirstItemButton, BackwardButton, PageButton, ForwardButton, LastItemButton]
            if len(self.contents) > 2
            else [BackwardButton, PageButton, ForwardButton]
            if not len(self.contents) == 1
            else []
        )
        for i in buttons_to_add:
            self.add_item(i())

        self.add_item(CloseButton())
        self.update_items()

    def update_items(self):
        for i in self.children:
            if isinstance(i, PageButton):
                i._change_label()
                continue

            elif self.index == 0 and isinstance(i, FirstItemButton):
                i.disabled = True
                continue

            elif self.index == len(self.contents) - 1 and isinstance(i, LastItemButton):
                i.disabled = True
                continue

            i.disabled = False

    async def start(self):
        if isinstance(self.contents[self.index], discord.Embed):
            embed = self.contents[self.index]
            content = ""
        elif isinstance(self.contents[self.index], str):
            embed = None
            content = self.contents[self.index]
        self.message = await self.ctx.send(content=content, embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await interaction_check(self.ctx, interaction)

    def current_page(self) -> tuple[str, Optional[discord.Embed]]:
        if isinstance(self.contents[self.index], discord.Embed):
            embed = self.contents[self.index]
            content = ""
        elif isinstance(self.contents[self.index], str):
            embed = None
            content = self.contents[self.index]

        return content, embed

    async def edit_message(self, inter: discord.Interaction):
        content, embed = self.current_page()

        self.update_items()
        await inter.response.edit_message(content=content, embed=embed, view=self)


class InfractionDeleteButton(Button):
    def __init__(
        self,
        infraction: Infraction,
        after_delete: Callable[
            ["InfractionDeleteButton", discord.Interaction], Coroutine[Any, Any, Any]
        ],
    ):
        super().__init__(style=discord.ButtonStyle.red, label="Delete")
        self.infraction = infraction
        self.after_delete = after_delete

    async def callback(self, inter: discord.Interaction):
        cog = self.view.cog

        sm = self.infraction.violator

        await sm.delete_infraction(cog, self.infraction)

        await self.after_delete(self, inter)


class InfractionView(ViewDisableOnTimeout):
    def __init__(self, bot, infraction: Infraction, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.infraction = infraction
        self.add_item(InfractionDeleteButton(infraction, self.delete))

    @property
    def cog(self) -> "ModPlus":
        return self.bot.get_cog("ModPlus")

    @staticmethod
    async def delete(self: InfractionDeleteButton, inter: discord.Interaction):
        disable_items(self.view)

        return await inter.response.edit_message(content="Infraction deleted.", view=None)


class InfractionPagination(PaginationView):
    def __init__(
        self,
        ctx: commands.Context,
        contents: List[discord.Embed],
        infractions: List[Infraction],
        timeout: int = 30,
    ):
        self.infractions = infractions
        super().__init__(ctx, contents, timeout)
        self.add_item(InfractionDeleteButton(self._get_infraction(0), self.delete))

    def _get_infraction(self, index: Optional[int] = None) -> Infraction:
        return self.infractions[self.index if index is None else index]

    @property
    def cog(self) -> "ModPlus":
        return self.ctx.cog

    async def edit_message(self, inter: Interaction):
        await super().edit_message(inter)
        self.children[0].infraction = self._get_infraction()

    @staticmethod
    async def delete(self: InfractionDeleteButton, inter: discord.Interaction):
        self.view.contents.remove(self.view.contents[self.view.index])
        await inter.response.edit_message(
            content="Infraction deleted.", embed=None, view=self.view
        )


class ActionSelectView(ViewDisableOnTimeout):
    def __init__(self, violator: discord.Member, timeout: int = 30):
        self.violator = violator
        super().__init__(timeout=timeout)

    @property
    def cog(self) -> "ModPlus":
        return self.bot.get_cog("ModPlus")

    @select(
        options=[
            discord.SelectOption(label="Kick User", value="kick"),
            discord.SelectOption(label="Ban User", value="ban"),
            discord.SelectOption(label="Mute User", value="mute"),
            discord.SelectOption(label="Warn User", value="warn"),
        ],
        max_values=1,
        placeholder="Select the action you want to perform",
    )
    async def on_select(
        self,
        inter: discord.Interaction,
        select: discord.ui.Select,
    ):
        ctx = commands.Context.from_interaction(inter)
        kwargs = {"ctx": ctx, "user": self.violator, "reason": "Flagged Message"}
        await inter.response.defer(ephemeral=True)
        if select.values[0] in ["mute", "ban"]:
            await inter.channel.send(
                "Please send the duration of the punishment. (minutes, hours, days, weeks)"
            )
            try:
                msg = await inter.client.wait_for(
                    "message",
                    check=lambda m: m.author == inter.user and m.channel.id == inter.channel_id,
                    timeout=60,
                )
                duration = await timedelta_converter().convert(ctx, msg.content)

            except asyncio.TimeoutError:
                return await inter.channel.send(
                    "You took too long to respond. Cancelling the action."
                )

            except commands.BadArgument:
                return await inter.channel.send(
                    "Invalid duration provided. Cancelling the action."
                )

            kwargs["until"] = duration

        await getattr(self.cog, select.values[0])(**kwargs)

        await inter.response.send_message("Action completed.")


class FlaggingView(View):
    def __init__(self, bot: Red):
        self.bot = bot
        super().__init__(timeout=None)

    @property
    def cog(self) -> "ModPlus":
        return self.bot.get_cog("ModPlus")

    async def interaction_check(self, interaction: discord.Interaction):
        if not self.cog:
            await interaction.response.send_message("Cog is not loaded.")
            return False

        await interaction.response.defer(ephemeral=True)

        channel_id, message_id = self.get_ids_from_embed(interaction.message.embeds[0])

        data = await self.cog.config.custom(
            "FLAGGED", interaction.guild_id, channel_id, message_id
        ).all()

        await interaction.message.edit(
            embed=self.cog._create_flag_embed(
                interaction.guild.id,
                channel_id,
                message_id,
                data["flagged_by"],
                data["author_id"],
                data["content"],
                data["reporters"],
            ),
            view=self,
        )

        if not (
            cond := interaction.user.get_role(
                await self.cog.config.guild(interaction.guild).flagging.mod_role()
            )
            is not None
        ):
            await interaction.followup.send(
                "You are not allowed to perform this action.", ephemeral=True
            )

        return cond

    def get_ids_from_embed(self, embed: discord.Embed):
        ids = embed.footer.text.split("-")
        return int(ids[0]), int(ids[1])

    async def get_message_details(self, guild_id: int, channel_id: int, message_id: int):
        return await self.cog.config.custom("FLAGGED", guild_id, channel_id, message_id).all()

    @button(
        label="Delete Original Message",
        style=discord.ButtonStyle.red,
        emoji="🗑️",
        custom_id="delete_org_msg",
    )
    async def delete_original(self, inter: discord.Interaction, button: discord.ui.Button):
        channel_id, message_id = self.get_ids_from_embed(inter.message.embeds[0])
        channel = inter.client.get_channel(channel_id)

        if not channel:
            await inter.followup.send(
                "The channel for this flagged message could not be found.", ephemeral=True
            )

        try:
            message = discord.PartialMessage(channel=channel, id=message_id)
            await message.delete()

        except discord.NotFound:
            await inter.followup.send(
                "The message for this flagged message could not be found so I'm removing this as a flagged message.",
                ephemeral=True,
            )
            return
        else:
            return await inter.followup.send("Message deleted.", ephemeral=True)

    @button(
        label="Take Action", style=discord.ButtonStyle.blurple, emoji="🛡️", custom_id="take_action"
    )
    async def take_action(self, inter: discord.Interaction, button: discord.ui.Button):
        channel_id, message_id = self.get_ids_from_embed(inter.message.embeds[0])
        details = await self.get_message_details(inter.guild_id, channel_id, message_id)

        author_id = details["author_id"]

        guild = inter.client.get_guild(inter.guild_id)

        if author_id == inter.user.id:
            return await inter.followup.send(
                "You cannot take action on your own message.", ephemeral=True
            )

        elif not (mem := guild.get_member(author_id)):
            return await inter.followup.send(
                "The author of this message is no longer in the server.", ephemeral=True
            )

        await inter.followup.send(
            "Select the action you want to take.", ephemeral=True, view=ActionSelectView(mem, 60)
        )

    @button(label="Clear Flag", style=discord.ButtonStyle.green, emoji="🚩", custom_id="clear_flag")
    async def clear_flag(self, inter: discord.Interaction, button: discord.ui.Button):
        channel_id, message_id = self.get_ids_from_embed(inter.message.embeds[0])
        if await self.cog.config.custom("FLAGGED", inter.guild_id, channel_id, message_id).get_raw(
            "cleared", default=False
        ):
            return await inter.followup.send(
                "This message has already been cleared.", ephemeral=True
            )
        await self.cog.config.custom("FLAGGED", inter.guild_id, channel_id, message_id).set_raw(
            "cleared", value=True
        )
        await inter.followup.send("Flag cleared.", ephemeral=True)

    @button(
        label="List of Reporters",
        style=discord.ButtonStyle.grey,
        emoji="📋",
        custom_id="list_reporters",
    )
    async def list_reporters(self, inter: discord.Interaction, button: discord.ui.Button):
        channel_id, message_id = self.get_ids_from_embed(inter.message.embeds[0])

        reporters = await self.cog.config.custom(
            "FLAGGED", inter.guild_id, channel_id, message_id
        ).get_raw("reporters", default=[])

        embed = discord.Embed(
            title="List of Reporters",
            description=f"Total Reporters: {len(reporters)}\n"
            + "\n".join([f"<@{i}> ({i})" for i in reporters]),
            color=discord.Color.green(),
        )

        await inter.followup.send(embed=embed, ephemeral=True)

    @button(
        label="Show Full Message Content",
        style=discord.ButtonStyle.red,
        emoji="📝",
        custom_id="show_full_content",
    )
    async def show_content(self, inter: discord.Interaction, button: discord.ui.Button):
        channel_id, message_id = self.get_ids_from_embed(inter.message.embeds[0])
        details = await self.get_message_details(inter.guild_id, channel_id, message_id)

        embed = discord.Embed(
            title=f"Full Message Content",
            description=f"||{details['content']}||",
            color=discord.Color.green(),
        )

        await inter.followup.send(embed=embed, ephemeral=True)
