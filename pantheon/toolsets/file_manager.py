import os
from pathlib import Path
import tempfile
import shutil
import base64
import io
from datetime import datetime

from PIL import Image


from ..toolset import ToolSet, tool
from ..utils.log import logger


class FileManagerToolSetBase(ToolSet):
    """Base class for file manager toolset.
    This class is not a toolset itself, but a base class for other file manager toolsets.
    It provides the basic file manager functionality, including:
        - list files
        - create directory
        - delete directory
        - delete file
        - move file
        - read file

    Args:
        name: The name of the toolset.
        path: The path to the directory to manage.
        black_list: The list of files to ignore.
        **kwargs: Additional keyword arguments.
    """

    def __init__(
        self,
        name: str,
        path: str | Path | None = None,
        black_list: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        if path is None:
            path = Path.cwd()
        self.path = Path(path)
        self.black_list = black_list or []

    @tool
    async def get_cwd(self) -> dict:
        """Get current working directory."""
        return {"success": True, "cwd": str(self.path)}

    @tool
    async def list_files(self, sub_dir: str | None = None) -> dict:
        """List all files in the directory."""
        if not self.path.exists():
            return {"success": False, "error": "Directory does not exist"}
        if (sub_dir is not None) and (".." in sub_dir):
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        if sub_dir is None or sub_dir == "":
            files = list(self.path.glob("*"))
        else:
            files = list(self.path.glob(f"{sub_dir}/*"))
        return {
            "success": True,
            "files": [
                {
                    "name": file.name,
                    "size": file.stat().st_size if file.is_file() else 0,
                    "type": "file" if file.is_file() else "directory",
                    "last_modified": datetime.fromtimestamp(
                        file.stat().st_mtime
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                }
                for file in files
                if file.name not in self.black_list
            ],
        }

    @tool
    async def create_directory(self, sub_dir: str):
        """Create a new directory."""
        if ".." in sub_dir:
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        new_dir = self.path / sub_dir
        new_dir.mkdir(parents=True, exist_ok=True)
        return {"success": True}

    @tool
    async def create_file(self, file_path: str, content: str | None = "") -> dict:
        """Create a new text file (optionally seeded with content)."""
        if ".." in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}

        target_path = self.path / file_path
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as handle:
                handle.write(content or "")
            return {"success": True}
        except Exception as exc:  # pragma: no cover - surfaced to caller
            logger.error(f"create_file failed for {file_path}: {exc}")
            return {"success": False, "error": str(exc)}

    @tool
    async def delete_directory(self, sub_dir: str):
        """Delete a directory and all its contents recursively."""
        if ".." in sub_dir:
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        dir_path = self.path / sub_dir
        if not dir_path.exists():
            return {"success": False, "error": "Directory does not exist"}
        if not dir_path.is_dir():
            return {"success": False, "error": "Path is not a directory"}
        shutil.rmtree(dir_path)
        return {"success": True}

    @tool
    async def delete_file(self, file_path: str):
        """Delete a file."""
        if ".." in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        path = self.path / file_path
        if not path.exists():
            return {"success": False, "error": "File does not exist"}
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return {"success": True}

    @tool
    async def move_file(self, old_path: str, new_path: str):
        """Move a file."""
        if ".." in old_path:
            return {"success": False, "error": "Old path cannot contain '..'"}
        if ".." in new_path:
            return {"success": False, "error": "New path cannot contain '..'"}
        old_path = self.path / old_path
        if not old_path.exists():
            return {"success": False, "error": "Old path does not exist"}
        new_path = self.path / new_path
        shutil.move(old_path, new_path)
        return {"success": True}


def path_to_image_url(path: str) -> str:
    img = Image.open(path)
    with io.BytesIO() as buffer:
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


class FileManagerToolSet(FileManagerToolSetBase):
    """File manager toolset.
    This class is a toolset that provides the basic file manager functionality, including:
    - list files
    - create directory
    - delete directory
    - delete file
    - move file
    - read file
    - read pdf (Convert pdf to text)
    - observe image

    Args:
        name: The name of the toolset.
        path: The path to the directory to manage.
        black_list: The list of files to ignore.
        **kwargs: Additional keyword arguments.
    """

    @tool
    async def list_file_tree(self, sub_dir: str | None = None) -> list[dict]:
        """List all files in the directory recursively."""
        if not self.path.exists():
            return {"success": False, "error": "Directory does not exist"}
        if (sub_dir is not None) and (".." in sub_dir):
            return {"success": False, "error": "Sub directory cannot contain '..'"}

        def _list_tree(path: Path) -> dict:
            """Helper function to recursively build the tree structure."""
            result = {
                "name": path.name,
                "type": "directory" if path.is_dir() else "file",
                "size": path.stat().st_size if path.is_file() else 0,
            }
            if path.is_dir():
                result["children"] = []
                for item in sorted(path.iterdir()):
                    result["children"].append(_list_tree(item))
            return result

        target_path = self.path / sub_dir if sub_dir else self.path
        if not target_path.exists():
            return {"success": False, "error": "Target directory does not exist"}

        return _list_tree(target_path)

    @tool
    async def read_file(self, file_path: str, first_n_lines: int | None = None) -> dict:
        """Read a text file.

        Args:
            file_path: The path to the file to read.
            first_n_lines: The number of lines to read from the file. If not provided, the entire file will be read.
        """
        if ".." in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        file_path = self.path / file_path
        if not file_path.exists():
            return {"success": False, "error": "File does not exist"}
        with open(file_path, "r") as f:
            if first_n_lines is not None:
                lines = f.readlines()[:first_n_lines]
                # Join lines without adding extra newlines since readlines() keeps original line endings
                content = "".join(lines).rstrip("\n")
            else:
                content = f.read()
            return {
                "success": True,
                "content": content,
                "format": file_path.suffix.lower(),
            }

    @tool
    async def write_file(self, file_path: str, content: str) -> dict:
        """Write text to a file, note this function only supports text files."""
        if ".." in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        file_path = self.path / file_path
        if not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content)
        return {"success": True}

    @tool
    async def observe_images(self, question: str, image_paths: list[str]) -> dict:
        """Observe images and answer a question about them.

        Args:
            question: The question to answer.
            image_paths: The paths to the images to view."""
        context = self.get_context()
        if context is None:
            return {"success": False, "error": "ExecutionContext not available"}

        # Build messages with question
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": question,
                    },
                ],
            }
        ]

        # Add images to the message
        for img_path in image_paths:
            ipath = self.path / img_path

            # Validate image path
            if not ipath.exists():
                return {
                    "success": False,
                    "error": f"Image file does not exist: {img_path}",
                }
            if not ipath.is_file():
                return {"success": False, "error": f"Path is not a file: {img_path}"}

            # Convert image to base64 URI and add to message
            base64_uri = path_to_image_url(str(ipath))
            messages[0]["content"].append(
                {
                    "type": "image_url",
                    "image_url": {"url": base64_uri},
                }
            )

        # Call LLM to analyze images
        try:
            response = await context.call_agent(messages=messages, use_memory=True)
            return {"success": True, "content": response}
        except Exception as e:
            logger.error(
                f"Error calling agent for image observation: {e}", exc_info=True
            )
            return {"success": False, "error": str(e)}

    @tool
    async def observe_pdf_screenshots(
        self,
        question: str,
        pdf_path: str,
        page_numbers: list[int] | None = None,
        dpi: int = 300,
    ) -> dict:
        """Observe the screenshots of the PDF file and answer a question about them.

        Args:
            question: The question to answer.
            pdf_path: The path to the PDF file to observe.
            page_numbers: The numbers of the pages to observe. If not provided, all pages will be observed.
            dpi: The DPI of the screenshots. If not provided, the default value is 300.
        """
        if ".." in pdf_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        file_path = self.path / pdf_path
        if not file_path.exists():
            return {"success": False, "error": "PDF file does not exist"}
        if not file_path.is_file():
            return {"success": False, "error": "Path is not a file"}
        if file_path.suffix.lower() != ".pdf":
            return {"success": False, "error": "File is not a PDF (wrong extension)"}
        try:
            import pymupdf
            with tempfile.TemporaryDirectory() as tmp_dir:
                image_paths = []
                with pymupdf.open(str(file_path)) as doc:
                    page_count = len(doc)
                    if page_numbers is not None:
                        page_numbers = [p for p in page_numbers if p <= page_count]
                    else:
                        page_numbers = list(range(page_count))
                    for page_number in page_numbers:
                        page = doc.load_page(page_number)
                        image = page.get_pixmap(dpi=dpi)
                        path = os.path.join(tmp_dir, f"page_{page_number}.png")
                        image.save(path)
                        image_paths.append(path)
                    resp = await self.observe_images(question, image_paths)
                    return resp
        except ImportError:
            return {
                "success": False,
                "error": "pymupdf library not installed. Install with: pip install pymupdf",
            }
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    @tool
    async def read_pdf(self, pdf_path: str) -> dict:
        """Read a PDF file and return the text inside it.

        Args:
            pdf_path: The path to the PDF file to read.

        Returns:
            dict: Success status, content, and metadata about the PDF.
        """
        if ".." in pdf_path:
            return {"success": False, "error": "File path cannot contain '..'"}

        file_path = self.path / pdf_path

        # Check if file exists
        if not file_path.exists():
            return {"success": False, "error": "PDF file does not exist"}

        # Check if it's actually a file
        if not file_path.is_file():
            return {"success": False, "error": "Path is not a file"}

        # Check if it has a PDF extension
        if file_path.suffix.lower() != ".pdf":
            return {"success": False, "error": "File is not a PDF (wrong extension)"}

        try:
            # Try to import pymupdf
            import pymupdf
        except ImportError:
            return {
                "success": False,
                "error": "pymupdf library not installed. Install with: pip install pymupdf",
            }

        try:
            # Open and read the PDF
            texts = []
            page_count = 0

            with pymupdf.open(str(file_path)) as doc:
                page_count = len(doc)

                # Check if PDF is password protected
                if doc.needs_pass:
                    return {
                        "success": False,
                        "error": "PDF is password protected and cannot be read",
                    }

                # Extract text from each page
                for page_num, page in enumerate(doc):
                    try:
                        page_text = page.get_text()
                        if page_text.strip():  # Only add non-empty pages
                            texts.append(f"--- Page {page_num + 1} ---\n{page_text}")
                    except Exception as e:
                        texts.append(
                            f"--- Page {page_num + 1} (Error reading) ---\nError: {str(e)}"
                        )

            # Combine all text
            full_text = "\n\n".join(texts)

            return {
                "success": True,
                "content": full_text,
                "format": ".pdf",
                "metadata": {
                    "total_pages": page_count,
                    "file_size": file_path.stat().st_size,
                    "pages_with_text": len(
                        [
                            t
                            for t in texts
                            if not t.startswith("--- Page") or "Error" not in t
                        ]
                    ),
                },
            }

        except pymupdf.FileDataError:
            return {"success": False, "error": "Invalid or corrupted PDF file"}
        except pymupdf.FitzError as e:
            return {"success": False, "error": f"PDF processing error: {str(e)}"}
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error reading PDF: {str(e)}",
            }

    @tool(exclude=True)
    async def fetch_image_base64(self, image_path: str) -> dict:
        """Fetch an image and return the base64 encoded image. for frontend display

        Args:
            image_path: Path to the image file (relative to workspace)

        Returns:
            Dict with success status and either data_uri or error message

        Raises:
            Returns error dict for invalid paths, unsupported formats, or file issues
        """
        # Security: Maximum image size (10MB)
        MAX_IMAGE_SIZE = 10 * 1024 * 1024

        # Security: Validate path doesn't contain directory traversal
        if ".." in image_path:
            return {"success": False, "error": "Image path cannot contain '..'"}

        try:
            i_path = self.path / image_path

            # Security: Check if path is within allowed workspace
            try:
                resolved_path = i_path.resolve()
                allowed_path = self.path.resolve()
                if not str(resolved_path).startswith(str(allowed_path)):
                    return {"success": False, "error": "Path outside allowed workspace"}
            except Exception:
                return {"success": False, "error": "Invalid path"}

            # Security: Reject symbolic links
            if resolved_path.is_symlink():
                return {"success": False, "error": "Symbolic links are not allowed"}

            # Check file existence
            if not resolved_path.exists():
                return {"success": False, "error": "Image does not exist"}

            # Check if it's a file (not directory)
            if not resolved_path.is_file():
                return {"success": False, "error": "Path is not a file"}

            # Validate format
            format = resolved_path.suffix.lower()
            if format not in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"]:
                return {
                    "success": False,
                    "error": "Image format must be jpg, jpeg, png, gif, webp, bmp, or svg",
                }

            # Check file size
            try:
                file_size = resolved_path.stat().st_size
                if file_size == 0:
                    return {"success": False, "error": "Image file is empty"}
                if file_size > MAX_IMAGE_SIZE:
                    return {
                        "success": False,
                        "error": f"Image size ({file_size / 1024 / 1024:.1f}MB) exceeds maximum ({MAX_IMAGE_SIZE / 1024 / 1024:.0f}MB)",
                    }
            except OSError as e:
                return {"success": False, "error": f"Cannot access file: {str(e)}"}

            # Encode image to base64
            try:
                with open(resolved_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
            except PermissionError:
                return {"success": False, "error": "Permission denied reading image"}
            except IOError as e:
                return {"success": False, "error": f"IO error reading image: {str(e)}"}

            # Map format to MIME type
            mime_format = format.lstrip(".")
            if mime_format == "jpg":
                mime_format = "jpeg"

            data_uri = f"data:image/{mime_format};base64,{b64}"
            return {
                "success": True,
                "image_path": image_path,
                "data_uri": data_uri,
            }

        except Exception as e:
            logger.error(f"Error fetching image base64 for {image_path}: {str(e)}")
            return {"success": False, "error": f"Unexpected error: {str(e)}"}


__all__ = ["FileManagerToolSet"]
