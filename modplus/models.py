from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING, Literal
from dataclasses import dataclass
from enum import Enum
import discord
import hashlib
from redbot.core import commands

if TYPE_CHECKING:
    from .main import ModPlus as InfractionsCog


class InfractionType(Enum):
    BAN = "ban"
    KICK = "kick"
    MUTE = "mute"
    WARN = "warn"
    TEMPBAN = "tempban"

    @property
    def is_temporary(self):
        return self in (InfractionType.MUTE, InfractionType.TEMPBAN)


@dataclass
class ServerMember:
    guild_id: int
    user_id: int
    infractions: list["Infraction"]
    watchlist: dict[str, str] | None

    @property
    def is_being_watched(self):
        return self.watchlist is not None

    @property
    def watchlist_reason(self):
        return self.watchlist["reason"] if self.is_being_watched else None

    @property
    def watchlist_expiry(self):
        return (
            datetime.fromisoformat(self.watchlist["duration"]) if self.is_being_watched else None
        )

    @property
    def json(self):
        return {"infractions": [infraction.json for infraction in self.infractions]}

    @classmethod
    async def from_member(cls, cog: "InfractionsCog", member: discord.Member):
        return await cls.from_ids(cog, member.guild.id, member.id)

    @classmethod
    async def from_ids(cls, cog: "InfractionsCog", guild_id: int, user_id: int):
        self = cls(
            guild_id=guild_id,
            user_id=user_id,
            infractions=[],
            watchlist=await cog._get_watchlist_status(guild_id, user_id),
        )

        self.infractions = list(
            map(
                lambda infraction: (infraction, setattr(infraction.violator, self))[0],
                await cog._get_infractions(guild_id, user_id),
            )
        )
        return self

    async def infraction(
        self,
        ctx: commands.Context,
        reason: str,
        duration: Optional[timedelta] = None,
    ):
        action: Literal["warn", "mute", "kick", "ban"] = ctx.command.qualified_name
        reason = reason or "No reason provided."
        issuer_id = ctx.author.id

        if action == "ban" and duration:
            action = "tempban"

        infraction = Infraction(
            type=InfractionType.__members__[action.upper()],
            reason=reason,
            at=datetime.now(timezone.utc),
            duration=duration,
            violator=self,
            issuer_id=issuer_id,
        )

        self.infractions.append(infraction)

        await ctx.bot.dispatch("modplus_infraction", ctx, self, infraction)
        # async def on_modplus_infraction(self, ctx: commands.Context, member: ServerMember, infraction: Infractions):

        return infraction

    async def delete_infraction(self, cog: "InfractionsCog", infraction: "Infraction"):
        await cog._remove_infraction(infraction)
        self.infractions.remove(infraction)

    async def clear_infractions(self, cog: "InfractionsCog"):
        await cog._clear_infractions(self)
        self.infractions.clear()


@dataclass
class Infraction:
    def __init__(
        self,
        type: InfractionType,
        reason: str,
        at: datetime,
        duration: Optional[timedelta],
        violator: ServerMember,
        issuer_id: int,
        *,
        id: Optional[str] = None,
    ):
        self.type: InfractionType = type
        self.reason: str = reason
        self.at: datetime = at
        self.duration: Optional[timedelta] = duration
        self.violator: ServerMember = violator
        self.issuer_id: int = issuer_id
        self.id = id or self._generate_id()

    def _generate_id(self):
        timestamp = str(self.at.timestamp()).encode("utf-8")
        hash_object = hashlib.sha256(timestamp)
        hex_dig = hash_object.hexdigest()
        short_hash = hex_dig[:8]
        return short_hash

    @property
    def lasts_until(self):
        return self.at + self.duration if self.duration else None

    @property
    def expired(self):
        return self.lasts_until and self.lasts_until < datetime.now(timezone.utc)

    @property
    def json(self):
        return {
            "id": self.id,
            "type": self.type.value,
            "reason": self.reason,
            "at": self.at.isoformat(),
            "duration": self.duration.total_seconds() if self.duration else None,
            "issuer_id": self.issuer_id,
        }

    @classmethod
    def from_json(cls, json: dict, violator: ServerMember):
        return cls(
            type=InfractionType.__members__[json["type"].upper()],
            reason=json["reason"],
            at=datetime.fromisoformat(json["at"]),
            duration=timedelta(seconds=json["duration"]) if json["duration"] else None,
            issuer_id=json["issuer_id"],
            violator=violator,
            id=json["id"],
        )
