import fire
from ..rag.workflow_build_db import build_all


if __name__ == "__main__":
    fire.Fire(build_all)
