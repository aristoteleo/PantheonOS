import typing as T
from io import TextIOBase


class Tee(TextIOBase):
    def __init__(self, file1: T.TextIO, file2: T.TextIO):
        self.file1 = file1
        self.file2 = file2

    def write(self, s: str) -> int:
        ret1 = self.file1.write(s)
        self.file2.write(s)
        return ret1
