from .build import build_all, upload_to_huggingface, download_from_huggingface


if __name__ == "__main__":
    import fire
    fire.Fire({
        'build': build_all,
        'upload': upload_to_huggingface,
        'download': download_from_huggingface,
    })
