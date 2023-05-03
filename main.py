import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Literal
from .models import ServerMember, Infraction
from datetime import datetime, timedelta, timezone
from .views import YesOrNoView

MEMBER_DEFAULTS = {
    "infractions": []
}

GUILD_DEFAULTS = {
    "reason_sh": {},
    "automod": {},
    "log_channel": None,
    "appeal_server": None,
    # "log_embed": None,
    # "dm_embed": None,
    # "channel_embed": None,
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
        
    async def _get_infraction_count(self, guild_id: int, user_id: int):
        ...
        
    async def _get_infraction(self, guild_id: int, user_id: int, infraction_id: int):
        ...
        
    async def _get_infractions(self, guild_id: int, user_id: int):
        ...
        
    async def _add_infraction(self, infraction: Infraction):
        ...
        
    async def _remove_infraction(self, infraction: Infraction):
        ...
        
    async def _modify_infraction(self, original: Infraction, new: Infraction):
        ...
        
    async def _warn_infraction_count(self, guild_id: int, user_id: int):
        ...
        
    # <--- Commands --->
    
    # <--- Setting Commands --->
    
    @commands.group(name="modplusset", aliases=["mpset"], )
    async def mpset(self, ctx: commands.Context):
        return await ctx.send_help()
    
    # <--- Reason Shorthands --->
    
    @mpset.group(name="reasonshorthands", aliases=["reasonsh", "rsh"])
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
                return await ctx.send("That shorthand already exists: `{}` - `{}`".format(shorthand, reason_sh[shorthand]))
            
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
            description="\n".join([f"`{shorthand}` - `{reason}`" for shorthand, reason in reason_sh.items()]),
            color=await ctx.bot.get_embed_color(ctx.channel),
        )
        
        await ctx.send(embed=embed)
        
    # <--- Automod --->
        
    @mpset.group(name="automod")
    async def mpset_automod(self, ctx: commands.Context, infraction_count: int, *, action: Literal["ban", "kick", "mute", "warn", "tempban", "clear"]):
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
                return await ctx.send("Removed automod for infraction count: `{}`".format(infraction_count))
            
            if a:=automod.get(infraction_count):
                view = YesOrNoView(ctx, "", "Alright, it will remain the same.")
                if isinstance(a, int):
                    await ctx.send(f"That infraction count is already set to `tempban` the user for {a}. Do you want to change it to `{action}`?", view=view)
                else:
                    await ctx.send(f"That infraction count is already set to `{a}` the user. Do you want to change it to `{action}`?", view=view)
            
                await view.wait()
                
                if not view.value:
                    return
                
            if action == "tempban":
                await ctx.send("How long do you want to tempban the user for? (days, weeks, hours)")
                msg = await ctx.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60)
                try:
                    time: timedelta = await commands.get_timedelta_converter(allowed_units=["hours", "days", "weeks"]).convert(ctx, msg.content)
                except TimeoutError:
                    return await ctx.send("Timed Out.")
                except ValueError:
                    return await ctx.send("That is not a valid number.")
                else:
                    automod[infraction_count] = int(time.total_seconds())
                
            else:
                automod[infraction_count] = action
                
            await ctx.send(f"Alright, I will {action} users with more than {infraction_count} infractions.")
            
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
            description="\n".join([f"`{infraction_count}` - `{action}`" for infraction_count, action in automod.items()]),
            color=await ctx.bot.get_embed_color(ctx.channel),
        )
        
        await ctx.send(embed=embed)