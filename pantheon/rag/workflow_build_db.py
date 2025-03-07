from pathlib import Path
import hashlib
import yaml

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy

from ..utils.log import logger


async def download_docs(
    root_url: str,
    output_dir: str,
    max_depth: int = 1,
    include_external: bool = False,
):
    config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=max_depth,
            include_external=include_external,
        ),
        scraping_strategy=LXMLWebScrapingStrategy(),
        verbose=True,
    )
    output_dir = Path(output_dir)
    if not output_dir.exists():
        output_dir.mkdir(parents=True)

    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun(root_url, config=config)

        logger.info(f"Crawled {len(results)} pages in total")

        logger.info("Saving results to files...")
        for result in results:
            logger.info(f"URL: {result.url}")
            logger.info(f"Depth: {result.metadata.get('depth', 0)}")
            file_name = result.url.split("/")[-1].split("#")[0] + ".md"
            file_path = output_dir / file_name
            logger.info(f"Saving to {file_path}")
            with open(file_path, "w", encoding="utf-8") as f:
                try:
                    f.write(result.markdown.raw_markdown)
                except Exception as e:
                    logger.error(e)


def remove_duplicates(input_dir: str):
    # remove duplicates by text hash
    hashes = set()
    for file in Path(input_dir).glob("*.md"):
        with open(file, "r", encoding="utf-8") as f:
            text = f.read()
            hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if hash in hashes:
                file.unlink()
            else:
                hashes.add(hash)


def remove_prefix(text: str, spliter="#"):
    prefix = text.split(spliter)[0]
    return text.replace(prefix, "")


def remove_prefix_from_files(dir: str, spliter="# "):
    for file in Path(dir).glob("*.md"):
        with open(file, "r", encoding="utf-8") as f:
            text = f.read()
            text = remove_prefix(text, spliter)
            with open(file, "w", encoding="utf-8") as f:
                f.write(text)


async def process_item(item: dict, output_dir: str | Path):
    dir = output_dir
    if item["type"] == "package documentation":
        if not dir.exists():
            await download_docs(item["url"], dir)
            remove_duplicates(dir)
            remove_prefix_from_files(dir)
    else:
        logger.error(f"Unknown item type: {item['type']}")


async def build_vector_db(name: str, db_item: dict, output_dir: str):
    from .vectordb import VectorDB
    root_dir = Path(output_dir) / name
    with open(root_dir / "metadata.yaml", "w", encoding="utf-8") as f:
        yaml.dump(db_item, f)
    db = VectorDB(root_dir)
    for name, item in db_item["items"].items():
        docs_dir = root_dir / "raw" / name
        logger.info(f"Processing item {name}")
        await process_item(item, docs_dir)
        for file in docs_dir.glob("*.md"):
            logger.info(f"Inserting {file} from {name} into database")
            await db.insert_from_file(file, {"source": name})


async def build_all(yaml_path: str, output_dir: str):
    with open(yaml_path, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f)

        for db_name in yaml_data:
            type_ = yaml_data[db_name]["type"]
            logger.info(f"Building {db_name} database")
            if type_ == "vector_db":
                await build_vector_db(db_name, yaml_data[db_name], output_dir)
            else:
                logger.error(f"Unsupported database type: {type_}")

    logger.info("Done")
