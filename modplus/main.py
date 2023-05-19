import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Literal, Optional, Union
from .models import ServerMember, Infraction
from datetime import datetime, timedelta, timezone
from .views import YesOrNoView, InfractionView, InfractionPagination, PaginationView, FlaggingView
from .tagscript import process_tagscript
import TagScriptEngine as tse
from discord.ext import tasks
from redbot.core.utils import chat_formatting as cf
from .utils import timedelta_converter, EmojiConverter, group_embeds_by_fields
from cachetools import TTLCache

FLAGGED_MESSAGE = {}
# { guild_id: { channel_id: { message_id: { author_id: int, content: int, timestamp: str, alert_message: int, cleared: bool, reporters: list[int], flagged_by: int } } } }

MEMBER_DEFAULTS = {"infractions": [], "watchlist": None}
# infractions: list[Infraction]
# watchlist: {duration: datetime | None, reason: str}

GUILD_DEFAULTS = {
    "reason_sh": {},
    "automod": {},
    "log_channel": None,
    "log_message": (
        "{embed(title):**New Action taken**}\n"
        "{embed(description): **Issued by:**\n"
        "{issuer(mention)} ({issuer(id)})\n"
        "**Offender:**\n"
        "{violator(mention)} ({violator(id)})\n"
        "**Action Taken:**\n"
        "*{type}ed*\n"
        "**Reason:**\n"
        "{reason}\n"
        "**Duration**:\n"
        "{if({duration}==Permanent):Permanent|<t:{math:round({unix}+{duration})}:F>}\n\n"
        "**DM'ed?**\n"
        "{if({dms_open}):Yes|No, user might have dms closed.}\n}"
    ),
    "appeal_server": None,
    "dm_message": (
        "{stop({type}==mute)}\n"
        "{=(infrom):{if({any({type}==ban|{type}==kick|{type}==tempban)}):from|in}}\n"
        "{=(inv):{if({invite}!=):Here's a one time use invite link for you to appeal this action {invite}|}}\n"
        "{=(dur):{if({duration}==Permanent):Permanently|until <t:{math:round({unix}+{duration})}:F>}}\n"
        "{embed(title):**{upper({type}ed)}**}\n\n"
        "{embed(description): You have been {capitalize({type})}ed in {server(name)} by {issuer(mention)} ({issuer})\n"
        "{dur}}"
    ),
    "channel_message": (
        "{embed(title):**New Action taken**}\n"
        "{embed(description): **Issued by:**\n"
        "{issuer(mention)} ({issuer(id)})\n"
        "**Offender:**\n"
        "{violator(mention)} ({violator(id)})\n"
        "**Action Taken:**\n"
        "*{type}ed*\n"
        "**Reason:**\n"
        "{reason}\n"
        "**Duration**:\n"
        "{if({duration}==Permanent):Permanent|<t:{math:round({unix}+{duration})}:F>}\n\n"
        "**DM'ed?**\n"
        "{if({dms_open}):Yes|No, user might have dms closed.}\n}"
    ),
    "watchlist": {
        "channel": None,
        "notify": False,
        "infraction_message": "{violator(mention)} ({violator(id)}) has just been **{type}ed** for **{reason}**. They were already on the watchlist",
    },
    "flagging": {
        "emoji": "🚩",
        "channel": None,
        "ping_threshold": 4,
        "mod_role": None,
        "cooldown": 300,
    },
}


class ModPlus(commands.Cog):
    """
    A cog that adds more moderation commands and features to your server.
    """

    __version__ = "1.2.0"
    __author__ = ["crayyy_zee#2900"]

    def __init__(self, bot: Red):
        self.bot = bot

        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        self.config.register_member(**MEMBER_DEFAULTS)
        self.config.register_guild(**GUILD_DEFAULTS)

        self.config.init_custom("FLAGGED", 3)

        self.cooldown_cache: dict[int, TTLCache] = {}

        self.flagging_view = FlaggingView(self.bot)
        self._update_view()

    def _update_view(self):
        for view in filter(
            lambda x: x.__class__.__name__ == "FlaggingView", self.bot.persistent_views
        ):
            view.stop()

        self.bot.add_view(self.flagging_view)

    def format_help_for_context(self, ctx: commands.Context) -> str:
        pre_processed = super().format_help_for_context(ctx) or ""
        n = "\n" if "\n\n" not in pre_processed else ""
        text = [
            f"{pre_processed}{n}",
            f"Cog Version: **{self.__version__}**",
            f"Author: {cf.humanize_list(self.__author__)}",
        ]
        return "\n".join(text)

    # <--- Helpers --->

    async def _get_watchlist(
        self, guild_id: int
    ) -> dict[int, dict[str, Union[str, datetime, None]]]:
        guild = self.bot.get_guild(guild_id)
        return dict(
            map(
                lambda x: (
                    x[0],
                    dict(
                        (
                            ("reason", x[1]["watchlist"]["reason"]),
                            (
                                "duration",
                                datetime.fromisoformat(x[1]["watchlist"]["duration"])
                                if x[1]["watchlist"]["duration"] is not None
                                else None,
                            ),
                        )
                    ),
                ),
                filter(
                    lambda x: x[1]["watchlist"] is not None,
                    (await self.config.all_members(guild)).items(),
                ),
            )
        )

    async def _get_watchlist_status(self, guild_id: int, user_id: int):
        watchlist = await self.config.member_from_ids(guild_id, user_id).watchlist()
        if (
            watchlist
            and watchlist["duration"]
            and datetime.fromisoformat(watchlist["duration"]) < datetime.utcnow()
        ):
            await self._remove_from_watchlist(guild_id, user_id)
            return None
        return watchlist

    async def _add_to_watchlist(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        duration: Union[datetime, None],
    ):
        await self.config.member_from_ids(guild_id, user_id).watchlist.set(
            {"reason": reason, "duration": duration.isoformat() if duration else None}
        )

    async def _remove_from_watchlist(self, guild_id: int, user_id: int):
        await self.config.member_from_ids(guild_id, user_id).watchlist.clear()

    async def _clear_watchlist(self, guild_id: int):
        for member in await self.config.all_members(guild_id):
            await self.config.member_from_ids(guild_id, member).watchlist.clear()

    async def _notify_watchlist_of_infraction(self, ctx: commands.Context, infraction: Infraction):
        wl_channel_id = await self.config.guild(ctx.guild).watchlist.channel()
        wl_notify = await self.config.guild(ctx.guild).watchlist.notify()
        wl_channel = ctx.guild.get_channel(wl_channel_id)
        wl_message = await self.config.guild(ctx.guild).watchlist.infraction_message()

        if not all((wl_channel_id, wl_channel, wl_notify, wl_message)):
            return

        kwargs = process_tagscript(
            wl_message,
            {
                "server": tse.GuildAdapter(ctx.guild),
                "violator": tse.MemberAdapter(ctx.guild.get_member(infraction.violator.user_id)),
                "issuer": tse.MemberAdapter(ctx.guild.get_member(infraction.issuer_id)),
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

        await wl_channel.send(**kwargs)

    def _create_flag_embed(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        flagger_id: int,
        author_id: int,
        message_content: str,
        reporters: list[int],
    ):
        embed = (
            discord.Embed(
                title="**MESSAGE FLAGGED**",
                description=f"Message flagged by <@{flagger_id}> ({flagger_id})",
                color=discord.Color.yellow(),
            )
            .add_field(
                name="Message Content",
                value=f"||{message_content[:197] + ('...' if len(message_content) > 197 else '')}||",
                inline=False,
            )
            .add_field(
                name="Message Author",
                value=f"<@{author_id}> ({author_id})",
                inline=False,
            )
            .add_field(
                name="Message Channel",
                value=f"<#{channel_id}> ({channel_id})",
                inline=False,
            )
            .add_field(
                name="Message Link",
                value=f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}",
                inline=False,
            )
            .add_field(
                name="Flagged By",
                value=f"{len(reporters)} users",
            )
            .set_footer(text=f"{channel_id}-{message_id}")
        )

        return embed

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
        sm = ServerMember(guild_id, user_id, [], {})
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

    async def _log_infraction(self, infraction: Infraction, dms_open: bool):
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
                "dms_open": tse.StringAdapter(dms_open),
            },
        )

        if not kwargs:
            return

        await chan.send(**kwargs)

    async def _channel_message(
        self, channel: discord.TextChannel, infraction: Infraction, dms_open: bool
    ):
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
                "dms_open": tse.StringAdapter(dms_open),
            },
        )

        if not kwargs:
            return

        await channel.send(**kwargs)

    async def _dm_message(
        self, user: discord.Member, infraction: Infraction, include_invite: bool = True
    ):
        message = await self.config.guild_from_id(infraction.violator.guild_id).dm_message()
        guild = self.bot.get_guild(infraction.violator.guild_id)

        invite = ""
        if infraction.type.value in ("ban", "tempban", "kick") and include_invite:
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
            return False

        try:
            await user.send(**kwargs)

        except Exception:
            return False

        else:
            return True

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
            kwargs = dict(ctx=ctx, user=user, reason=f"Automod action for {count} infractions")
            if len(a := action.split()) > 1:
                action = a[0]
                duration = timedelta(seconds=int(a[1]))
                kwargs.update(until=duration)

            await getattr(self, action)(**kwargs)

    async def _validate_action(self, ctx: commands.Context, user: discord.Member, action: str):
        return all(
            [
                ctx.me.top_role > user.top_role,
                ctx.author.top_role > user.top_role,
                ctx.me.guild_permissions
                >= discord.Permissions(
                    manage_roles=True,
                    kick_members=True,
                    ban_members=True,
                    manage_guild=True,
                    moderate_members=True,
                ),
            ]
        ) or any(
            [
                ctx.guild.owner_id == ctx.author.id,
                await ctx.bot.is_owner(ctx.author),
                await ctx.bot.is_mod(ctx.author),
            ]
        )

    # <--- Tempban Expiry loop --->

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

    # <--- listeners --->

    @commands.Cog.listener()
    async def on_modplus_infraction(
        self, ctx: commands.Context, sm: ServerMember, infraction: Infraction
    ):
        await self._add_infraction(infraction)
        include_invite = ctx.args[-1] if infraction.type.value in ("ban", "tempban") else False
        dms_open = await self._dm_message(ctx.args[2], infraction, include_invite=include_invite)
        await self._channel_message(ctx.channel, infraction, dms_open=dms_open)
        await self._log_infraction(infraction, dms_open=dms_open)

        if sm.is_being_watched:
            await self._notify_watchlist_of_infraction(ctx, infraction)

        await self._check_automod(ctx, ctx.guild.get_member(infraction.violator.user_id))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        if message.channel.id != await self.config.guild(message.guild).watchlist.channel():
            return

        if message.mentions:
            user = message.mentions[0]
            stat = await self._get_watchlist_status(message.guild.id, user.id)
            if stat:
                duration = (
                    f"until <t:{int(stat['duration'].timestamp())}:R>"
                    if stat["duration"]
                    else "Permanently"
                )
                msg = f"User {user.mention} ({user.id}) is on the watchlist {duration}"

            else:
                msg = f"User {user.mention} ({user.id}) is not on the watchlist"

            await message.channel.send(msg)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        guild = self.bot.get_guild(payload.guild_id)

        if str(payload.emoji) != await self.config.guild(guild).flagging.emoji():
            return

        fc = guild.get_channel(await self.config.guild(guild).flagging.channel())
        if not fc:
            return

        message_details = await self.config.custom(
            "FLAGGED", payload.guild_id, payload.channel_id, payload.message_id
        ).all()

        if message_details:
            reporters: list[int] = [*message_details["reporters"], payload.user_id]
            await self.config.custom(
                "FLAGGED", payload.guild_id, payload.channel_id, payload.message_id
            ).set_raw("reporters", value=reporters)

            threshold = await self.config.guild(guild).flagging.ping_threshold()
            if len(reporters) >= threshold:
                ping_role = await self.config.guild(guild).flagging.mod_role()
                alert_message = discord.PartialMessage(
                    channel=fc, id=message_details["alert_message"]
                )
                await fc.send(
                    f"<@&{ping_role}> need your attention on this",
                    reference=alert_message,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )

            return

        try:
            message = await self.bot.get_channel(payload.channel_id).fetch_message(
                payload.message_id
            )

        except discord.NotFound:
            return

        if not message_details:
            if not (cache := self.cooldown_cache.get(payload.guild_id)):
                self.cooldown_cache[payload.message_id] = cache = TTLCache(
                    5, await self.config.guild(guild).flagging.cooldown()
                )
                cache.update({payload.user_id: payload.message_id})

            else:
                if cache.get(payload.user_id):
                    return

            reporters = [payload.user_id]

            embed = self._create_flag_embed(
                payload.guild_id,
                payload.channel_id,
                payload.message_id,
                payload.member.id,
                message.author.id,
                message.content,
                reporters,
            )

            view = self.flagging_view

            msg = await fc.send(embed=embed, view=view)

            message_details = {
                "content": message.content,
                "author_id": message.author.id,
                "timestamp": message.created_at.isoformat(),
                "alert_message": msg.id,
                "cleared": False,
                "reporters": reporters,
                "flagged_by": payload.member.id,
            }

            await self.config.custom(
                "FLAGGED", payload.guild_id, payload.channel_id, payload.message_id
            ).set(message_details)

            await message.clear_reaction(payload.emoji)

    # <--- Commands --->

    # <--- Setting Commands --->

    @commands.group(
        name="modplusset",
        aliases=["mpset"],
        invoke_without_command=True,
    )
    @commands.has_permissions(ban_members=True)
    async def mpset(self, ctx: commands.Context):
        return await ctx.send_help()

    # <--- Flagging --->

    @mpset.group(name="flag", aliases=["fl"], invoke_without_command=True)
    async def mpset_flag(self, ctx: commands.Context):
        """
        Flags are used to mark message by normal users for review by moderators.
        """
        return await ctx.send_help()

    @mpset_flag.command(name="emoji", aliases=["em"])
    async def mpset_flag_emoji(self, ctx: commands.Context, emoji: EmojiConverter):
        """
        Set the emoji that will be used to flag messages.
        """
        if isinstance(emoji, discord.Emoji):
            emoji = str(emoji)

        await self.config.guild(ctx.guild).flagging.emoji.set(emoji)
        await ctx.send(f"Flag emoji set to {emoji}")

    @mpset_flag.command(name="channel", aliases=["ch"])
    async def mpset_flag_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Set the channel that flagged messages will be sent to.
        """
        await self.config.guild(ctx.guild).flagging.channel.set(channel.id)
        await ctx.send(f"Flagged messages will be sent to {channel.mention}")

    @mpset_flag.command(name="modrole", aliases=["mr"])
    async def mpset_flag_modrole(self, ctx: commands.Context, role: discord.Role):
        """
        Set the role that will be pinged when a message is flagged.
        """
        await self.config.guild(ctx.guild).flagging.mod_role.set(role.id)
        await ctx.send(f"{role.mention} will be pinged when a message is flagged.")

    @mpset_flag.command(name="cooldown", aliases=["cd"])
    async def mpset_flag_cooldown(self, ctx: commands.Context, cooldown: int):
        """
        Set the cooldown between flagging messages.

        Cooldown must be in seconds
        """
        await self.config.guild(ctx.guild).flagging.cooldown.set(cooldown)
        await ctx.send(f"Flagging cooldown set to {cooldown} seconds.")

    @mpset_flag.command(name="threshold", aliases=["th"])
    async def mpset_flag_threshold(self, ctx: commands.Context, threshold: int):
        """
        Set the threshold for flagging messages.

        Threshold is the number of flags a message must receive before the mod role is pinged.
        """
        await self.config.guild(ctx.guild).flagging.ping_threshold.set(threshold)
        await ctx.send(f"Flagging threshold set to {threshold}.")

    @mpset_flag.command(name="show")
    async def mpset_flag_show(self, ctx: commands.Context):
        """
        Show the current flagging settings.
        """
        flagging = await self.config.guild(ctx.guild).flagging()
        await ctx.send(
            cf.box(
                f"""
            Flagging settings for {ctx.guild.name}:
                    Flag emoji: {flagging['emoji']}
                    Flag channel: {ctx.guild.get_channel(flagging['channel'])}
                    Mod role: {ctx.guild.get_role(flagging['mod_role'])}
                    Cooldown: {flagging['cooldown']}
                    Threshold: {flagging['ping_threshold']}
                """
            )
        )

    # <--- Watchlist --->

    @mpset.group(name="watchlist", aliases=["wl"], invoke_without_command=True)
    async def mpset_wl(self, ctx: commands.Context):
        """
        Watchlists are lists of users that will be watched for certain actions.
        """
        return await ctx.send_help()

    @mpset_wl.command(name="channel", aliases=["ch"])
    async def mpset_wl_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Set the channel that watchlist notifications will be sent to.
        """
        await self.config.guild(ctx.guild).watchlist.channel.set(channel.id)
        await ctx.send(f"Watchlist notifications will be sent to {channel.mention}")

    @mpset_wl.command(name="notifyoninfraction", aliases=["noi"])
    async def mpset_wl_notifyoninfraction(self, ctx: commands.Context, toggle: bool):
        """
        Toggle whether or not to notify the watchlist when a user is added to the watchlist.
        """
        await self.config.guild(ctx.guild).watchlist.notify_on_infraction.set(toggle)
        await ctx.send(
            f"Watchlist will {'now' if toggle else 'no longer'} notify on infractions done by users on the watchlist"
        )

    @mpset_wl.command(name="notifymessage", aliases=["nm"])
    async def mpset_wl_notifymessage(
        self, ctx: commands.Context, *, message: Union[Literal["clear", "default"], None, str]
    ):
        """
        Set the message that will be sent when a user is added to the watchlist.

        Use `clear` to clear the message, `default` to set the message to the default message.

        The following variables are available:
        {server} - The server in which the moderation action was performed.
        {violator} - The user who was moderated.
        {issuer} - The user who performed the moderation action.
        {reason} - The reason for the moderation action.
        {id} - The ID of the moderation action.
        {type} - The type of moderation action.
        {duration} - The duration of the moderation action.
        """
        if message == "clear":
            await self.config.guild(ctx.guild).watchlist.infraction_message.clear()
            return await ctx.send("Watchlist notify message cleared")

        elif message == "default":
            await self.config.guild(ctx.guild).watchlist.infraction_message.clear()
            return await ctx.send("Watchlist notify message set to default")

        elif message is None:
            return await ctx.send(
                f"The current notify message is:\n```\n{await self.config.guild(ctx.guild).watchlist.infraction_message()}```"
            )

        await self.config.guild(ctx.guild).watchlist.infraction_message.set(message)
        await ctx.send("Watchlist notify message set")

    @mpset_wl.command(name="show")
    async def mpset_wl_show(self, ctx: commands.Context):
        """
        Show the current watchlist settings.
        """
        watchlist = await self.config.guild(ctx.guild).watchlist()
        await ctx.send(
            cf.box(
                f"""
            Watchlist settings for {ctx.guild.name}:
                    Watchlist channel: {ctx.guild.get_channel(watchlist['channel'])}
                    Notify on infraction: {watchlist['notify']}
                    Notify message: {watchlist['infraction_message']}
                """
            )
        )

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
        Automod actions are also counted as infraction. So do not add automod for consecutive infraction counts.
        For example, if you have automod for 3 and 4 infractions, and the user has 3 infractions, they will be actioned.
        Which will add another infraction, making it 4. Which will action them again. And so on.

        Use `clear` for the `action` argument to remove the automod for that infraction count.
        """

        async with self.config.guild(ctx.guild).automod() as automod:
            if action == "clear":
                if not automod.get(str(infraction_count)):
                    return await ctx.send("There is no automod for that infraction count.")

                del automod[str(infraction_count)]
                return await ctx.send(
                    "Removed automod for infraction count: `{}`".format(infraction_count)
                )

            if a := automod.get(str(infraction_count)):
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

            if action in ("tempban", "mute"):
                await ctx.send(
                    f"How long do you want to {action} the user for? (days, weeks, hours, minutes)"
                )
                msg = await ctx.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=60,
                )
                try:
                    time: timedelta = await timedelta_converter().convert(ctx, msg.content)
                except TimeoutError:
                    return await ctx.send("Timed Out.")
                except ValueError:
                    return await ctx.send("That is not a valid number.")
                else:
                    automod[infraction_count] = f"{action} {int(time.total_seconds())}"

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
                    if len(a := action.split()) == 1
                    else f"`{infraction_count}` - `{a[0]} for {cf.humanize_timedelta(seconds=int(a[1]))}`"
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

    @mpset_log.command(name="message")
    async def mpset_log_message(
        self,
        ctx: commands.Context,
        *,
        tagscript: Union[Literal["clear", "default"], None, str] = None,
    ):
        """
        Use tagscript to generate a message that is sent to the log channel when a moderation action is performed.

        Use `clear` to remove the embed message or don't provide a tagscript to see the current message.
        Use `default` to set the message to the default message.

        The following variables are available:
        {server} - The server in which the moderation action was performed.
        {violator} - The user who was moderated.
        {issuer} - The user who performed the moderation action.
        {reason} - The reason for the moderation action.
        {id} - The ID of the moderation action.
        {type} - The type of moderation action.
        {duration} - The duration of the moderation action.
        {dms_open} - Whether or not the violator's DMs were open and the bot was able to DM them.
        """
        if tagscript == "clear":
            await self.config.guild(ctx.guild).log_message.set("")
            return await ctx.send("Cleared the log message.")

        if tagscript == "default":
            await self.config.guild(ctx.guild).log_message.clear()
            return await ctx.send("Set the log message to the default message.")

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
            description=f"Log Channel: {getattr(ctx.guild.get_channel(log_channel), 'mention', 'N/A')}\nLog Message: ```{log_message or 'N/A'}```",
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
        self, ctx: commands.Context, *, dm: Union[Literal["clear", "default"], None, str] = None
    ):
        """
        Use tagscript to generate a message that is sent to the violator when a moderation action is performed.

        Use `clear` to remove the DM message or don't provide a tagscript to see the current message.
        Use `default` to set the message to the default message.

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
            await self.config.guild(ctx.guild).dm_message.set("")
            return await ctx.send("Cleared the DM message.")

        elif dm == "default":
            await self.config.guild(ctx.guild).dm_message.clear()
            return await ctx.send("Set the DM message to the default message.")

        elif dm is None:
            dm = await self.config.guild(ctx.guild).dm_message()
            if dm is None:
                return await ctx.send("There is no DM message set.")
            return await ctx.send(f"The current DM message is ```{dm}```")

        await self.config.guild(ctx.guild).dm_message.set(dm)
        return await ctx.send(f"Set the DM message to ```{dm}```")

    @mpset.command(name="channelmessage", aliases=["cm"])
    async def mpset_channelmessage(
        self, ctx: commands.Context, *, cm: Union[Literal["clear", "default"], None, str] = None
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

        elif cm == "default":
            await self.config.guild(ctx.guild).channel_message.clear()
            return await ctx.send("Set the channel message to the default message.")

        elif cm is None:
            cm = await self.config.guild(ctx.guild).channel_message()
            if cm is None:
                return await ctx.send("There is no channel message set.")
            return await ctx.send(f"The current channel message is ```{cm}```")

        await self.config.guild(ctx.guild).channel_message.set(cm)
        return await ctx.send(f"Set the channel message to ```{cm}```")

    # <--- Moderation Commands --->

    @commands.command(name="warn")
    @commands.has_permissions(ban_members=True)
    async def warn(
        self,
        ctx: commands.Context,
        user: discord.Member,
        until: Optional[timedelta_converter] = None,
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
        infraction = await sm.infraction(ctx, reason, until)

    @commands.command(name="mute")
    @commands.has_permissions(ban_members=True)
    async def mute(
        self,
        ctx: commands.Context,
        user: discord.Member,
        until: Optional[timedelta_converter] = None,
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
        infraction = await sm.infraction(ctx, reason, duration=until)
        await user.timeout(until, reason=reason)

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        user: discord.Member,
        until: timedelta_converter = None,
        send_invite: Optional[bool] = True,
        *,
        reason: str,
    ):
        """
        Ban a user.
        """
        if not await self._validate_action(ctx, user, "ban"):
            return await ctx.send("You cannot ban this user.")

        reason = await self._appropriate_reason(ctx.guild.id, reason)

        sm = await ServerMember.from_member(self, user)
        infraction = await sm.infraction(ctx, reason, duration=until)
        await user.ban(reason=reason)

    @commands.command(name="kick")
    @commands.has_permissions(ban_members=True)
    async def kick(self, ctx: commands.Context, user: discord.Member, *, reason: str):
        """
        Kick a user.
        """
        if not await self._validate_action(ctx, user, "kick"):
            return await ctx.send("You cannot kick this user.")

        reason = await self._appropriate_reason(ctx.guild.id, reason)

        sm = await ServerMember.from_member(self, user)
        infraction = await sm.infraction(ctx, reason)
        await user.kick(reason=reason)

    # <--- Infractions --->

    @commands.group(name="infractions", aliases=["infraction"], invoke_without_command=True)
    @commands.has_permissions(ban_members=True)
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
    @commands.has_permissions(ban_members=True)
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
            description=f"User ID: {user.id}\nUsername: {user.display_name}\nInfractions: {len(infractions)}\nCreated At: <t:{int(user.created_at.timestamp())}:R> (<t:{int(user.created_at.timestamp())}:F>))",
            color=discord.Color.red(),
        )
        if mem := ctx.guild.get_member(user.id):
            embed.add_field(name="Joined At", value=f"<t:{int(mem.joined_at.timestamp())}:R>")
            embed.add_field(name="Server Nickname", value=mem.display_name)
            embed.add_field(
                name="Watchlist",
                value=(
                    f"Being watched: {sm.is_being_watched}\n"
                    + (
                        f"Watchlist reason: {sm.watchlist_reason}\n"
                        f"Watchlist expires : <t:{int(sm.watchlist_expiry.timestamp())}:R> (<t:{int(sm.watchlist_expires_at.timestamp())}:F>)"
                        if sm.is_being_watched
                        else ""
                    )
                ),
            )

        embed.set_thumbnail(url=user.display_avatar.url)
        await ctx.send(embed=embed)

    # <--- Watchlist --->

    @commands.group(name="watchlist", invoke_without_command=True)
    @commands.has_permissions(ban_members=True)
    async def watchlist(self, ctx: commands.Context):
        """
        See the watchlist.

        Use subcommands to manage the watchlist.
        """

        guild_watchlist = await self._get_watchlist(ctx.guild.id)

        # filter out member ids that are no longer in guild
        guild_watchlist: dict[int, dict[str, Union[str, datetime, None]]] = dict(
            filter(lambda x: ctx.guild.get_member(x[0]) is not None, guild_watchlist.items())
        )

        if not guild_watchlist:
            return await ctx.send("The watchlist is empty.")

        fields = []
        for user_id, data in guild_watchlist.items():
            user = ctx.guild.get_member(user_id)
            duration = (
                f"<t:{int(data['duration'].timestamp())}:R>" if data["duration"] else "Never"
            )
            fields.append(
                dict(
                    name=f"{user.display_name} ({user.id})",
                    value=f"**Reason:** {data['reason']}\n**Expires:** {duration}",
                    inline=False,
                )
            )

        embeds = await group_embeds_by_fields(
            *fields,
            per_embed=6,
            page_in_footer=True,
            title=f"Watchlist for {ctx.guild.name}",
            description=f"Total: {len(guild_watchlist)}",
            color=discord.Color.red().value,
            thumbnail__url=getattr(ctx.guild.icon, "url", None),
        )

        await PaginationView(ctx, embeds).start()

    @watchlist.command(name="add")
    @commands.has_permissions(ban_members=True)
    async def watchlist_add(
        self,
        ctx: commands.Context,
        user: discord.Member,
        duration: Optional[timedelta_converter] = None,
        *,
        reason: str,
    ):
        """
        Add a user to the watchlist.
        """
        if duration is not None:
            duration = datetime.now(tz=timezone.utc) + duration

        await self._add_to_watchlist(ctx.guild.id, user.id, reason, duration)
        await ctx.send("User added to watchlist.")

    @watchlist.command(name="remove")
    @commands.has_permissions(ban_members=True)
    async def watchlist_remove(self, ctx: commands.Context, user: discord.Member):
        """
        Remove a user from the watchlist.
        """
        await self._remove_from_watchlist(ctx.guild.id, user.id)
        await ctx.send("User removed from watchlist.")

    @watchlist.command(name="clear")
    @commands.has_permissions(ban_members=True)
    async def watchlist_clear(self, ctx: commands.Context):
        """
        Clear the watchlist.
        """
        await self._clear_watchlist(ctx.guild.id)
        await ctx.send("Watchlist cleared.")
