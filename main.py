import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from .models import ServerMember, Infraction

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
    
    @commands.group(name="modplusset", aliases=["mpset"], )
    async def mpset(self, ctx: commands.Context):
        return await ctx.send_help()
    
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
        