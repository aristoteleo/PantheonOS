import os
import typing as T
from pathlib import Path
from functools import update_wrapper


class ChDir(object):
    def __init__(self, func: T.Callable, target_dir: Path):
        update_wrapper(func, self)
        self.func = func
        self.target_dir = target_dir

    def __call__(self, *args, **kwargs) -> T.Any:
        old = os.getcwd()
        os.chdir(self.target_dir)
        res = self.func(*args, **kwargs)
        os.chdir(old)
        return res
