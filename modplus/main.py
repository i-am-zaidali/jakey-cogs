import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Literal, Optional, Union
from .models import ServerMember, Infraction
from datetime import datetime, timedelta, timezone
from .views import YesOrNoView, InfractionView, InfractionPagination
from .tagscript import process_tagscript
import TagScriptEngine as tse
from discord.ext import tasks

MEMBER_DEFAULTS = {"infractions": []}

GUILD_DEFAULTS = {
    "reason_sh": {},
    "automod": {},
    "log_channel": None,
    "log_message": None,
    "appeal_server": None,
    "dm_message": None,
    "channel_message": None,
    # "channel_actions": {
    #     "ban": True,
    #     "kick": True,
    #     "mute": True,
    #     "warn": True,
    #     "tempban": True,
    # }
}


class ModPlus(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot

        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        self.config.register_member(**MEMBER_DEFAULTS)
        self.config.register_guild(**GUILD_DEFAULTS)

    # <--- Helpers --->

    def _create_infraction_embed(self, infraction: Infraction):
        embed = (
            discord.Embed(
                title=f"Infraction {infraction.id}",
                description=f"Infraction Type: {infraction.type.value}\nOffender: <@{infraction.violator.user_id}>",
                color=discord.Color.red(),
            )
            .add_field(
                name="Reason",
                value=infraction.reason,
                inline=False,
            )
            .add_field(
                name="Issuer",
                value=f"<@{infraction.issuer_id}>",
                inline=False,
            )
            .add_field(
                name="Date Issued",
                value=f"<t:{int(infraction.at.timestamp())}:R>",
                inline=False,
            )
            .add_field(
                name="Expired?",
                value=(
                    (
                        f"Expires at <t:{int(infraction.lasts_until.timestamp())}:R>"
                        if not infraction.expired
                        else "Already expired"
                    )
                    if infraction.duration
                    else "Never expires"
                ),
            )
        )

        return embed

    async def _get_infraction_count(self, guild_id: int, user_id: int) -> int:
        return len(await self._get_infractions(guild_id, user_id))

    async def _get_non_expired_infraction_count(self, guild_id: int, user_id: int) -> int:
        return len(
            list(filter(lambda x: not x.expired, await self._get_infractions(guild_id, user_id)))
        )

    async def _get_infraction(
        self, guild_id: int, user_id: int, infraction_id: int
    ) -> Optional[Infraction]:
        return next(
            filter(
                lambda x: x.id == infraction_id, await self._get_infractions(guild_id, user_id)
            ),
            None,
        )

    async def _get_infractions(self, guild_id: int, user_id: int) -> list[Infraction]:
        sm = ServerMember(guild_id, user_id, [])
        infractions = await self.config.member_from_ids(guild_id, user_id).infractions()
        return list(
            map(
                lambda x: (inf := Infraction.from_json(x, sm), sm.infractions.append(inf))[0],
                infractions,
            )
        )

    async def _add_infraction(self, infraction: Infraction) -> Infraction:
        async with self.config.member_from_ids(
            infraction.violator.guild_id, infraction.violator.user_id
        ).infractions() as infractions:
            infractions.append(infraction.json)

        return infraction

    async def _remove_infraction(self, infraction: Infraction) -> bool:
        infraction = await self._get_infraction(
            infraction.violator.guild_id, infraction.violator.user_id, infraction.id
        )
        if not infraction:
            return False

        async with self.config.member_from_ids(
            infraction.violator.guild_id, infraction.violator.user_id
        ).infractions() as infractions:
            infractions.remove(next(filter(lambda x: x["id"] == infraction.id, infractions)))

        return True

    async def _clear_infractions(self, guild_id: int, user_id: int) -> bool:
        async with self.config.member_from_ids(guild_id, user_id).infractions() as infractions:
            infractions.clear()

        return True

    async def _modify_infraction(
        self, original: Infraction, new: Infraction
    ) -> Union[Infraction, bool]:
        infraction = await self._get_infraction(
            original.violator.guild_id, original.violator.user_id, original.id
        )

        if not infraction:
            return False

        async with self.config.member_from_ids(
            infraction.violator.guild_id, infraction.violator.user_id
        ).infractions() as infractions:
            infractions.remove(next(filter(lambda x: x["id"] == infraction.id, infraction.json)))
            infractions.append(new.json)

    async def _warn_infraction_count(self, guild_id: int, user_id: int) -> int:
        return len(
            list(
                filter(
                    lambda x: x.type.value == "warn" and not x.expired,
                    await self._get_infractions(guild_id, user_id),
                )
            )
        )

    async def _log_infraction(self, infraction: Infraction):
        log_channel = await self.config.guild_from_id(infraction.violator.guild_id).log_channel()
        if not log_channel:
            return

        guild = self.bot.get_guild(infraction.violator.guild_id)

        chan = guild.get_channel(log_channel)
        if not chan:
            return

        log_message = await self.config.guild_from_id(infraction.violator.guild_id).log_message()
        if not log_message:
            return

        kwargs = process_tagscript(
            log_message,
            {
                "server": tse.GuildAdapter(guild),
                "violator": tse.MemberAdapter(guild.get_member(infraction.violator.user_id)),
                "issuer": tse.MemberAdapter(guild.get_member(infraction.issuer_id)),
                "reason": tse.StringAdapter(infraction.reason),
                "id": tse.StringAdapter(str(infraction.id)),
                "type": tse.StringAdapter(infraction.type.value),
                "duration": tse.IntAdapter(infraction.duration.total_seconds())
                if infraction.duration
                else tse.StringAdapter("Permanent"),
            },
        )

        if not kwargs:
            return

        await chan.send(**kwargs)

    async def _channel_message(self, channel: discord.TextChannel, infraction: Infraction):
        message = await self.config.guild_from_id(infraction.violator.guild_id).channel_message()
        guild = self.bot.get_guild(infraction.violator.guild_id)
        kwargs = process_tagscript(
            message,
            {
                "server": tse.GuildAdapter(guild),
                "violator": tse.MemberAdapter(guild.get_member(infraction.violator.user_id)),
                "issuer": tse.MemberAdapter(guild.get_member(infraction.issuer_id)),
                "reason": tse.StringAdapter(infraction.reason),
                "id": tse.StringAdapter(str(infraction.id)),
                "type": tse.StringAdapter(infraction.type.value),
                "duration": tse.IntAdapter(infraction.duration.total_seconds())
                if infraction.duration
                else tse.StringAdapter("Permanent"),
            },
        )

        if not kwargs:
            return

        await channel.send(**kwargs)

    async def _dm_message(self, user: discord.Member, infraction: Infraction):
        message = await self.config.guild_from_id(infraction.violator.guild_id).dm_message()
        guild = self.bot.get_guild(infraction.violator.guild_id)

        invite = ""
        if infraction.type.value in ("ban", "tempban", "kick"):
            appeal = await self.config.guild_from_id(infraction.violator.guild_id).appeal_server()
            if appeal:
                server = self.bot.get_guild(appeal)
                if server:
                    invite = await server.channels[0].create_invite(
                        max_uses=1, max_age=48 * 60 * 60, reason=f"Infraction Appeal for {user}"
                    )

        kwargs = process_tagscript(
            message,
            {
                "server": tse.GuildAdapter(guild),
                "invite": tse.StringAdapter(invite),
                "violator": tse.MemberAdapter(guild.get_member(infraction.violator.user_id)),
                "issuer": tse.MemberAdapter(guild.get_member(infraction.issuer_id)),
                "reason": tse.StringAdapter(infraction.reason),
                "id": tse.StringAdapter(str(infraction.id)),
                "type": tse.StringAdapter(infraction.type.value),
                "duration": tse.IntAdapter(infraction.duration.total_seconds())
                if infraction.duration
                else tse.StringAdapter("Permanent"),
            },
        )

        if not kwargs:
            return

        await user.send(**kwargs)

    async def _appropriate_reason(self, guild_id: int, reason: str):
        shorthands = await self.config.guild_from_id(guild_id).reason_sh()
        for shorthand, replacement in shorthands.items():
            reason = reason.replace(shorthand, replacement)

        return reason

    async def _check_automod(self, ctx: commands.Context, user: discord.Member):
        count = await self._warn_infraction_count(user.guild.id, user.id)

        am_counts = await self.config.guild(user.guild).automod()

        try:
            action = am_counts[count]

        except KeyError:
            return

        else:
            if isinstance(action, str):
                await getattr(self, action)(
                    ctx, user=user, reason=f"Automod action for {count} infractions"
                )

            elif isinstance(action, int):
                await self.tempban(
                    ctx,
                    user=user,
                    duration=timedelta(seconds=action),
                    reason=f"Automod action for {count} infractions",
                )

    @tasks.loop(hours=1)
    async def remove_tempbans(self):
        guilds = await self.config.all_members()

        for guild_id, guild_data in guilds.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            for member_id, member_data in guild_data["members"].items():
                for infraction in filter(
                    lambda x: x["type"] == "tempban", member_data["infractions"]
                ):
                    if datetime.fromisoformat(infraction["duration"]) < datetime.utcnow():
                        # check if user isnt already unbanned
                        if not next(
                            filter(
                                lambda x: x.user.id == int(member_id),
                                [x async for x in guild.bans()],
                            ),
                            None,
                        ):
                            continue
                        await guild.unban(
                            discord.Object(id=int(member_id)), reason="Tempban expired"
                        )

    @remove_tempbans.before_loop
    async def before_remove_tempbans(self):
        await self.bot.wait_until_red_ready()

    # <--- Commands --->

    # <--- Setting Commands --->

    @commands.group(
        name="modplusset",
        aliases=["mpset"],
        invoke_without_command=True,
    )
    async def mpset(self, ctx: commands.Context):
        return await ctx.send_help()

    # <--- Reason Shorthands --->

    @mpset.group(name="reasonshorthands", aliases=["reasonsh", "rsh"], invoke_without_command=True)
    async def mpset_rsh(self, ctx: commands.Context):
        """
        Reason shorthands are shortforms that you can use in reason arguments for moderation commands.

        These will be replaces by longer strings when the reason is logged.
        """
        return await ctx.send_help()

    @mpset_rsh.command(name="add")
    async def mpset_rsh_add(self, ctx: commands.Context, shorthand: str, *, reason: str):
        """
        Add a new reason shorthand.

        The shorthand will be replaced by the reason when the reason is logged.
        """
        async with self.config.guild(ctx.guild).reason_sh() as reason_sh:
            if shorthand in reason_sh:
                return await ctx.send(
                    "That shorthand already exists: `{}` - `{}`".format(
                        shorthand, reason_sh[shorthand]
                    )
                )

            reason_sh[shorthand] = reason

            return await ctx.send("Added shorthand: `{}` - `{}`".format(shorthand, reason))

    @mpset_rsh.command(name="remove", aliases=["delete", "del"])
    async def mpset_rsh_remove(self, ctx: commands.Context, shorthand: str):
        """
        Remove a reason shorthand.
        """
        async with self.config.guild(ctx.guild).reason_sh() as reason_sh:
            if shorthand not in reason_sh:
                return await ctx.send("That shorthand doesn't exist.")

            del reason_sh[shorthand]

            return await ctx.send("Removed shorthand: `{}`".format(shorthand))

    @mpset_rsh.command(name="list")
    async def mpset_rsh_list(self, ctx: commands.Context):
        """
        List all reason shorthands.
        """
        reason_sh = await self.config.guild(ctx.guild).reason_sh()

        if not reason_sh:
            return await ctx.send("There are no reason shorthands.")

        embed = discord.Embed(
            title=f"Reason Shorthands for {ctx.guild.name}",
            description="\n".join(
                [f"`{shorthand}` - `{reason}`" for shorthand, reason in reason_sh.items()]
            ),
            color=await ctx.bot.get_embed_color(ctx.channel),
        )

        await ctx.send(embed=embed)

    # <--- Automod --->

    @mpset.group(name="automod", aliases=["am"], invoke_without_command=True)
    async def mpset_automod(
        self,
        ctx: commands.Context,
        infraction_count: int,
        *,
        action: Literal["ban", "kick", "mute", "warn", "tempban", "clear"],
    ):
        """
        Infraction based automod.

        If a user has more than `infraction_count` infractions, they will be actioned.

        Use `clear` for the `action` argument to remove the automod for that infraction count.
        """

        async with self.config.guild(ctx.guild).automod() as automod:
            if action == "clear":
                if infraction_count not in automod:
                    return await ctx.send("There is no automod for that infraction count.")

                del automod[infraction_count]
                return await ctx.send(
                    "Removed automod for infraction count: `{}`".format(infraction_count)
                )

            if a := automod.get(infraction_count):
                view = YesOrNoView(ctx, "", "Alright, it will remain the same.")
                if isinstance(a, int):
                    await ctx.send(
                        f"That infraction count is already set to `tempban` the user for {a}. Do you want to change it to `{action}`?",
                        view=view,
                    )
                else:
                    await ctx.send(
                        f"That infraction count is already set to `{a}` the user. Do you want to change it to `{action}`?",
                        view=view,
                    )

                await view.wait()

                if not view.value:
                    return

            if action == "tempban":
                await ctx.send(
                    "How long do you want to tempban the user for? (days, weeks, hours)"
                )
                msg = await ctx.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=60,
                )
                try:
                    time: timedelta = await commands.get_timedelta_converter(
                        allowed_units=["hours", "days", "weeks"]
                    ).convert(ctx, msg.content)
                except TimeoutError:
                    return await ctx.send("Timed Out.")
                except ValueError:
                    return await ctx.send("That is not a valid number.")
                else:
                    automod[infraction_count] = int(time.total_seconds())

            else:
                automod[infraction_count] = action

            await ctx.send(
                f"Alright, I will {action} users with more than {infraction_count} infractions."
            )

    @mpset_automod.command(name="show")
    async def mpset_automod_show(self, ctx: commands.Context):
        """
        Show the automod settings for the guild.
        """
        automod = await self.config.guild(ctx.guild).automod()

        if not automod:
            return await ctx.send("There are no automod settings.")

        embed = discord.Embed(
            title=f"Automod Settings for {ctx.guild.name}",
            description="\n".join(
                [
                    f"`{infraction_count}` - `{action}`"
                    for infraction_count, action in automod.items()
                ]
            ),
            color=await ctx.bot.get_embed_color(ctx.channel),
        )

        await ctx.send(embed=embed)

    @mpset_automod.command(name="clear")
    async def mpset_automod_clear(self, ctx: commands.Context):
        """
        Clear all automod settings for the guild.
        """
        await self.config.guild(ctx.guild).automod.clear()
        await ctx.send("Cleared all automod settings.")

    # <--- Logging --->

    @mpset.group(name="log", invoke_without_command=True)
    async def mpset_log(self, ctx: commands.Context):
        """
        Logging settings.
        """
        return await ctx.send_help()

    @mpset_log.command(name="channel")
    async def mpset_log_channel(
        self,
        ctx: commands.Context,
        channel: Union[discord.TextChannel, None, Literal["clear"]] = None,
    ):
        """
        Set the channel to log moderation actions to.

        Use `clear` to remove the log channel or don't provide a channel to see the current log channel.
        """
        if channel == "clear":
            await self.config.guild(ctx.guild).log_channel.clear()
            return await ctx.send("Cleared the log channel.")

        elif channel is None:
            channel = await self.config.guild(ctx.guild).log_channel()
            if channel is None:
                return await ctx.send("There is no log channel set.")
            return await ctx.send(f"The current log channel is {channel.mention}.")

        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        return await ctx.send(f"Set the log channel to {channel.mention}.")

    @mpset_log.command(name="embed")
    async def mpset_log_message(
        self, ctx: commands.Context, *, tagscript: Union[Literal["clear"], None, str] = None
    ):
        """
        Use tagscript to generate a message that is sent to the log channel when a moderation action is performed.

        Use `clear` to remove the embed message or don't provide a tagscript to see the current message.

        The following variables are available:
        {server} - The server in which the moderation action was performed.
        {violator} - The user who was moderated.
        {issuer} - The user who performed the moderation action.
        {reason} - The reason for the moderation action.
        {id} - The ID of the moderation action.
        {type} - The type of moderation action.
        {duration} - The duration of the moderation action.
        """
        if tagscript == "clear":
            await self.config.guild(ctx.guild).log_message.clear()
            return await ctx.send("Cleared the log message.")

        elif tagscript is None:
            tagscript = await self.config.guild(ctx.guild).log_message()
            if tagscript is None:
                return await ctx.send("There is no log message set.")
            return await ctx.send(f"The current log message is ```{tagscript}```")

        await self.config.guild(ctx.guild).log_message.set(tagscript)
        return await ctx.send(f"Set the log message to ```{tagscript}```")

    @mpset_log.command(name="show")
    async def mpset_log_show(self, ctx: commands.Context):
        """
        Show the logging settings for the guild.
        """
        log_channel = await self.config.guild(ctx.guild).log_channel()
        log_message = await self.config.guild(ctx.guild).log_message()

        if not log_channel:
            return await ctx.send("There is no log channel set.")

        embed = discord.Embed(
            title=f"Logging Settings for {ctx.guild.name}",
            description=f"Log Channel: {getattr(log_channel, 'mention', 'N/A')}\nLog Message: ```{log_message or 'N/A'}```",
            color=await ctx.bot.get_embed_color(ctx.channel),
        )

        await ctx.send(embed=embed)

    # <--- Appeal Server --->

    @mpset.command(name="appealserver", aliases=["appeal"])
    async def mpset_appealserver(
        self, ctx: commands.Context, server: Union[discord.Guild, None, Literal["clear"]] = None
    ):
        """
        Set the appeal server to generate invite links for.

        Use `clear` to remove the appeal server or don't provide a server to see the current appeal server.
        """
        if server == "clear":
            await self.config.guild(ctx.guild).appeal_server.clear()
            return await ctx.send("Cleared the appeal server.")

        elif server is None:
            server = await self.config.guild(ctx.guild).appeal_server()
            if server is None:
                return await ctx.send("There is no appeal server set.")
            return await ctx.send(f"The current appeal server is {server.name}.")

        await self.config.guild(ctx.guild).appeal_server.set(server.id)
        return await ctx.send(f"Set the appeal server to {server.name}.")

    # <--- DM on Infraction --->

    @mpset.command(name="dm")
    async def mpset_dm(
        self, ctx: commands.Context, *, dm: Union[Literal["clear"], None, str] = None
    ):
        """
        Use tagscript to generate a message that is sent to the violator when a moderation action is performed.

        Use `clear` to remove the DM message or don't provide a tagscript to see the current message.

        The following variables are available:
        {server} - The server in which the moderation action was performed.
        {violator} - The user who was moderated.
        {issuer} - The user who performed the moderation action.
        {reason} - The reason for the moderation action.
        {id} - The ID of the moderation action.
        {type} - The type of moderation action.
        {duration} - The duration of the moderation action.
        {invite} - the invite link to the appeal server. Only available if an appeal server is set and the moderation action is a ban, kick or tempban
        """
        if dm == "clear":
            await self.config.guild(ctx.guild).dm_message.clear()
            return await ctx.send("Cleared the DM message.")

        elif dm is None:
            dm = await self.config.guild(ctx.guild).dm_message()
            if dm is None:
                return await ctx.send("There is no DM message set.")
            return await ctx.send(f"The current DM message is ```{dm}```")

        await self.config.guild(ctx.guild).dm_message.set(dm)
        return await ctx.send(f"Set the DM message to ```{dm}```")

    @mpset.command(name="channelmessage", aliases=["cm"])
    async def mpset_channelmessage(
        self, ctx: commands.Context, *, cm: Union[Literal["clear"], None, str] = None
    ):
        """
        Use tagscript to generate a message that is sent to the channel where the moderation action was performed.

        Use `clear` to remove the channel message or don't provide a tagscript to see the current message.

        The following variables are available:
        {server} - The server in which the moderation action was performed.
        {violator} - The user who was moderated.
        {issuer} - The user who performed the moderation action.
        {reason} - The reason for the moderation action.
        {id} - The ID of the moderation action.
        {type} - The type of moderation action.
        {duration} - The duration of the moderation action.
        """
        if cm == "clear":
            await self.config.guild(ctx.guild).channel_message.clear()
            return await ctx.send("Cleared the channel message.")

        elif cm is None:
            cm = await self.config.guild(ctx.guild).channel_message()
            if cm is None:
                return await ctx.send("There is no channel message set.")
            return await ctx.send(f"The current channel message is ```{cm}```")

        await self.config.guild(ctx.guild).channel_message.set(cm)
        return await ctx.send(f"Set the channel message to ```{cm}```")

    # <--- Moderation Commands --->

    @commands.command(name="warn")
    async def warn(
        self,
        ctx: commands.Context,
        user: discord.Member,
        until: Optional[
            commands.get_timedelta_converter(allowed_units=["hours", "days", "weeks"])
        ] = None,
        *,
        reason: str,
    ):
        """
        Warn a user.

        The `until` argument can be used to set an expiry for the warning, although this is not enforced and is optional and can be skipped.
        """
        if not await self._validate_action(ctx, user, "warn"):
            return await ctx.send("You cannot warn this user.")

        reason = await self._appropriate_reason(ctx.guild.id, reason)

        sm = await ServerMember.from_member(self, user)
        infraction = await sm.infraction(self, "warn", reason, ctx.author.id, duration=until)
        await self._channel_message(ctx.channel, infraction)
        await self._dm_message(user, infraction)

    @commands.command(name="mute")
    async def mute(
        self,
        ctx: commands.Context,
        user: discord.Member,
        until: Optional[
            commands.get_timedelta_converter(allowed_units=["hours", "days", "weeks"])
        ] = None,
        *,
        reason: str,
    ):
        """
        Mute a user.

        The `until` argument can be used to set an expiry for the mute, although this is not enforced and is optional and can be skipped.
        """
        if not await self._validate_action(ctx, user, "mute"):
            return await ctx.send("You cannot mute this user.")

        reason = await self._appropriate_reason(ctx.guild.id, reason)

        sm = await ServerMember.from_member(self, user)
        infraction = await sm.infraction(self, "mute", reason, ctx.author.id, duration=until)
        await self._channel_message(ctx.channel, infraction)
        await self._dm_message(user, infraction)
        await user.timeout(until, reason=reason)

    @commands.command(name="ban")
    async def ban(self, ctx: commands.Context, user: discord.Member, *, reason: str):
        """
        Ban a user.
        """
        if not await self._validate_action(ctx, user, "ban"):
            return await ctx.send("You cannot ban this user.")

        reason = await self._appropriate_reason(ctx.guild.id, reason)

        sm = await ServerMember.from_member(self, user)
        infraction = await sm.infraction(self, "ban", reason, ctx.author.id)
        await self._channel_message(ctx.channel, infraction)
        await self._dm_message(user, infraction)
        await user.ban(reason=reason)

    @commands.command(name="tempban")
    async def tempban(
        self,
        ctx: commands.Context,
        user: discord.Member,
        until: commands.get_timedelta_converter(allowed_units=["hours", "days", "weeks"]),
        *,
        reason: str,
    ):
        """
        Temporarily ban a user.
        """
        if not await self._validate_action(ctx, user, "ban"):
            return await ctx.send("You cannot ban this user.")

        reason = await self._appropriate_reason(ctx.guild.id, reason)

        sm = await ServerMember.from_member(self, user)
        infraction = await sm.infraction(self, "tempban", reason, ctx.author.id, duration=until)
        await self._channel_message(ctx.channel, infraction)
        await self._dm_message(user, infraction)
        await user.ban(reason=reason)

    @commands.command(name="kick")
    async def kick(self, ctx: commands.Context, user: discord.Member, *, reason: str):
        """
        Kick a user.
        """
        if not await self._validate_action(ctx, user, "kick"):
            return await ctx.send("You cannot kick this user.")

        reason = await self._appropriate_reason(ctx.guild.id, reason)

        sm = await ServerMember.from_member(self, user)
        infraction = await sm.infraction(self, "kick", reason, ctx.author.id)
        await self._channel_message(ctx.channel, infraction)
        await self._dm_message(user, infraction)
        await user.kick(reason=reason)

    # <--- Infractions --->

    @commands.group(name="infractions", aliases=["infraction"], invoke_without_command=True)
    async def infractions(self, ctx: commands.Context):
        """
        Infraction management commands.
        """
        return await ctx.send_help(ctx.command)

    @infractions.command(name="delete", aliases=["remove"])
    async def infractions_delete(
        self, ctx: commands.Context, user: discord.Member, infraction_id: int
    ):
        """
        Delete an infraction.
        """
        infraction = await self._get_infraction(ctx.guild.id, user.id, infraction_id)

        if infraction is None:
            return await ctx.send("That infraction does not exist.")

        if infraction.issuer_id != ctx.author.id and (
            not await self.bot.is_owner(ctx.author) or ctx.author.guild_permissions.administrator
        ):
            return await ctx.send("You cannot delete that infraction.")

        sm = await ServerMember.from_member(self, user)
        await sm.delete_infraction(self, infraction_id)
        await ctx.send("Infraction deleted.")

    @infractions.command(name="clear")
    async def infractions_clear(self, ctx: commands.Context, user: discord.Member):
        """
        Clear all the infractions of a user.
        """
        sm = await ServerMember.from_member(self, user)
        await sm.clear_infractions(self)
        await ctx.send("Infractions cleared.")

    @infractions.command(name="show")
    async def infractions_show(
        self, ctx: commands.Context, user: discord.Member, infraction_id: str
    ):
        """
        Show the infractions of a user.
        """
        infraction = await self._get_infraction(ctx.guild.id, user.id, infraction_id)

        if not infraction:
            return await ctx.send("That infraction does not exist.")

        embed = self._create_infraction_embed(infraction)

        await ctx.send(embed=embed, view=InfractionView(self.bot, infraction))

    @infractions.command(name="list")
    async def infractions_list(self, ctx: commands.Context, user: discord.Member):
        """
        List the infractions of a user.
        """
        infractions = await self._get_infractions(ctx.guild.id, user.id)
        if not infractions:
            return await ctx.send("This user has no infractions.")

        embeds = [self._create_infraction_embed(infraction) for infraction in infractions]

        await InfractionPagination(ctx, embeds, infractions).start()

    # <--- User Lookup --->

    @commands.command(name="lookup")
    async def lookup(self, ctx: commands.Context, user: discord.User):
        """
        Lookup a user.
        """
        if not (await self.bot.is_owner(ctx.author) or ctx.author.guild_permissions.administrator):
            return await ctx.send("You cannot use this command.")
        sm = await ServerMember.from_ids(self, ctx.guild.id, user.id)
        infractions = sm.infractions
        embed = discord.Embed(
            title=f"User Lookup",
            description=f"User ID: {user.id}\nUsername: {user.display_name}\nInfractions: {len(infractions)}\nCreated At: <t:{int(user.created_at.timestamp())}:R>",
            color=discord.Color.red(),
        )
        if mem := ctx.guild.get_member(user.id):
            embed.add_field(name="Joined At", value=f"<t:{int(mem.joined_at.timestamp())}:R>")
            embed.add_field(name="Server Nickname", value=mem.display_name)

        embed.set_thumbnail(url=user.display_avatar.url)
        await ctx.send(embed=embed)
