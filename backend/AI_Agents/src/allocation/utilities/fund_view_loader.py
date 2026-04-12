from pathlib import Path


class FundViewLoader:
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def load(self) -> str:
        text = self.file_path.read_text()
        if not text.strip():
            raise ValueError("Fund view file is empty")
        return text.strip()
