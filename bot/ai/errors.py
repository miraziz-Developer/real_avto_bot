"""AI qatlami xatolari (provayderdan qat'i nazar bir xil)."""


class AIError(RuntimeError):
    """AI javob bermadi / javob noto'g'ri — chaqiruvchi oddiy (regex/bazadan) javobga o'tadi."""


class AIBudgetExceeded(AIError):
    """Kunlik xarajat chegarasi tugadi."""
