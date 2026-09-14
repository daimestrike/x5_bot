from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Button:
    label: str
    data: str  # callback data, например "card:A-07"


@dataclass
class Message:
    text: str
    buttons: List[List[Button]] = field(default_factory=list)  # ряды кнопок

    def flat_buttons(self) -> List[Button]:
        return [b for row in self.buttons for b in row]


@dataclass
class Reply:
    messages: List[Message] = field(default_factory=list)
    screen: str = ""  # идентификатор последнего экрана (S0..S8) — для тестов и журнала


@dataclass
class Incoming:
    """Входящее событие от пользователя, независимое от мессенджера."""
    user_id: str
    text: Optional[str] = None
    callback: Optional[str] = None       # данные нажатой кнопки
    attachment_type: Optional[str] = None  # voice / photo / file / video / sticker ...
