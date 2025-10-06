"""ATAC-seq toolset CLI entry point"""

import fire
from .core import ATACSeqToolSet

def main():
    """CLI entry point for ATAC-seq toolset"""
    fire.Fire(ATACSeqToolSet)

if __name__ == "__main__":
    main()