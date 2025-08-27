"""Hi-C toolset CLI entry point"""

import fire
from . import HiCToolSet

def main():
    """CLI entry point for Hi-C toolset"""
    fire.Fire(HiCToolSet)

if __name__ == "__main__":
    main()