"""Source-driven Rooms Avatar reference bot."""

from ._bootstrap import activate as _activate_vendor

__version__ = "1.3.2"  # совпадает с номером релиза

# Зависимости из vendor/ подключаются при первом же импорте пакета app — до fastapi и yaml.
_activate_vendor()
