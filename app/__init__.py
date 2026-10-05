"""Source-driven Rooms Avatar reference bot."""

from ._bootstrap import activate as _activate_vendor

__version__ = "2.3.0"

# Зависимости из vendor/ подключаются при первом же импорте пакета app — до fastapi и yaml.
_activate_vendor()
