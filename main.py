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
    