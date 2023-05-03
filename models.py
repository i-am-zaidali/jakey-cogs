from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass

@dataclass
class ServerMember:
    guild_id: int
    user_id: int
    infractions: list["Infraction"]
    
    @property
    def json(self):
        return {
            "infractions": [infraction.json for infraction in self.infractions]
        }
    
@dataclass
class Infraction:
    def __init__(self) -> None:
        self.reason: str
        self.at: datetime
        self.duration: Optional[timedelta]
        self.violator: ServerMember
        self.issuer_id: int
        
    @property
    def lasts_until(self):
        return self.at + self.duration if self.duration else None
    
    @property
    def expired(self):
        return self.lasts_until and self.lasts_until < datetime.now(timezone.utc)
    
    @property
    def json(self):
        return {
            "reason": self.reason,
            "at": self.at.isoformat(),
            "duration": self.duration.total_seconds() if self.duration else None,
            "issuer_id": self.issuer_id
        }