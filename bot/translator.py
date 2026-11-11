"""Bot messages are in English. The Translator keeps the old interface (each
message goes through translate()) so the commands did not need changing."""


class Translator:
    def __init__(self, language: str = "en") -> None:
        pass

    def translate(self, message: str) -> str:
        return message
