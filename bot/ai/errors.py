"""AI qatlami xatolari (provayderdan qat'i nazar bir xil)."""


class AIError(RuntimeError):
    """AI javob bermadi / javob noto'g'ri — chaqiruvchi oddiy (regex/bazadan) javobga o'tadi."""


class AIBudgetExceeded(AIError):
    """Kunlik xarajat chegarasi tugadi."""


def json_object(value: object) -> dict:
    """Model JSON javobi → obyekt. Ba'zan obyekt o'rniga ro'yxat qaytaradi (masalan postda bir nechta mashina):
    birinchi obyekt olinadi, ro'yxat uzunligi «_items_count» da (chaqiruvchi buni hisobga olishi mumkin)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        items = [v for v in value if isinstance(v, dict)]
        if items:
            return {**items[0], "_items_count": len(items)}
    raise AIError("JSON obyekt kutilgan edi")
