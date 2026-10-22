class TextParser:
    def parse(self, data: bytes, mime: str) -> str:
        if not mime.startswith("text/") and mime not in ("application/json", "application/markdown"):
            raise ValueError(f"unsupported mime type {mime!r}; only text is supported in v1")
        return data.decode("utf-8", errors="replace")
