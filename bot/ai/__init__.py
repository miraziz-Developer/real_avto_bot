"""AI provayder qatlami. Hozir Groq; boshqa provayderga o'tish faqat shu paketni o'zgartiradi."""

from bot.ai.groq import AIError, GroqClient, get_ai

__all__ = ["AIError", "GroqClient", "get_ai"]
